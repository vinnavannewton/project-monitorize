package com.example.monitorize

import android.content.Context
import android.net.ConnectivityManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.util.Log
import androidx.compose.runtime.mutableStateListOf
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import java.net.*
import java.util.*

data class DiscoveredDevice(
    val name: String,
    val ip: String,
    val port: Int,
    val isUsb: Boolean = false,
    val isMonitorizeService: Boolean = false
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
    private var scanJob: Job? = null
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
        } catch (e: Exception) {}
        addDevice(DiscoveredDevice(usbName, "127.0.0.1", DEFAULT_PORT, isUsb = true, isMonitorizeService = true))

        
        val channel = Channel<NsdServiceInfo>(Channel.UNLIMITED)
        resolveChannel = channel
        startResolverJob(channel)

        
        val harvestTypes = listOf(SERVICE_TYPE)
        
        harvestJob = scope.launch {
            harvestTypes.forEachIndexed { index, type ->
                launch {
                    delay(500L + index * 150L) 
                    if (isDiscovering) {
                        startNsdDiscovery(type)
                    }
                }
            }
        }

        
        startSubnetScan()
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
                                
                                
                                try {
                                    resolved.attributes?.let { attrs ->
                                        if (attrs.containsKey("fn")) resolvedName = String(attrs["fn"]!!)
                                        else if (attrs.containsKey("model")) resolvedName = String(attrs["model"]!!)
                                        else if (attrs.containsKey("name")) resolvedName = String(attrs["name"]!!)
                                    }
                                } catch (e: Exception) {}

                                val isOurService = resolved.serviceType.contains("monitorize") || resolved.port == DEFAULT_PORT
                                addDevice(DiscoveredDevice(
                                    name = if (resolvedName.isEmpty()) "WiFi Device" else resolvedName,
                                    ip = ip,
                                    port = if (isOurService) resolved.port else DEFAULT_PORT,
                                    isMonitorizeService = isOurService
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

    private fun startSubnetScan() {
        scanJob = scope.launch {
            val localIp = getLocalIpAddress()
            if (localIp == null) {
                Log.e(TAG, "No local IP found for subnet scan")
                return@launch
            }
            
            val prefix = localIp.substringBeforeLast(".")
            Log.d(TAG, "Starting subnet scan on $prefix.0/24")

            
            (1..254).chunked(32).forEach { chunk ->
                if (!isActive) return@launch
                chunk.map { i ->
                    async {
                        val targetIp = "$prefix.$i"
                        if (targetIp == localIp) return@async
                        
                        
                        
                        val ports = listOf(DEFAULT_PORT, 1714, 22, 80, 53)
                        var foundAlive = false
                        var isOurPort = false
                        
                        val checks = ports.map { port ->
                            async { port to isPortOpen(targetIp, port, 300) }
                        }.awaitAll()
                        
                        for ((port, open) in checks) {
                            if (open) {
                                foundAlive = true
                                if (port == DEFAULT_PORT) isOurPort = true
                                break
                            }
                        }

                        if (foundAlive) {
                            
                            addDevice(DiscoveredDevice("WiFi Device", targetIp, DEFAULT_PORT, isMonitorizeService = isOurPort))
                            
                            
                            launch {
                                try {
                                    val inet = InetAddress.getByName(targetIp)
                                    val name = inet.hostName
                                    if (name != null && name != targetIp) {
                                        addDevice(DiscoveredDevice(name, targetIp, DEFAULT_PORT, isMonitorizeService = isOurPort))
                                    }
                                } catch (e: Exception) {}
                            }
                        }
                    }
                }.awaitAll()
                delay(100)
            }
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
                val betterService = existing.isMonitorizeService || newDevice.isMonitorizeService
                
                if (betterName != existing.name || betterService != existing.isMonitorizeService) {
                    devices[index] = existing.copy(name = betterName, isMonitorizeService = betterService)
                }
            } else {
                
                if (newDevice.isMonitorizeService) {
                    val pos = if (devices.isNotEmpty() && devices[0].isUsb) 1 else 0
                    if (pos <= devices.size) devices.add(pos, newDevice) else devices.add(newDevice)
                } else {
                    devices.add(newDevice)
                }
            }
        }
    }

    private fun isGenericName(name: String): Boolean {
        val n = name.lowercase()
        return n == "wifi device" || n == "network device" || n == "monitorize device" || n.isEmpty()
    }

    private fun isPortOpen(ip: String, port: Int, timeout: Int): Boolean {
        return try {
            Socket().use { it.connect(InetSocketAddress(ip, port), timeout); true }
        } catch (_: Exception) {
            false
        }
    }

    private fun getLocalIpAddress(): String? {
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val activeNetwork = cm.activeNetwork
            val lp = cm.getLinkProperties(activeNetwork)
            for (la in lp?.linkAddresses ?: emptyList()) {
                val addr = la.address
                if (addr is Inet4Address && !addr.isLoopbackAddress) {
                    return addr.hostAddress
                }
            }
        } catch (_: Exception) {}

        
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            var fallbackAddr: String? = null
            for (intf in Collections.list(interfaces)) {
                if (!intf.isUp || intf.isLoopback) continue
                val name = intf.name.lowercase()
                val isWifiOrEthernet = name.contains("wlan") || name.contains("ap") || name.contains("p2p") || name.contains("eth")
                
                val addrs = intf.inetAddresses
                for (addr in Collections.list(addrs)) {
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        val ip = addr.hostAddress
                        if (isWifiOrEthernet) {
                            return ip 
                        } else if (fallbackAddr == null) {
                            fallbackAddr = ip
                        }
                    }
                }
            }
            if (fallbackAddr != null) return fallbackAddr
        } catch (e: Exception) {
            Log.e(TAG, "Error fallback getting network interfaces", e)
        }
        return null
    }

    fun stopDiscovery() {
        Log.d(TAG, "stopDiscovery() called")
        isDiscovering = false
        harvestJob?.cancel()
        harvestJob = null
        discoveryListeners.forEach { try { nsdManager.stopServiceDiscovery(it) } catch (_: Exception) {} }
        discoveryListeners.clear()
        scanJob?.cancel()
        resolverJob?.cancel()
        resolveChannel?.close()
        resolveChannel = null
        devices.clear()
        try { if (multicastLock?.isHeld == true) multicastLock?.release() } catch (_: Exception) {}
        multicastLock = null
    }
}
