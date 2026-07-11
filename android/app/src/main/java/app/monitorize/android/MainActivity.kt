package app.monitorize.android

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.wifi.WifiManager
import android.util.Log
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.zIndex
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import app.monitorize.android.discovery.DeviceDiscovery
import app.monitorize.android.discovery.DiscoveredDevice
import app.monitorize.android.input.InputEventSender
import app.monitorize.android.streaming.H264Decoder
import app.monitorize.android.streaming.StreamReceiver
import app.monitorize.android.ui.theme.BreezeAccent as AccentIndigo
import app.monitorize.android.ui.theme.BreezeBackground as BackgroundDark
import app.monitorize.android.ui.theme.BreezeBorder as BorderDark
import app.monitorize.android.ui.theme.BreezeButton as GreenAccent
import app.monitorize.android.ui.theme.BreezeSurface as CardDark
import app.monitorize.android.ui.theme.BreezeTextMuted as TextMuted
import app.monitorize.android.ui.theme.MonitorizeTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale








enum class Screen { Home, Receive }

private const val SURFACE_READY_RETRY_INTERVAL_MS = 75L
private const val SURFACE_READY_RETRY_TIMEOUT_MS = 3000L
private const val SURFACE_READY_BLOCKED_LOG_INTERVAL_MS = 500L

private const val MAX_RECENT_WIFI_DEVICES = 5

internal fun recentHostKey(device: DiscoveredDevice): String =
    "${device.ip.trim().lowercase(Locale.ROOT)}:${device.port}"

private fun isRememberableWifiDevice(device: DiscoveredDevice): Boolean =
    !device.isUsb && device.ip.isNotBlank() && device.ip != "127.0.0.1"

internal fun updatedRecentDevices(
    existing: List<DiscoveredDevice>,
    device: DiscoveredDevice
): List<DiscoveredDevice> {
    if (!isRememberableWifiDevice(device)) return existing
    val hostKey = recentHostKey(device)
    return (listOf(device.copy(isUsb = false)) + existing.filterNot { recentHostKey(it) == hostKey })
        .take(MAX_RECENT_WIFI_DEVICES)
}

internal fun removedRecentDevices(
    existing: List<DiscoveredDevice>,
    device: DiscoveredDevice
): List<DiscoveredDevice> = existing.filterNot { recentHostKey(it) == recentHostKey(device) }

class MainActivity : ComponentActivity() {

    @Volatile private var decoder: H264Decoder? = null
    @Volatile private var receiver: StreamReceiver? = null
    @Volatile private var inputSender: InputEventSender? = null
    @Volatile private var wifiLock: WifiManager.WifiLock? = null
    @Volatile private var clearPairingUi: ((Boolean) -> Unit)? = null
    private val streamStateLock = Any()
    private val streamMutex = Mutex()
    private var activeStreamSession = 0L
    private var surfaceGeneration = 0L
    private var restartingAfterDisconnect = false
    private val status = mutableStateOf("")
    private lateinit var discovery: DeviceDiscovery

    private val prefs by lazy { getSharedPreferences("monitorize_prefs", Context.MODE_PRIVATE) }

    private data class StreamDimensions(val width: Int, val height: Int)

    private data class StreamResources(
        val receiver: StreamReceiver?,
        val decoder: H264Decoder?,
        val inputSender: InputEventSender?,
        val wifiLock: WifiManager.WifiLock?
    )

    companion object {
        private const val DEFAULT_STREAM_WIDTH = 1280
        private const val DEFAULT_STREAM_HEIGHT = 800
        private const val MIN_STREAM_DIMENSION = 2
        private const val MAX_STREAM_DIMENSION = 7680
        private const val RECENT_WIFI_DEVICES_KEY = "recent_wifi_devices"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        discovery = DeviceDiscovery(this)

        
        WindowCompat.setDecorFitsSystemWindows(window, false)
        applyImmersiveMode()

        
        window.attributes.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES

        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(android.graphics.Color.parseColor("#1B1E24")))
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        
        WindowInsetsControllerCompat(window, window.decorView).isAppearanceLightStatusBars = false

        setContent {
            val configuration = LocalConfiguration.current
            val isTablet = configuration.smallestScreenWidthDp >= 600
            val isLandscapeMobile = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE && !isTablet

            var currentScreen by remember { mutableStateOf(Screen.Home) }
            var isSettingsOpen by remember { mutableStateOf(false) }

            val initialDimensions = remember {
                sanitizeStreamDimensions(
                    prefs.getInt("width", DEFAULT_STREAM_WIDTH),
                    prefs.getInt("height", DEFAULT_STREAM_HEIGHT)
                )
            }
            var width by remember { mutableIntStateOf(initialDimensions.width) }
            var height by remember { mutableIntStateOf(initialDimensions.height) }
            var decodedWidth by remember { mutableIntStateOf(width) }
            var decodedHeight by remember { mutableIntStateOf(height) }
            var selectedDevice by remember { mutableStateOf<DiscoveredDevice?>(null) }
            var recentDevices by remember { mutableStateOf(loadRecentDevices()) }
            var disconnectionMessage by remember { mutableStateOf<String?>(
                if (intent.getBooleanExtra("SHOW_DISCONNECTED", false)) "Disconnected" else null
            ) }
            var pairingSubmit by remember { mutableStateOf<((String) -> Unit)?>(null) }
            var pairingCode by remember { mutableStateOf("") }
            
            val coroutineScope = rememberCoroutineScope()
            fun clearPairingState(invokeCancel: Boolean) {
                if (invokeCancel) {
                    pairingSubmit?.invoke("")
                }
                pairingSubmit = null
                pairingCode = ""
            }

            SideEffect {
                clearPairingUi = { invokeCancel ->
                    clearPairingState(invokeCancel)
                }
            }
            DisposableEffect(Unit) {
                onDispose {
                    if (clearPairingUi != null) {
                        clearPairingUi = null
                    }
                }
            }

            fun cancelPairing() {
                clearPairingState(invokeCancel = true)
                selectedDevice = null
                currentScreen = Screen.Home
                status.value = ""
                coroutineScope.launch { stopStream() }
            }

            
            if (disconnectionMessage != null) {
                LaunchedEffect(disconnectionMessage) {
                    delay(5000)
                    disconnectionMessage = null
                }
            }

            MonitorizeTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = BackgroundDark) {
                    Box(modifier = Modifier.fillMaxSize()) {
                        when (currentScreen) {
                            Screen.Home -> {
                                HomeScreen(
                                    devices = discovery.devices,
                                    recentDevices = recentDevices,
                                    onDeviceSelected = { device ->
                                        discovery.stopDiscovery()
                                        decodedWidth = width
                                        decodedHeight = height
                                        selectedDevice = device
                                        currentScreen = Screen.Receive
                                        disconnectionMessage = null 
                                    },
                                    onForgetDevice = { device ->
                                        recentDevices = forgetRecentDevice(device)
                                    },
                                    onSettingsToggle = { isSettingsOpen = true },
                                    onStartDiscovery = { discovery.startDiscovery() }
                                )
                            }
                            Screen.Receive -> {
                                ReceiveScreen(
                                    hostIp = if (selectedDevice?.isUsb == true) "" else selectedDevice?.ip ?: "",
                                    width = width,
                                    height = height,
                                    fps = selectedDevice?.fps ?: 60,
                                    displayWidth = decodedWidth,
                                    displayHeight = decodedHeight,
                                    status = status.value,
                                    onBack = {
                                        clearPairingState(invokeCancel = true)
                                        restartApp()
                                    },
                                    onSurfaceCreated = { ip, surface, w, h, onFirstFrameRendered ->
                                        val generation = registerSurfaceCreated()
                                        coroutineScope.launch {
                                            val port = selectedDevice?.port ?: 7110
                                            startStream(
                                                ip, port, surface, w, h,
                                                surfaceGeneration = generation,
                                                device = selectedDevice,
                                                onPairingRequired = { submit ->
                                                    runOnUiThread {
                                                        pairingCode = ""
                                                        pairingSubmit = submit
                                                    }
                                                },
                                                onDecodedSize = { decodedW, decodedH ->
                                                    decodedWidth = decodedW
                                                    decodedHeight = decodedH
                                                },
                                                onFirstFrameRendered = {
                                                    selectedDevice?.let { device ->
                                                        recentDevices = rememberRecentDevice(device)
                                                    }
                                                    onFirstFrameRendered()
                                                },
                                                onDisconnect = {
                                                    runOnUiThread {
                                                        disconnectionMessage = "Connection stopped"
                                                        status.value = "Unable to keep the connection alive"
                                                    }
                                                }
                                            )
                                        }
                                        generation
                                    },
                                    onSurfaceDestroyed = { generation ->
                                        clearPairingState(invokeCancel = false)
                                        coroutineScope.launch {
                                            stopStream(surfaceGeneration = generation)
                                        }
                                    },
                                    onSurfaceRenderTimeout = {
                                        status.value = "Video surface did not render; try moving your mouse into the virtual display"
                                    },
                                    onInputEvent = { event, viewW, viewH -> inputSender?.send(event, viewW, viewH) }
                                )
                            }
                        }

                        
                        AnimatedVisibility(
                            visible = isSettingsOpen,
                            enter = slideInHorizontally(initialOffsetX = { it }, animationSpec = tween(300)),
                            exit = slideOutHorizontally(targetOffsetX = { it }, animationSpec = tween(300)),
                            modifier = Modifier.align(Alignment.CenterEnd).zIndex(10f)
                        ) {
                            val panelWidthFraction = when {
                                isTablet -> 0.38f
                                isLandscapeMobile -> 0.42f
                                else -> 0.74f
                            }

                            Box(modifier = Modifier.fillMaxHeight().fillMaxWidth(panelWidthFraction).background(CardDark).border(1.dp, BorderDark)) {
                                SettingsPanel(
                                    initialWidth = width,
                                    initialHeight = height,
                                    onSave = { w, h ->
                                        val sanitized = sanitizeStreamDimensions(w, h)
                                        if (sanitized.width != width || sanitized.height != height) {
                                            prefs.edit()
                                                .putInt("width", sanitized.width)
                                                .putInt("height", sanitized.height)
                                                .apply()
                                            width = sanitized.width
                                            height = sanitized.height
                                            decodedWidth = sanitized.width
                                            decodedHeight = sanitized.height
                                            isSettingsOpen = false
                                        } else {
                                            isSettingsOpen = false
                                        }
                                    }
                                )
                            }
                        }
                        
                        
                        if (isSettingsOpen) {
                            Box(
                                modifier = Modifier
                                    .fillMaxSize()
                                    .background(Color.Black.copy(alpha = 0.6f))
                                    .clickable { isSettingsOpen = false }
                                    .zIndex(9f)
                            )
                        }

                        if (pairingSubmit != null) {
                            AlertDialog(
                                onDismissRequest = {
                                    cancelPairing()
                                },
                                title = { Text("Pair encrypted connection") },
                                text = {
                                    Column {
                                        Text("Enter the 6-digit code shown in the Linux app.")
                                        Spacer(modifier = Modifier.height(12.dp))
                                        OutlinedTextField(
                                            value = pairingCode,
                                            onValueChange = { pairingCode = it.filter(Char::isDigit).take(6) },
                                            singleLine = true,
                                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                                            label = { Text("Pairing code") }
                                        )
                                    }
                                },
                                confirmButton = {
                                    TextButton(
                                        enabled = pairingCode.length == 6,
                                        onClick = {
                                            pairingSubmit?.invoke(pairingCode)
                                            pairingSubmit = null
                                        }
                                    ) { Text("Pair") }
                                },
                                dismissButton = {
                                    TextButton(onClick = {
                                        cancelPairing()
                                    }) { Text("Cancel") }
                                }
                            )
                        }

                        
                        AnimatedVisibility(
                            visible = disconnectionMessage != null,
                            enter = fadeIn() + expandVertically(expandFrom = Alignment.Bottom),
                            exit = fadeOut() + shrinkVertically(shrinkTowards = Alignment.Bottom),
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 48.dp)
                                .zIndex(20f)
                        ) {
                            Surface(
                                color = CardDark,
                                shape = RoundedCornerShape(12.dp),
                                border = BorderStroke(1.dp, BorderDark),
                                shadowElevation = 8.dp
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 14.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.Center
                                ) {
                                    Box(modifier = Modifier.size(8.dp).background(Color.Red, CircleShape))
                                    Spacer(modifier = Modifier.width(12.dp))
                                    Text(
                                        text = disconnectionMessage ?: "",
                                        color = Color.White,
                                        fontSize = 14.sp,
                                        fontWeight = FontWeight.SemiBold
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            applyImmersiveMode()
        }
    }

    override fun onStop() {
        super.onStop()
        discovery.stopDiscovery()
        clearPairingUi?.invoke(false)
        closeStreamResourcesBlocking(snapshotAndClearStreamResources(invalidateSurface = true))
    }

    override fun onDestroy() {
        super.onDestroy()
        clearPairingUi?.invoke(false)
        closeStreamResourcesBlocking(snapshotAndClearStreamResources(invalidateSurface = true))
    }

    private fun restartApp() {
        if (restartingAfterDisconnect) return
        restartingAfterDisconnect = true
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        )
        finish()
    }

    private fun applyImmersiveMode() {
        val windowInsetsController = WindowCompat.getInsetsController(window, window.decorView)
        windowInsetsController.hide(WindowInsetsCompat.Type.systemBars())
        windowInsetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun sanitizeStreamDimensions(width: Int, height: Int): StreamDimensions {
        return StreamDimensions(
            sanitizeStreamDimension(width, DEFAULT_STREAM_WIDTH),
            sanitizeStreamDimension(height, DEFAULT_STREAM_HEIGHT)
        )
    }

    private fun sanitizeStreamDimension(value: Int, fallback: Int): Int {
        val bounded = value.takeIf { it >= MIN_STREAM_DIMENSION }
            ?: fallback
        val clamped = bounded.coerceIn(MIN_STREAM_DIMENSION, MAX_STREAM_DIMENSION)
        val even = if (clamped % 2 == 0) clamped else clamped - 1
        return even.coerceAtLeast(MIN_STREAM_DIMENSION)
    }

    private fun loadRecentDevices(): List<DiscoveredDevice> {
        val saved = prefs.getString(RECENT_WIFI_DEVICES_KEY, null) ?: return emptyList()
        return try {
            val devices = JSONArray(saved).let { entries ->
                buildList {
                    for (index in 0 until entries.length()) {
                        val entry = entries.optJSONObject(index) ?: continue
                        val host = entry.optString("ip").trim()
                        val port = entry.optInt("port", 7110)
                        if (host.isBlank() || host == "127.0.0.1" || port !in 1..65532) continue
                        add(
                            DiscoveredDevice(
                                name = entry.optString("name").ifBlank { "WiFi Device" },
                                ip = host,
                                port = port,
                                encrypted = entry.optBoolean("encrypted", false),
                                fingerprint = entry.optString("fingerprint").takeIf { it.isNotBlank() }
                            )
                        )
                    }
                }
            }
            devices.asReversed().fold(emptyList()) { recent, device ->
                updatedRecentDevices(recent, device)
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun rememberRecentDevice(device: DiscoveredDevice): List<DiscoveredDevice> {
        if (!isRememberableWifiDevice(device)) return loadRecentDevices()
        val remembered = device.copy(
            fingerprint = device.fingerprint ?: prefs.getString("tls_host_${device.ip}", null),
            isUsb = false,
            inputTransport = null,
            serviceName = ""
        )
        val recentDevices = updatedRecentDevices(loadRecentDevices(), remembered)
        persistRecentDevices(recentDevices)
        return recentDevices
    }

    private fun forgetRecentDevice(device: DiscoveredDevice): List<DiscoveredDevice> {
        val savedDevices = loadRecentDevices()
        val fingerprints = buildSet {
            add(device.fingerprint)
            add(prefs.getString("tls_host_${device.ip}", null))
            savedDevices.filter { recentHostKey(it) == recentHostKey(device) }
                .forEach { add(it.fingerprint) }
        }.filterNotNull().filter { it.isNotBlank() }
        val recentDevices = removedRecentDevices(savedDevices, device)

        prefs.edit().apply {
            remove("tls_host_${device.ip}")
            prefs.all
                .filter { (key, value) ->
                    key.startsWith("tls_host_") && value is String && value in fingerprints
                }
                .keys
                .forEach { remove(it) }
            fingerprints.forEach { remove("tls_token_$it") }
            writeRecentDevices(this, recentDevices)
            apply()
        }
        return recentDevices
    }

    private fun persistRecentDevices(devices: List<DiscoveredDevice>) {
        prefs.edit().apply {
            writeRecentDevices(this, devices)
            apply()
        }
    }

    private fun writeRecentDevices(editor: SharedPreferences.Editor, devices: List<DiscoveredDevice>) {
        if (devices.isEmpty()) {
            editor.remove(RECENT_WIFI_DEVICES_KEY)
            return
        }
        val entries = JSONArray()
        devices.forEach { device ->
            entries.put(
                JSONObject()
                    .put("name", device.name)
                    .put("ip", device.ip)
                    .put("port", device.port)
                    .put("encrypted", device.encrypted)
                    .put("fingerprint", device.fingerprint ?: "")
            )
        }
        editor.putString(RECENT_WIFI_DEVICES_KEY, entries.toString())
    }

    private fun registerSurfaceCreated(): Long = synchronized(streamStateLock) {
        surfaceGeneration += 1
        surfaceGeneration
    }

    private fun isSurfaceCurrent(generation: Long): Boolean = synchronized(streamStateLock) {
        surfaceGeneration == generation
    }

    private fun isActiveStream(sessionId: Long, expectedReceiver: StreamReceiver? = null): Boolean =
        synchronized(streamStateLock) {
            activeStreamSession == sessionId &&
                (expectedReceiver == null || receiver === expectedReceiver)
        }

    private fun snapshotAndClearStreamResources(invalidateSurface: Boolean = false): StreamResources =
        synchronized(streamStateLock) {
            activeStreamSession += 1
            if (invalidateSurface) {
                surfaceGeneration += 1
            }
            val resources = StreamResources(receiver, decoder, inputSender, wifiLock)
            receiver = null
            decoder = null
            inputSender = null
            wifiLock = null
            resources
        }

    private suspend fun closeStreamResources(resources: StreamResources) {
        withContext(Dispatchers.IO) {
            closeStreamResourcesBlocking(resources)
        }
    }

    private fun closeStreamResourcesBlocking(resources: StreamResources) {
        resources.receiver?.stop()
        resources.inputSender?.stop()
        resources.decoder?.release()
        try {
            if (resources.wifiLock?.isHeld == true) {
                resources.wifiLock.release()
                Log.i("MainActivity", "Released low latency Wi-Fi lock")
            }
        } catch (e: Exception) {
            Log.e("MainActivity", "Failed to release Wi-Fi lock: ${e.message}")
        }
    }

    private fun acquireLowLatencyWifiLock(): WifiManager.WifiLock? {
        return try {
            val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val lockType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiManager.createWifiLock(lockType, "Monitorize:LowLatencyLock").apply {
                setReferenceCounted(false)
                acquire()
                Log.i("MainActivity", "Acquired low latency Wi-Fi lock")
            }
        } catch (e: Exception) {
            Log.e("MainActivity", "Failed to acquire Wi-Fi lock: ${e.message}")
            null
        }
    }

    private suspend fun startStream(
        hostIp: String,
        hostPort: Int,
        surface: Surface,
        width: Int,
        height: Int,
        surfaceGeneration: Long,
        device: DiscoveredDevice?,
        onPairingRequired: ((String) -> Unit) -> Unit,
        onDecodedSize: (Int, Int) -> Unit,
        onFirstFrameRendered: () -> Unit,
        onDisconnect: () -> Unit
    ) = streamMutex.withLock {
        if (!isSurfaceCurrent(surfaceGeneration)) {
            return@withLock
        }

        closeStreamResources(snapshotAndClearStreamResources())
        if (!isSurfaceCurrent(surfaceGeneration)) {
            return@withLock
        }

        val streamDimensions = sanitizeStreamDimensions(width, height)
        val sessionId = synchronized(streamStateLock) {
            activeStreamSession += 1
            activeStreamSession
        }
        val newWifiLock = acquireLowLatencyWifiLock()
        val d = H264Decoder(
            surface,
            onOutputSizeChanged = { decodedWidth, decodedHeight ->
                if (isActiveStream(sessionId)) {
                    runOnUiThread {
                        if (isActiveStream(sessionId)) {
                            onDecodedSize(decodedWidth, decodedHeight)
                        }
                    }
                }
            },
            onFirstFrameRendered = {
                if (isActiveStream(sessionId)) {
                    runOnUiThread {
                        if (isActiveStream(sessionId)) {
                            onFirstFrameRendered()
                        }
                    }
                }
            }
        )
        val encrypted = device?.let { it.encrypted && !it.isUsb } == true
        val advertisedFingerprint = device?.fingerprint
        val savedFingerprint = advertisedFingerprint?.takeIf {
            prefs.getString("tls_token_$it", null) != null
        } ?: prefs.getString("tls_host_$hostIp", null)
        val savedToken = savedFingerprint?.let { prefs.getString("tls_token_$it", null) }
        var inputStarted = false

        val streamReceiver = StreamReceiver(
            d, streamDimensions.width, streamDimensions.height, hostIp.takeIf { it.isNotBlank() }, hostPort,
            encrypted, savedFingerprint, savedToken
        )
        streamReceiver.apply {
            onStatusChange = { msg ->
                if (isActiveStream(sessionId, streamReceiver)) {
                    runOnUiThread {
                        if (isActiveStream(sessionId, streamReceiver)) {
                            status.value = msg
                        }
                    }
                }
            }
            this.onDisconnect = {
                if (isActiveStream(sessionId, streamReceiver)) {
                    onDisconnect()
                }
            }
            this.onPairingRequired = { submit ->
                if (isActiveStream(sessionId, streamReceiver)) {
                    onPairingRequired { code ->
                        if (isActiveStream(sessionId, streamReceiver)) {
                            submit(code)
                        } else {
                            submit("")
                        }
                    }
                } else {
                    submit("")
                }
            }
            onCredentials = credentials@ { fingerprint, token ->
                if (!isActiveStream(sessionId, streamReceiver)) {
                    return@credentials
                }
                if (fingerprint.isEmpty() || token.isEmpty()) {
                    prefs.edit().remove("tls_host_$hostIp").apply()
                    return@credentials
                }

                prefs.edit()
                    .putString("tls_host_$hostIp", fingerprint)
                    .putString("tls_token_$fingerprint", token)
                    .apply()
                if (!inputStarted) {
                    val sender = InputEventSender(hostIp, hostPort, true, fingerprint, token)
                    val assigned = synchronized(streamStateLock) {
                        if (activeStreamSession == sessionId && receiver === streamReceiver && inputSender == null) {
                            inputStarted = true
                            inputSender = sender
                            sender.start()
                            true
                        } else {
                            false
                        }
                    }
                    if (!assigned) {
                        sender.stop()
                    }
                }
            }
        }

        val unencryptedInputSender = if (!encrypted) {
            InputEventSender(hostIp.takeIf { it.isNotBlank() }, hostPort)
        } else {
            null
        }
        val registered = synchronized(streamStateLock) {
            if (activeStreamSession == sessionId && this.surfaceGeneration == surfaceGeneration) {
                decoder = d
                receiver = streamReceiver
                wifiLock = newWifiLock
                if (unencryptedInputSender != null) {
                    inputSender = unencryptedInputSender
                    unencryptedInputSender.start()
                }
                streamReceiver.start()
                true
            } else {
                false
            }
        }
        if (!registered) {
            unencryptedInputSender?.stop()
            streamReceiver.stop()
            d.release()
            closeStreamResourcesBlocking(StreamResources(null, null, null, newWifiLock))
        }
    }

    private suspend fun stopStream(surfaceGeneration: Long? = null) {
        streamMutex.withLock {
            if (surfaceGeneration != null && !isSurfaceCurrent(surfaceGeneration)) {
                return@withLock
            }
            closeStreamResources(snapshotAndClearStreamResources(invalidateSurface = true))
        }
    }
}



@Composable
fun HomeScreen(
    devices: List<DiscoveredDevice>,
    recentDevices: List<DiscoveredDevice>,
    onDeviceSelected: (DiscoveredDevice) -> Unit,
    onForgetDevice: (DiscoveredDevice) -> Unit,
    onSettingsToggle: () -> Unit,
    onStartDiscovery: () -> Unit = {}
) {
    val configuration = LocalConfiguration.current
    val isTablet = configuration.smallestScreenWidthDp >= 600
    val isLandscapeMobile = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE && !isTablet

    val horizontalPadding = when {
        isTablet -> 48.dp
        isLandscapeMobile -> 36.dp
        else -> 28.dp
    }
    val topSpacerHeight = when {
        isTablet -> 104.dp
        isLandscapeMobile -> 56.dp
        else -> 88.dp
    }
    val devicesSpacing = if (isLandscapeMobile) 12.dp else 22.dp
    val settingsButtonPadding = if (isLandscapeMobile) 16.dp else 24.dp
    val settingsButtonSize = when {
        isTablet -> 48.dp
        isLandscapeMobile -> 36.dp
        else -> 44.dp
    }
    val settingsIconSize = when {
        isTablet -> 24.dp
        isLandscapeMobile -> 18.dp
        else -> 22.dp
    }
    val refreshButtonWidth = if (isLandscapeMobile) 82.dp else 94.dp
    val refreshButtonHeight = if (isLandscapeMobile) 34.dp else 38.dp
    val manualRowPadding = if (isLandscapeMobile) 16.dp else 32.dp
    val manualSpacerHeight = if (isLandscapeMobile) 12.dp else 16.dp
    val manualFieldHeight = if (isLandscapeMobile) 48.dp else 56.dp
    val context = androidx.compose.ui.platform.LocalContext.current
    val prefs = remember {
        context.getSharedPreferences(
            "monitorize_prefs", android.content.Context.MODE_PRIVATE
        )
    }
    var manualIp by remember { mutableStateOf(prefs.getString("manual_ip", "") ?: "") }
    var manualPort by remember { mutableStateOf(prefs.getString("manual_port", "7110") ?: "7110") }
    var manualError by remember { mutableStateOf<String?>(null) }
    var deviceToForget by remember { mutableStateOf<DiscoveredDevice?>(null) }
    val recentHostKeys = recentDevices.map(::recentHostKey).toSet()
    val discoveredByHost = devices.filterNot { it.isUsb }.associateBy(::recentHostKey)
    val displayedRecentDevices = recentDevices.map { recent ->
        discoveredByHost[recentHostKey(recent)] ?: recent
    }
    val otherDevices = devices.filter { it.isUsb || recentHostKey(it) !in recentHostKeys }

    LaunchedEffect(Unit) {
        onStartDiscovery()
    }

    Box(modifier = Modifier.fillMaxSize()) {
        IconButton(
            onClick = onSettingsToggle,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(settingsButtonPadding)
                .size(settingsButtonSize)
                .clip(CircleShape)
                .background(CardDark)
        ) {
            Icon(
                Icons.Default.Settings,
                contentDescription = "Settings",
                tint = Color.White,
                modifier = Modifier.size(settingsIconSize)
            )
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = horizontalPadding)
        ) {
            Spacer(modifier = Modifier.height(topSpacerHeight))
            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(devicesSpacing),
                contentPadding = PaddingValues(bottom = 16.dp)
            ) {
                if (displayedRecentDevices.isNotEmpty()) {
                    item {
                        Text(
                            "FREQUENTLY CONNECTED:",
                            fontSize = 11.sp,
                            color = TextMuted,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 2.sp
                        )
                    }
                    items(displayedRecentDevices, key = { recentHostKey(it) }) { device ->
                        DeviceItem(
                            device = device,
                            onClick = { onDeviceSelected(device) },
                            onLongClick = { deviceToForget = device }
                        )
                    }
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            "DEVICES:",
                            fontSize = 11.sp,
                            color = TextMuted,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 2.sp
                        )
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(AccentIndigo.copy(alpha = 0.15f))
                                    .border(1.dp, AccentIndigo.copy(alpha = 0.4f), RoundedCornerShape(6.dp))
                                    .clickable { onStartDiscovery() }
                                    .padding(horizontal = 12.dp, vertical = 6.dp)
                            ) {
                                Text(
                                    text = "REFRESH",
                                    color = AccentIndigo,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                            if (!isTablet) {
                                IconButton(
                                    onClick = onSettingsToggle,
                                    modifier = Modifier
                                        .size(if (isLandscapeMobile) 32.dp else 36.dp)
                                        .background(CardDark, CircleShape)
                                ) {
                                    Icon(
                                        Icons.Default.Settings,
                                        contentDescription = "Settings",
                                        tint = Color.White,
                                        modifier = Modifier.size(if (isLandscapeMobile) 18.dp else 20.dp)
                                    )
                                }
                            }
                        }
                    }
                }

                if (otherDevices.isEmpty()) {
                    item {
                        Box(
                            modifier = Modifier.fillParentMaxSize(),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "Searching for devices...\n(Check USB or Wi-Fi connection)",
                                color = TextMuted,
                                fontSize = 14.sp,
                                textAlign = androidx.compose.ui.text.style.TextAlign.Center
                            )
                        }
                    }
                } else {
                    items(otherDevices, key = { if (it.isUsb) "usb" else recentHostKey(it) }) { device ->
                        DeviceItem(device = device, onClick = { onDeviceSelected(device) })
                    }
                }
            }
            
            Spacer(modifier = Modifier.height(manualSpacerHeight))
            Column(modifier = Modifier.fillMaxWidth().padding(bottom = manualRowPadding)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedTextField(
                        value = manualIp,
                        onValueChange = {
                            manualIp = it
                            manualError = null
                        },
                        placeholder = { Text("Enter IP address", color = TextMuted) },
                        modifier = Modifier.weight(1.5f).height(manualFieldHeight),
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentIndigo,
                            unfocusedBorderColor = BorderDark
                        )
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    OutlinedTextField(
                        value = manualPort,
                        onValueChange = {
                            manualPort = it.filter(Char::isDigit).take(5)
                            manualError = null
                        },
                        placeholder = { Text("Port", color = TextMuted) },
                        modifier = Modifier.weight(0.8f).height(manualFieldHeight),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentIndigo,
                            unfocusedBorderColor = BorderDark
                        )
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Button(
                        onClick = {
                            val ip = manualIp.trim()
                            val portText = manualPort.trim()
                            val port = portText.toIntOrNull()
                            when {
                                ip.isBlank() -> manualError = "Enter a host or IP address."
                                port == null || port !in 1..65532 ->
                                    manualError = "Port must be between 1 and 65532."
                                else -> {
                                    manualError = null
                                    manualIp = ip
                                    manualPort = port.toString()
                                    prefs.edit().apply {
                                        putString("manual_ip", ip)
                                        putString("manual_port", port.toString())
                                        apply()
                                    }
                                    onDeviceSelected(DiscoveredDevice(
                                        name = "Manual WiFi", ip = ip, port = port,
                                        isUsb = false, encrypted = false
                                    ))
                                }
                            }
                        },
                        modifier = Modifier.height(manualFieldHeight),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = GreenAccent)
                    ) {
                        Text("Connect", fontWeight = FontWeight.Bold, color = Color.White)
                    }
                }
                manualError?.let { error ->
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(error, color = Color(0xFFFF6B6B), fontSize = 12.sp)
                }
            }
        }

        deviceToForget?.let { device ->
            AlertDialog(
                onDismissRequest = { deviceToForget = null },
                title = { Text("Forget saved device?") },
                text = {
                    Text("Remove ${device.name} from Frequently Connected and forget its saved credentials?")
                },
                confirmButton = {
                    TextButton(onClick = {
                        onForgetDevice(device)
                        deviceToForget = null
                    }) { Text("Forget") }
                },
                dismissButton = {
                    TextButton(onClick = { deviceToForget = null }) { Text("Cancel") }
                }
            )
        }
    }
}

@Composable
@OptIn(ExperimentalFoundationApi::class)
fun DeviceItem(
    device: DiscoveredDevice,
    onClick: () -> Unit,
    onLongClick: (() -> Unit)? = null
) {
    val configuration = LocalConfiguration.current
    val isTablet = configuration.smallestScreenWidthDp >= 600
    val isLandscapeMobile = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE && !isTablet

    val verticalPadding = if (isLandscapeMobile) 14.dp else 18.dp
    val horizontalPadding = if (isLandscapeMobile) 16.dp else 18.dp
    val titleFontSize = if (isLandscapeMobile) 16.sp else 18.sp

    val interactionModifier = if (onLongClick == null) {
        Modifier.clickable(onClick = onClick)
    } else {
        Modifier.combinedClickable(
            onClick = onClick,
            onLongClick = onLongClick,
            onLongClickLabel = "Forget saved device"
        )
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(CardDark)
            .border(1.dp, BorderDark, RoundedCornerShape(12.dp))
            .then(interactionModifier)
            .padding(horizontal = horizontalPadding, vertical = verticalPadding),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                device.name, 
                color = Color.White, 
                fontWeight = FontWeight.Bold, 
                fontSize = titleFontSize
            )
            
            Text(
                if (device.isUsb) device.ip else "${device.ip}:${device.port}",
                color = Color.White.copy(alpha = 0.7f), 
                fontSize = 13.sp,
                modifier = Modifier.padding(top = 2.dp)
            )
        }
        
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(4.dp))
                .background(Color.White.copy(alpha = 0.2f))
                .padding(horizontal = 10.dp, vertical = 4.dp)
        ) {
            Text(
                text = if (device.isUsb) "usb" else "wifi",
                color = Color.White,
                fontSize = 10.sp,
                fontWeight = FontWeight.ExtraBold
            )
        }
    }
}

@Composable
fun ResolutionCard(
    title: String,
    subtitle: String,
    isSelected: Boolean,
    onClick: () -> Unit
) {
    val configuration = LocalConfiguration.current
    val isTablet = configuration.smallestScreenWidthDp >= 600
    val cardPadding = if (isTablet) 16.dp else 12.dp
    val titleFontSize = if (isTablet) 16.sp else 14.sp
    val subtitleFontSize = if (isTablet) 13.sp else 11.sp
    val verticalPadding = if (isTablet) 6.dp else 4.dp

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = verticalPadding)
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (isSelected) AccentIndigo.copy(alpha = 0.15f) else CardDark
        ),
        border = BorderStroke(
            width = if (isSelected) 2.dp else 1.dp,
            color = if (isSelected) AccentIndigo else BorderDark
        )
    ) {
        Column(
            modifier = Modifier.padding(cardPadding)
        ) {
            Text(
                text = title,
                color = if (isSelected) Color.White else Color.White,
                fontWeight = FontWeight.Bold,
                fontSize = titleFontSize
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = subtitle,
                color = Color.White.copy(alpha = 0.7f),
                fontSize = subtitleFontSize
            )
        }
    }
}

private data class SettingsMetrics(
    val nativeW: Int,
    val nativeH: Int,
    val mediumW: Int,
    val mediumH: Int,
    val lowW: Int,
    val lowH: Int,
    val initialSelected: String
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsPanel(
    initialWidth: Int,
    initialHeight: Int,
    onSave: (Int, Int) -> Unit
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val metrics = remember(context, initialWidth, initialHeight) {
        val wm = context.getSystemService(android.content.Context.WINDOW_SERVICE) as android.view.WindowManager
        val (rawW, rawH) = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
            val bounds = wm.maximumWindowMetrics.bounds
            Pair(bounds.width(), bounds.height())
        } else {
            val dm = android.util.DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(dm)
            Pair(dm.widthPixels, dm.heightPixels)
        }
        val nativeW = maxOf(rawW, rawH)
        val nativeH = minOf(rawW, rawH)

        fun getNearestMultipleOf16(value: Int): Int {
            return ((value + 8) / 16) * 16
        }

        val mediumW = getNearestMultipleOf16((nativeW * 0.75f).toInt())
        val mediumH = getNearestMultipleOf16((nativeH * 0.75f).toInt())
        val lowW = getNearestMultipleOf16((nativeW * 0.5f).toInt())
        val lowH = getNearestMultipleOf16((nativeH * 0.5f).toInt())

        val initialSelected = when {
            initialWidth == nativeW && initialHeight == nativeH -> "native"
            initialWidth == mediumW && initialHeight == mediumH -> "medium"
            initialWidth == lowW && initialHeight == lowH -> "low"
            else -> "custom"
        }
        SettingsMetrics(nativeW, nativeH, mediumW, mediumH, lowW, lowH, initialSelected)
    }

    val nativeW = metrics.nativeW
    val nativeH = metrics.nativeH
    val mediumW = metrics.mediumW
    val mediumH = metrics.mediumH
    val lowW = metrics.lowW
    val lowH = metrics.lowH
    val initialSelected = metrics.initialSelected

    var selectedOption by remember { mutableStateOf(initialSelected) }
    var customWidthText by remember { mutableStateOf(if (initialSelected == "custom") initialWidth.toString() else "") }
    var customHeightText by remember { mutableStateOf(if (initialSelected == "custom") initialHeight.toString() else "") }

    val configuration = LocalConfiguration.current
    val isTablet = configuration.smallestScreenWidthDp >= 600
    val panelHorizontalPadding = if (isTablet) 28.dp else 18.dp
    val panelTopPadding = if (isTablet) 40.dp else 52.dp
    val panelBottomPadding = if (isTablet) 28.dp else 20.dp
    val titleSize = if (isTablet) 22.sp else 18.sp
    val spacingHeight = if (isTablet) 24.dp else 16.dp

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(
                start = panelHorizontalPadding,
                top = panelTopPadding,
                end = panelHorizontalPadding,
                bottom = panelBottomPadding,
            )
            .verticalScroll(rememberScrollState())
    ) {
        Text("Resolution Settings", fontSize = titleSize, fontWeight = FontWeight.Bold, color = Color.White)
        Spacer(modifier = Modifier.height(spacingHeight))

        listOf(
            Triple("Native", "${nativeW} × ${nativeH} (${if (isTablet) "Tablet" else "Phone"} Screen)", "native"),
            Triple("Medium", "${mediumW} × ${mediumH} (0.75x Scale)", "medium"),
            Triple("Low", "${lowW} × ${lowH} (0.5x Scale)", "low"),
            Triple("Custom", "Manually enter dimensions", "custom"),
        ).forEach { (title, subtitle, option) ->
            ResolutionCard(
                title = title,
                subtitle = subtitle,
                isSelected = selectedOption == option,
                onClick = { selectedOption = option },
            )
        }

        AnimatedVisibility(visible = selectedOption == "custom") {
            Column {
                Spacer(modifier = Modifier.height(10.dp))
                OutlinedTextField(
                    value = customWidthText,
                    onValueChange = { customWidthText = it },
                    label = { Text("Stream Width") },
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Color.White,
                        unfocusedTextColor = Color.White,
                        focusedLabelColor = Color.White.copy(alpha = 0.7f),
                        unfocusedLabelColor = Color.White.copy(alpha = 0.7f),
                        cursorColor = Color.White,
                        focusedBorderColor = AccentIndigo,
                        unfocusedBorderColor = BorderDark
                    )
                )
                Spacer(modifier = Modifier.height(10.dp))
                OutlinedTextField(
                    value = customHeightText,
                    onValueChange = { customHeightText = it },
                    label = { Text("Stream Height") },
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Color.White,
                        unfocusedTextColor = Color.White,
                        focusedLabelColor = Color.White.copy(alpha = 0.7f),
                        unfocusedLabelColor = Color.White.copy(alpha = 0.7f),
                        cursorColor = Color.White,
                        focusedBorderColor = AccentIndigo,
                        unfocusedBorderColor = BorderDark
                    )
                )
            }
        }

        Spacer(modifier = Modifier.height(spacingHeight))

        Button(
            onClick = {
                val finalW = when (selectedOption) {
                    "native" -> nativeW
                    "medium" -> mediumW
                    "low" -> lowW
                    else -> customWidthText.toIntOrNull() ?: 1280
                }
                val finalH = when (selectedOption) {
                    "native" -> nativeH
                    "medium" -> mediumH
                    "low" -> lowH
                    else -> customHeightText.toIntOrNull() ?: 800
                }
                onSave(finalW, finalH)
            },
            modifier = Modifier.fillMaxWidth().height(if (isTablet) 52.dp else 42.dp),
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(containerColor = AccentIndigo)
        ) {
            Text("SAVE & APPLY", fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
fun ReceiveScreen(
    hostIp: String,
    width: Int,
    height: Int,
    fps: Int,
    displayWidth: Int,
    displayHeight: Int,
    status: String,
    onBack: () -> Unit,
    onSurfaceCreated: (String, Surface, Int, Int, () -> Unit) -> Long,
    onSurfaceDestroyed: (Long) -> Unit,
    onSurfaceRenderTimeout: () -> Unit,
    onInputEvent: (android.view.MotionEvent, Float, Float) -> Unit
) {
    BackHandler(onBack = onBack)
    var surfaceKey by remember(hostIp, width, height, fps) { mutableStateOf(0) }
    var surfaceRecreateUsed by remember(hostIp, width, height, fps) { mutableStateOf(false) }

    LaunchedEffect(hostIp, width, height, fps) {
        delay(300)
        if (!surfaceRecreateUsed) {
            surfaceRecreateUsed = true
            surfaceKey += 1
            Log.w("ReceiveScreen", "Recreating SurfaceView once after initial attach")
        }
    }

    fun handleSurfaceStartupFailure() {
        if (!surfaceRecreateUsed) {
            surfaceRecreateUsed = true
            surfaceKey += 1
            Log.w("ReceiveScreen", "Recreating SurfaceView once after startup failure")
        } else {
            onSurfaceRenderTimeout()
        }
    }

    Box(
        modifier = Modifier.fillMaxSize().background(Color.Black),
        contentAlignment = Alignment.Center
    ) {
        
        
        Box(modifier = Modifier.aspectRatio(displayWidth.toFloat() / displayHeight.toFloat())) {
            key(surfaceKey) {
                StreamSurface(
                    modifier = Modifier.fillMaxSize(),
                    width = width,
                    height = height,
                    fps = fps,
                    hostIp = hostIp,
                    onSurfaceCreated = onSurfaceCreated,
                    onSurfaceDestroyed = onSurfaceDestroyed,
                    onSurfaceRenderTimeout = ::handleSurfaceStartupFailure,
                    onInputEvent = onInputEvent
                )
            }
        }

        
        if (status.isNotEmpty()) {
            Box(
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(24.dp)
                    .zIndex(1f)
                    .background(Color(0x88000000), RoundedCornerShape(6.dp))
                    .clickable { onBack() }
                    .padding(horizontal = 12.dp, vertical = 6.dp)
            ) {
                Text(text = status, color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
fun StreamSurface(
    modifier: Modifier,
    width: Int,
    height: Int,
    fps: Int,
    hostIp: String,
    onSurfaceCreated: (String, Surface, Int, Int, () -> Unit) -> Long,
    onSurfaceDestroyed: (Long) -> Unit,
    onSurfaceRenderTimeout: () -> Unit,
    onInputEvent: (android.view.MotionEvent, Float, Float) -> Unit
) {
    AndroidView(
        factory = { ctx ->
            AccessibleSurfaceView(ctx).apply {
                isClickable = true
                val streamCallback = object : SurfaceHolder.Callback {
                    private var surfaceGeneration = 0L
                    private var activeSurfaceWidth = 0
                    private var activeSurfaceHeight = 0
                    private var callbackSurfaceWidth = 0
                    private var callbackSurfaceHeight = 0
                    private var hasSurfaceEvent = false
                    private var hasSizedSurfaceChange = false
                    private var firstFrameRendered = false
                    private var watchdogRestartUsed = false
                    private var pendingForceRestart = false
                    private var readinessRetryStartedAt = 0L
                    private var lastReadinessBlockedLogAt = 0L
                    private var readinessRetry: Runnable? = null
                    private var firstFrameWatchdog: Runnable? = null

                    override fun surfaceCreated(holder: SurfaceHolder) {
                        hasSurfaceEvent = true
                        hasSizedSurfaceChange = false
                        callbackSurfaceWidth = 0
                        callbackSurfaceHeight = 0
                        applyFrameRateHint(holder.surface)
                        watchdogRestartUsed = false
                        scheduleSurfaceReadyChecks(reason = "surfaceCreated")
                    }

                    override fun surfaceChanged(holder: SurfaceHolder, format: Int, w: Int, h: Int) {
                        hasSurfaceEvent = true
                        callbackSurfaceWidth = w
                        callbackSurfaceHeight = h
                        hasSizedSurfaceChange = w > 0 && h > 0
                        applyFrameRateHint(holder.surface)
                        scheduleSurfaceReadyChecks(forceRestart = true, reason = "surfaceChanged")
                    }

                    override fun surfaceDestroyed(holder: SurfaceHolder) {
                        stopSurfaceReadyChecks()
                        stopFirstFrameWatchdog()
                        hasSurfaceEvent = false
                        hasSizedSurfaceChange = false
                        callbackSurfaceWidth = 0
                        callbackSurfaceHeight = 0
                        firstFrameRendered = false
                        watchdogRestartUsed = false
                        lastReadinessBlockedLogAt = 0L
                        stopActiveStream()
                    }

                    fun scheduleSurfaceReadyChecks(forceRestart: Boolean = false, reason: String = "layout") {
                        if (surfaceGeneration != 0L && !forceRestart) {
                            return
                        }
                        pendingForceRestart = pendingForceRestart || forceRestart
                        if (readinessRetry != null) {
                            return
                        }
                        readinessRetryStartedAt = SystemClock.uptimeMillis()
                        lastReadinessBlockedLogAt = 0L
                        Log.i("StreamSurface", "Surface readiness retry started: $reason")
                        val retry = object : Runnable {
                            override fun run() {
                                if (maybeStartOrRestartStream(pendingForceRestart)) {
                                    stopSurfaceReadyChecks()
                                    return
                                }
                                val elapsed = SystemClock.uptimeMillis() - readinessRetryStartedAt
                                if (elapsed >= SURFACE_READY_RETRY_TIMEOUT_MS) {
                                    Log.w("StreamSurface", "Surface readiness timeout after ${elapsed}ms")
                                    stopSurfaceReadyChecks()
                                    onSurfaceRenderTimeout()
                                    return
                                }
                                postDelayed(this, SURFACE_READY_RETRY_INTERVAL_MS)
                            }
                        }
                        readinessRetry = retry
                        post(retry)
                    }

                    private fun maybeStartOrRestartStream(forceRestart: Boolean): Boolean {
                        val actualWidth = callbackSurfaceWidth
                        val actualHeight = callbackSurfaceHeight
                        val blockedReason = surfaceBlockedReason(actualWidth, actualHeight)
                        if (blockedReason != null) {
                            logReadinessBlocked(blockedReason)
                            return false
                        }
                        val surface = holder.surface
                        applyFrameRateHint(surface)
                        if (surfaceGeneration != 0L && !forceRestart) {
                            if (actualWidth == activeSurfaceWidth && actualHeight == activeSurfaceHeight) {
                                return true
                            }
                        }
                        if (surfaceGeneration != 0L) {
                            stopActiveStream()
                        }
                        activeSurfaceWidth = actualWidth
                        activeSurfaceHeight = actualHeight
                        firstFrameRendered = false
                        surfaceGeneration = onSurfaceCreated(hostIp, surface, width, height) {
                            firstFrameRendered = true
                        }
                        Log.i("StreamSurface", "Starting stream after surface readiness: ${actualWidth}x${actualHeight}")
                        scheduleFirstFrameWatchdog(surfaceGeneration, actualWidth, actualHeight)
                        return true
                    }

                    private fun surfaceBlockedReason(surfaceWidth: Int, surfaceHeight: Int): String? {
                        if (!hasSurfaceEvent) return "waiting for surface callback"
                        if (!hasSizedSurfaceChange) return "waiting for surfaceChanged size"
                        if (!isAttachedToWindow) return "view not attached"
                        if (windowVisibility != android.view.View.VISIBLE) return "view not visible"
                        if (surfaceWidth <= 0 || surfaceHeight <= 0) return "surface not measured"
                        if (!holder.surface.isValid) return "surface not valid"
                        return null
                    }

                    private fun logReadinessBlocked(reason: String) {
                        val now = SystemClock.uptimeMillis()
                        if (now - lastReadinessBlockedLogAt < SURFACE_READY_BLOCKED_LOG_INTERVAL_MS) {
                            return
                        }
                        lastReadinessBlockedLogAt = now
                        Log.d("StreamSurface", "Surface readiness blocked: $reason")
                    }

                    private fun scheduleFirstFrameWatchdog(generation: Long, surfaceWidth: Int, surfaceHeight: Int) {
                        stopFirstFrameWatchdog()
                        val watchdog = Runnable {
                            firstFrameWatchdog = null
                            if (generation != surfaceGeneration || firstFrameRendered) {
                                return@Runnable
                            }
                            if (!watchdogRestartUsed) {
                                watchdogRestartUsed = true
                                callbackSurfaceWidth = surfaceWidth
                                callbackSurfaceHeight = surfaceHeight
                                maybeStartOrRestartStream(forceRestart = true)
                            } else {
                                onSurfaceRenderTimeout()
                            }
                        }
                        firstFrameWatchdog = watchdog
                        postDelayed(watchdog, 2000L)
                    }

                    private fun stopSurfaceReadyChecks() {
                        readinessRetry?.let { removeCallbacks(it) }
                        readinessRetry = null
                        pendingForceRestart = false
                        readinessRetryStartedAt = 0L
                    }

                    private fun stopFirstFrameWatchdog() {
                        firstFrameWatchdog?.let { removeCallbacks(it) }
                        firstFrameWatchdog = null
                    }

                    fun release() {
                        stopSurfaceReadyChecks()
                        stopFirstFrameWatchdog()
                        hasSurfaceEvent = false
                        hasSizedSurfaceChange = false
                        callbackSurfaceWidth = 0
                        callbackSurfaceHeight = 0
                        stopActiveStream()
                    }

                    private fun stopActiveStream() {
                        if (surfaceGeneration != 0L) {
                            onSurfaceDestroyed(surfaceGeneration)
                            surfaceGeneration = 0L
                        }
                        activeSurfaceWidth = 0
                        activeSurfaceHeight = 0
                    }

                    private fun applyFrameRateHint(surface: Surface?) {
                        if (surface == null || !surface.isValid || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                            return
                        }
                        val frameRate = fps.coerceIn(MIN_STREAM_FPS, MAX_STREAM_FPS).toFloat()
                        try {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                                surface.setFrameRate(
                                    frameRate,
                                    Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE,
                                    Surface.CHANGE_FRAME_RATE_ALWAYS
                                )
                            } else {
                                surface.setFrameRate(frameRate, Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE)
                            }
                        } catch (e: Exception) {
                            Log.w("StreamSurface", "Frame rate hint failed: ${e.message}")
                        }
                    }

                }
                setStreamCallback(streamCallback) { streamCallback.release() }
                addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
                    streamCallback.scheduleSurfaceReadyChecks(reason = "layout")
                }
                streamCallback.scheduleSurfaceReadyChecks(reason = "factory")
                fun requestLowLatencyInput(event: android.view.MotionEvent) {
                    when (event.actionMasked) {
                        android.view.MotionEvent.ACTION_DOWN,
                        android.view.MotionEvent.ACTION_MOVE,
                        android.view.MotionEvent.ACTION_HOVER_ENTER,
                        android.view.MotionEvent.ACTION_HOVER_MOVE -> requestUnbufferedDispatch(event)
                    }
                }
                setOnTouchListener { v, event ->
                    if (event.action == android.view.MotionEvent.ACTION_DOWN) v.performClick()
                    requestLowLatencyInput(event)
                    onInputEvent(event, v.width.toFloat(), v.height.toFloat())
                    true
                }
                setOnHoverListener { v, event ->
                    requestLowLatencyInput(event)
                    onInputEvent(event, v.width.toFloat(), v.height.toFloat())
                    true
                }
            }
        },
        modifier = modifier,
        onRelease = { view ->
            view.releaseStreamCallback()
        }
    )
}

private class AccessibleSurfaceView(context: Context) : SurfaceView(context) {
    private var streamCallback: SurfaceHolder.Callback? = null
    private var releaseStreamCallback: (() -> Unit)? = null

    fun setStreamCallback(callback: SurfaceHolder.Callback, onRelease: () -> Unit) {
        streamCallback = callback
        releaseStreamCallback = onRelease
        holder.addCallback(callback)
    }

    fun releaseStreamCallback() {
        streamCallback?.let { holder.removeCallback(it) }
        streamCallback = null
        releaseStreamCallback?.invoke()
        releaseStreamCallback = null
        setOnTouchListener(null)
        setOnHoverListener(null)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }
}
