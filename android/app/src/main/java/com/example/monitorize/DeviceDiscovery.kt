package com.example.monitorize

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.util.Log
import androidx.compose.runtime.mutableStateListOf
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel

data class DiscoveredDevice(
    val name: String,
    val ip: String,
    val port: Int,
    val isUsb: Boolean = false,
    val encrypted: Boolean = false,
    val fingerprint: String? = null
)

class DeviceDiscovery(private val context: Context) {
    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private var multicastLock: WifiManager.MulticastLock? = null
    
    private val TAG = "DeviceDiscovery"
    private val SERVICE_TYPE = "_monitorize._tcp."
    private val DEFAULT_PORT = 7110

    val devices = mutableStateListOf<DiscoveredDevice>()
    private val discoveryListeners = mutableListOf<NsdManager.DiscoveryListener>()
    private var resolverJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private var resolveChannel: Channel<NsdServiceInfo>? = null
    var isDiscovering = false
        private set
    private var harvestJob: Job? = null

    fun startDiscovery() {
        Log.d(TAG, "startDiscovery() called")
        stopDiscovery()
        isDiscovering = true
        
        
        try {
            multicastLock = wifiManager.createMulticastLock("monitorize_discovery").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Multicast lock error", e)
        }

        
        var usbName = "Local PC (USB)"
        try {
            val process = Runtime.getRuntime().exec("getprop debug.monitorize.pc_name")
            val reader = java.io.BufferedReader(java.io.InputStreamReader(process.inputStream))
            val propName = reader.readLine()
            if (!propName.isNullOrBlank()) {
                usbName = "$propName (USB)"
            }
        } catch (_: Exception) {}
        addDevice(DiscoveredDevice(usbName, "127.0.0.1", DEFAULT_PORT, isUsb = true))

        
        val channel = Channel<NsdServiceInfo>(Channel.UNLIMITED)
        resolveChannel = channel
        startResolverJob(channel)

        
        harvestJob = scope.launch {
            delay(500)
            if (isDiscovering) startNsdDiscovery(SERVICE_TYPE)
        }
    }

    private fun startResolverJob(channel: Channel<NsdServiceInfo>) {
        resolverJob = scope.launch {
            for (si in channel) {
                if (!isActive) break
                try {
                    val completer = CompletableDeferred<Unit>()
                    Log.d(TAG, "Resolving: ${si.serviceName} (${si.serviceType})")
                    
                    nsdManager.resolveService(si, object : NsdManager.ResolveListener {
                        override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                            Log.e(TAG, "Resolve fail: $errorCode")
                            completer.complete(Unit)
                        }
                        override fun onServiceResolved(resolved: NsdServiceInfo) {
                            val ip = resolved.host?.hostAddress ?: ""
                            if (ip.isNotEmpty() && !ip.contains(":")) {
                                var resolvedName = resolved.serviceName.replace(Regex("\\[.*\\]"), "").trim()
                                
                                val hostName = resolved.host?.hostName?.removeSuffix(".local")
                                if (hostName != null && !hostName.contains(".") && hostName != ip) {
                                    resolvedName = hostName
                                }
                                
                                
                                var encrypted = false
                                var fingerprint: String? = null
                                try {
                                    resolved.attributes?.let { attrs ->
                                        if (attrs.containsKey("fn")) resolvedName = String(attrs["fn"]!!)
                                        else if (attrs.containsKey("model")) resolvedName = String(attrs["model"]!!)
                                        else if (attrs.containsKey("name")) resolvedName = String(attrs["name"]!!)
                                        encrypted = attrs["encrypted"]?.let { String(it) == "1" } == true
                                        fingerprint = attrs["fingerprint"]?.let { String(it) }
                                    }
                                } catch (_: Exception) {}

                                addDevice(DiscoveredDevice(
                                    name = if (resolvedName.isEmpty()) "WiFi Device" else resolvedName,
                                    ip = ip,
                                    port = resolved.port,
                                    encrypted = encrypted,
                                    fingerprint = fingerprint
                                ))
                            }
                            completer.complete(Unit)
                        }
                    })
                    
                    withTimeoutOrNull(5000) { completer.await() }
                    delay(200)
                } catch (e: Exception) {
                    if (e is CancellationException) throw e
                    Log.e(TAG, "Resolver job error", e)
                }
            }
        }
    }

    private fun startNsdDiscovery(type: String) {
        val listener = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(t: String?, errorCode: Int) { Log.e(TAG, "NSD Start failed $t: $errorCode") }
            override fun onStopDiscoveryFailed(t: String?, errorCode: Int) {}
            override fun onDiscoveryStarted(t: String?) {}
            override fun onDiscoveryStopped(t: String?) {}
            override fun onServiceFound(si: NsdServiceInfo) {
                scope.launch { resolveChannel?.send(si) }
            }
            override fun onServiceLost(si: NsdServiceInfo) {}
        }
        discoveryListeners.add(listener)
        try {
            nsdManager.discoverServices(type, NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (e: Exception) {
            Log.e(TAG, "Discovery launch error for $type", e)
        }
    }

    private fun addDevice(newDevice: DiscoveredDevice) {
        if (!isDiscovering) return
        if (newDevice.ip == "127.0.0.1" && !newDevice.isUsb) return
        
        scope.launch(Dispatchers.Main) {
            if (!isDiscovering) return@launch
            val index = devices.indexOfFirst { it.ip == newDevice.ip }
            if (index != -1) {
                val existing = devices[index]
                
                
                val isExistingGeneric = isGenericName(existing.name)
                val isNewGeneric = isGenericName(newDevice.name)
                
                val betterName = if (isExistingGeneric && !isNewGeneric) newDevice.name else existing.name
                devices[index] = existing.copy(
                    name = betterName,
                    encrypted = existing.encrypted || newDevice.encrypted,
                    fingerprint = newDevice.fingerprint ?: existing.fingerprint,
                )
            } else {
                devices.add(if (devices.firstOrNull()?.isUsb == true) 1 else 0, newDevice)
            }
        }
    }

    private fun isGenericName(name: String): Boolean {
        val n = name.lowercase()
        return n == "wifi device" || n == "network device" || n == "monitorize device" || n.isEmpty()
    }

    fun stopDiscovery() {
        Log.d(TAG, "stopDiscovery() called")
        isDiscovering = false
        harvestJob?.cancel()
        harvestJob = null
        discoveryListeners.forEach { try { nsdManager.stopServiceDiscovery(it) } catch (_: Exception) {} }
        discoveryListeners.clear()
        resolverJob?.cancel()
        resolveChannel?.close()
        resolveChannel = null
        devices.clear()
        try { if (multicastLock?.isHeld == true) multicastLock?.release() } catch (_: Exception) {}
        multicastLock = null
    }
}
