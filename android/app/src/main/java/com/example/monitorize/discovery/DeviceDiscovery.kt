package com.example.monitorize.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.util.Log
import androidx.compose.runtime.mutableStateListOf
import com.example.monitorize.DEFAULT_STREAM_FPS
import com.example.monitorize.MAX_STREAM_FPS
import com.example.monitorize.MIN_STREAM_FPS
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ChannelResult

data class DiscoveredDevice(
    val name: String,
    val ip: String,
    val port: Int,
    val fps: Int = DEFAULT_STREAM_FPS,
    val isUsb: Boolean = false,
    val encrypted: Boolean = false,
    val fingerprint: String? = null,
    val inputTransport: String? = null,
    val serviceName: String = ""
)

class DeviceDiscovery(private val context: Context) {
    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private var multicastLock: WifiManager.MulticastLock? = null
    
    private val TAG = "DeviceDiscovery"
    private val SERVICE_TYPE = "_monitorize._tcp."
    private val DEFAULT_PORT = 7110
    private val RESOLVE_QUEUE_CAPACITY = 64

    val devices = mutableStateListOf<DiscoveredDevice>()
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var resolverJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private var resolveChannel: Channel<NsdServiceInfo>? = null
    var isDiscovering = false
        private set
    private var harvestJob: Job? = null
    private var generation = 0
    private val pendingResolveLock = Any()
    private val pendingResolveNames = mutableSetOf<String>()

    fun startDiscovery() {
        Log.d(TAG, "startDiscovery() called")
        stopDiscovery()
        val currentGeneration = ++generation
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
        addDevice(
            DiscoveredDevice(usbName, "127.0.0.1", DEFAULT_PORT, isUsb = true),
            currentGeneration
        )

        
        val channel = Channel<NsdServiceInfo>(RESOLVE_QUEUE_CAPACITY)
        resolveChannel = channel
        startResolverJob(channel, currentGeneration)

        
        harvestJob = scope.launch {
            delay(500)
            if (isDiscovering && generation == currentGeneration) {
                startNsdDiscovery(SERVICE_TYPE, currentGeneration)
            }
        }
    }

    private fun startResolverJob(
        channel: Channel<NsdServiceInfo>,
        currentGeneration: Int
    ) {
        resolverJob = scope.launch {
            for (si in channel) {
                if (!isActive || generation != currentGeneration) break
                try {
                    val completer = CompletableDeferred<Unit>()
                    Log.d(TAG, "Resolving: ${si.serviceName} (${si.serviceType})")
                    
                    nsdManager.resolveService(si, object : NsdManager.ResolveListener {
                        override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                            Log.e(TAG, "Resolve fail: $errorCode")
                            completer.complete(Unit)
                        }
                        override fun onServiceResolved(resolved: NsdServiceInfo) {
                            if (!isDiscovering || generation != currentGeneration) {
                                completer.complete(Unit)
                                return
                            }
                            val ip = resolved.host?.hostAddress ?: ""
                            if (ip.isNotEmpty() && !ip.contains(":")) {
                                var resolvedName = resolved.serviceName.replace(Regex("\\[.*\\]"), "").trim()
                                
                                val hostName = resolved.host?.hostName?.removeSuffix(".local")
                                if (hostName != null && !hostName.contains(".") && hostName != ip) {
                                    resolvedName = hostName
                                }
                                
                                
                                var encrypted = false
                                var fingerprint: String? = null
                                var inputTransport: String? = null
                                var fps = DEFAULT_STREAM_FPS
                                try {
                                    resolved.attributes?.let { attrs ->
                                        if (attrs.containsKey("fn")) resolvedName = String(attrs["fn"]!!)
                                        else if (attrs.containsKey("model")) resolvedName = String(attrs["model"]!!)
                                        else if (attrs.containsKey("name")) resolvedName = String(attrs["name"]!!)
                                        encrypted = attrs["encrypted"]?.let { String(it) == "1" } == true
                                        fingerprint = attrs["fingerprint"]?.let { String(it) }
                                        inputTransport = attrs["input_transport"]?.let { String(it) }
                                        fps = attrs["fps"]?.let { parseFps(String(it)) } ?: DEFAULT_STREAM_FPS
                                    }
                                } catch (_: Exception) {}

                                addDevice(DiscoveredDevice(
                                    name = if (resolvedName.isEmpty()) "WiFi Device" else resolvedName,
                                    ip = ip,
                                    port = resolved.port,
                                    fps = fps,
                                    encrypted = encrypted,
                                    fingerprint = fingerprint,
                                    inputTransport = inputTransport,
                                    serviceName = resolved.serviceName
                                ), currentGeneration)
                            }
                            completer.complete(Unit)
                        }
                    })
                    
                    withTimeoutOrNull(5000) { completer.await() }
                    delay(200)
                } catch (e: Exception) {
                    if (e is CancellationException) throw e
                    Log.e(TAG, "Resolver job error", e)
                } finally {
                    synchronized(pendingResolveLock) {
                        pendingResolveNames.remove(si.serviceName)
                    }
                }
            }
        }
    }

    private fun startNsdDiscovery(type: String, currentGeneration: Int) {
        val listener = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(t: String?, errorCode: Int) {
                Log.e(TAG, "NSD Start failed $t: $errorCode")
                failDiscovery(currentGeneration)
            }
            override fun onStopDiscoveryFailed(t: String?, errorCode: Int) {}
            override fun onDiscoveryStarted(t: String?) {}
            override fun onDiscoveryStopped(t: String?) {}
            override fun onServiceFound(si: NsdServiceInfo) {
                if (generation == currentGeneration) {
                    enqueueResolve(si, currentGeneration)
                }
            }
            override fun onServiceLost(si: NsdServiceInfo) {
                if (generation != currentGeneration) return
                scope.launch(Dispatchers.Main) {
                    if (generation == currentGeneration) {
                        devices.removeAll {
                            !it.isUsb && it.serviceName == si.serviceName
                        }
                    }
                }
            }
        }
        discoveryListener = listener
        try {
            nsdManager.discoverServices(type, NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (e: Exception) {
            discoveryListener = null
            Log.e(TAG, "Discovery launch error for $type", e)
            failDiscovery(currentGeneration)
        }
    }

    private fun enqueueResolve(si: NsdServiceInfo, currentGeneration: Int) {
        val channel = resolveChannel ?: return
        val serviceName = si.serviceName
        synchronized(pendingResolveLock) {
            if (!pendingResolveNames.add(serviceName)) return
        }
        val result: ChannelResult<Unit> = channel.trySend(si)
        if (result.isFailure) {
            synchronized(pendingResolveLock) {
                pendingResolveNames.remove(serviceName)
            }
            Log.w(TAG, "Resolve queue full; dropping $serviceName")
        }
        if (generation != currentGeneration) {
            synchronized(pendingResolveLock) {
                pendingResolveNames.remove(serviceName)
            }
        }
    }

    private fun failDiscovery(currentGeneration: Int) {
        if (generation != currentGeneration) return
        generation += 1
        isDiscovering = false
        harvestJob?.cancel()
        harvestJob = null
        resolverJob?.cancel()
        resolverJob = null
        resolveChannel?.close()
        resolveChannel = null
        discoveryListener = null
        synchronized(pendingResolveLock) {
            pendingResolveNames.clear()
        }
        scope.launch(Dispatchers.Main) {
            devices.removeAll { !it.isUsb }
        }
        try { if (multicastLock?.isHeld == true) multicastLock?.release() } catch (_: Exception) {}
        multicastLock = null
    }

    private fun addDevice(newDevice: DiscoveredDevice, currentGeneration: Int) {
        if (!isDiscovering || generation != currentGeneration) return
        if (newDevice.ip == "127.0.0.1" && !newDevice.isUsb) return
        
        scope.launch(Dispatchers.Main) {
            if (!isDiscovering || generation != currentGeneration) return@launch
            val index = devices.indexOfFirst { it.ip == newDevice.ip }
            if (index != -1) {
                val existing = devices[index]
                
                
                val isExistingGeneric = isGenericName(existing.name)
                val isNewGeneric = isGenericName(newDevice.name)
                
                val betterName = if (isExistingGeneric && !isNewGeneric) newDevice.name else existing.name
                devices[index] = existing.copy(
                    name = betterName,
                    port = newDevice.port,
                    encrypted = newDevice.encrypted,
                    fingerprint = newDevice.fingerprint,
                    inputTransport = newDevice.inputTransport,
                    fps = newDevice.fps,
                    serviceName = newDevice.serviceName,
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

    private fun parseFps(value: String): Int {
        return value.toIntOrNull()?.coerceIn(MIN_STREAM_FPS, MAX_STREAM_FPS) ?: DEFAULT_STREAM_FPS
    }

    fun stopDiscovery() {
        Log.d(TAG, "stopDiscovery() called")
        generation += 1
        isDiscovering = false
        harvestJob?.cancel()
        harvestJob = null
        discoveryListener?.let {
            try { nsdManager.stopServiceDiscovery(it) } catch (_: Exception) {}
        }
        discoveryListener = null
        resolverJob?.cancel()
        resolveChannel?.close()
        resolveChannel = null
        synchronized(pendingResolveLock) {
            pendingResolveNames.clear()
        }
        devices.clear()
        try { if (multicastLock?.isHeld == true) multicastLock?.release() } catch (_: Exception) {}
        multicastLock = null
    }
}
