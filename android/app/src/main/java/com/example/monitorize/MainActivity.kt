package com.example.monitorize

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import android.os.Build
import android.os.Bundle
import android.view.Surface
import android.view.SurfaceHolder
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import com.example.monitorize.ui.theme.MonitorizeTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch








val BackgroundDark = Color(0xFF1B1E24)
val CardDark       = Color(0xFF232831)
val BorderDark     = Color(0xFF343B46)
val AccentIndigo   = Color(0xFF3DAEE9)
val GreenAccent    = Color(0xFF2F6F95)
val TextPrimary    = Color(0xFFEFF0F1)
val TextSecondary  = Color(0xFFC7D0D9)
val TextMuted      = Color(0xFF8F9AA6)

enum class Screen { Home, Receive }

class MainActivity : ComponentActivity() {

    private var decoder: H264Decoder? = null
    private var receiver: StreamReceiver? = null
    private var inputSender: InputEventSender? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private val status = mutableStateOf("")
    private lateinit var discovery: DeviceDiscovery

    private val prefs by lazy { getSharedPreferences("monitorize_prefs", Context.MODE_PRIVATE) }

    private fun triggerAppRestart(showDisconnected: Boolean = false) {
        runOnUiThread {
            val intent = android.content.Intent(this@MainActivity, MainActivity::class.java).apply {
                addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TASK)
                if (showDisconnected) {
                    putExtra("SHOW_DISCONNECTED", true)
                }
            }
            startActivity(intent)
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        discovery = DeviceDiscovery(this)

        
        WindowCompat.setDecorFitsSystemWindows(window, false)
        applyImmersiveMode()

        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(android.graphics.Color.parseColor("#1B1E24")))
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        
        WindowInsetsControllerCompat(window, window.decorView).isAppearanceLightStatusBars = false

        setContent {
            val configuration = LocalConfiguration.current
            val isTablet = configuration.smallestScreenWidthDp >= 600
            val isLandscapeMobile = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE && !isTablet

            var currentScreen by remember { mutableStateOf(Screen.Home) }
            var isSettingsOpen by remember { mutableStateOf(false) }

            var width by remember { mutableIntStateOf(prefs.getInt("width", 1280)) }
            var height by remember { mutableIntStateOf(prefs.getInt("height", 800)) }
            var decodedWidth by remember { mutableIntStateOf(width) }
            var decodedHeight by remember { mutableIntStateOf(height) }
            
            var selectedDevice by remember { mutableStateOf<DiscoveredDevice?>(null) }
            var disconnectionMessage by remember { mutableStateOf<String?>(
                if (intent.getBooleanExtra("SHOW_DISCONNECTED", false)) "Disconnected" else null
            ) }
            
            val coroutineScope = rememberCoroutineScope()

            
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
                                    onDeviceSelected = { device ->
                                        discovery.stopDiscovery()
                                        decodedWidth = width
                                        decodedHeight = height
                                        selectedDevice = device
                                        currentScreen = Screen.Receive
                                        disconnectionMessage = null 
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
                                    displayWidth = decodedWidth,
                                    displayHeight = decodedHeight,
                                    status = status.value,
                                    onBack = {
                                        coroutineScope.launch {
                                            stopStream()
                                        }
                                        triggerAppRestart(showDisconnected = true)
                                    },
                                    onSurfaceCreated = { ip, surface, w, h ->
                                        coroutineScope.launch {
                                            
                                            delay(400)
                                            val port = selectedDevice?.port ?: 7110
                                            startStream(
                                                ip, port, surface, w, h,
                                                onDecodedSize = { decodedW, decodedH ->
                                                    decodedWidth = decodedW
                                                    decodedHeight = decodedH
                                                },
                                                onDisconnect = {
                                                    runOnUiThread {
                                                        coroutineScope.launch { stopStream() }
                                                        triggerAppRestart(showDisconnected = true)
                                                    }
                                                }
                                            )
                                        }
                                    },
                                    onSurfaceDestroyed = { 
                                        coroutineScope.launch { stopStream() } 
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
                            val panelWidthFraction = if (isTablet || isLandscapeMobile) 0.45f else 0.85f

                            Box(modifier = Modifier.fillMaxHeight().fillMaxWidth(panelWidthFraction).background(CardDark).border(1.dp, BorderDark)) {
                                SettingsPanel(
                                    initialWidth = width,
                                    initialHeight = height,
                                    onSave = { w, h ->
                                        if (w != width || h != height) {
                                            prefs.edit().putInt("width", w).putInt("height", h).apply()
                                            isSettingsOpen = false
                                            triggerAppRestart(showDisconnected = false)
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
    }

    override fun onDestroy() {
        super.onDestroy()
        try {
            if (wifiLock?.isHeld == true) {
                wifiLock?.release()
            }
        } catch (_: Exception) {}
        wifiLock = null
    }

    private fun applyImmersiveMode() {
        val windowInsetsController = WindowCompat.getInsetsController(window, window.decorView)
        windowInsetsController.hide(WindowInsetsCompat.Type.systemBars())
        windowInsetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun startStream(
        hostIp: String,
        hostPort: Int,
        surface: Surface,
        width: Int,
        height: Int,
        onDecodedSize: (Int, Int) -> Unit,
        onDisconnect: () -> Unit
    ) {
        
        try {
            val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val lockType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = wifiManager.createWifiLock(lockType, "Monitorize:LowLatencyLock").apply {
                setReferenceCounted(false)
                acquire()
            }
            Log.i("MainActivity", "Acquired low latency Wi-Fi lock")
        } catch (e: Exception) {
            Log.e("MainActivity", "Failed to acquire Wi-Fi lock: ${e.message}")
        }

        val d = H264Decoder(surface) { decodedWidth, decodedHeight ->
            runOnUiThread { onDecodedSize(decodedWidth, decodedHeight) }
        }
        decoder = d
        receiver = StreamReceiver(d, width, height, hostIp.takeIf { it.isNotBlank() }, hostPort).also {
            it.onStatusChange = { msg -> runOnUiThread { status.value = msg } }
            it.onDisconnect = onDisconnect
            it.start()
        }
        val metrics = resources.displayMetrics
        inputSender = InputEventSender(hostIp.takeIf { it.isNotBlank() }, hostPort).also { it.start() }
    }

    private var isStopping = false

    private suspend fun stopStream() {
        if (isStopping) return
        isStopping = true
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            receiver?.stop(); receiver = null
            decoder?.release(); decoder = null
            inputSender?.stop(); inputSender = null
        }
        
        try {
            if (wifiLock?.isHeld == true) {
                wifiLock?.release()
            }
            Log.i("MainActivity", "Released low latency Wi-Fi lock")
        } catch (e: Exception) {
            Log.e("MainActivity", "Failed to release Wi-Fi lock: ${e.message}")
        }
        wifiLock = null
        isStopping = false
    }
}



@Composable
fun HomeScreen(
    devices: List<DiscoveredDevice>,
    onDeviceSelected: (DiscoveredDevice) -> Unit,
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
        isTablet -> 100.dp
        isLandscapeMobile -> 40.dp
        else -> 60.dp
    }
    val devicesSpacing = if (isLandscapeMobile) 10.dp else 14.dp
    val settingsButtonPadding = if (isLandscapeMobile) 16.dp else 24.dp
    val manualRowPadding = if (isLandscapeMobile) 16.dp else 32.dp
    val manualSpacerHeight = if (isLandscapeMobile) 12.dp else 16.dp
    val manualFieldHeight = if (isLandscapeMobile) 48.dp else 56.dp

    LaunchedEffect(Unit) {
        onStartDiscovery()
    }

    Box(modifier = Modifier.fillMaxSize()) {
        if (isTablet) {
            IconButton(
                onClick = onSettingsToggle,
                modifier = Modifier.align(Alignment.TopEnd).padding(settingsButtonPadding).size(48.dp).background(CardDark, CircleShape)
            ) {
                Icon(Icons.Default.Settings, contentDescription = "Settings", tint = Color.White)
            }
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = horizontalPadding)
        ) {
            Spacer(modifier = Modifier.height(topSpacerHeight))
            
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
            Spacer(modifier = Modifier.height(devicesSpacing))

            if (devices.isEmpty()) {
                Box(modifier = Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                    Text(
                        "Searching for devices...\n(Check USB or Wi-Fi connection)",
                        color = TextMuted,
                        fontSize = 14.sp,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(devicesSpacing),
                    contentPadding = PaddingValues(bottom = 16.dp)
                ) {
                    items(devices) { device ->
                        DeviceItem(device = device, onClick = { onDeviceSelected(device) })
                    }
                }
            }
            
            val context = androidx.compose.ui.platform.LocalContext.current
            val prefs = remember { context.getSharedPreferences("monitorize_prefs", android.content.Context.MODE_PRIVATE) }
            var manualIp by remember { mutableStateOf(prefs.getString("manual_ip", "") ?: "") }
            var manualPort by remember { mutableStateOf(prefs.getString("manual_port", "7110") ?: "7110") }
            Spacer(modifier = Modifier.height(manualSpacerHeight))
            Row(
                modifier = Modifier.fillMaxWidth().padding(bottom = manualRowPadding),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = manualIp,
                    onValueChange = { manualIp = it },
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
                    onValueChange = { manualPort = it },
                    placeholder = { Text("Port", color = TextMuted) },
                    modifier = Modifier.weight(0.8f).height(manualFieldHeight),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = AccentIndigo,
                        unfocusedBorderColor = BorderDark
                    )
                )
                Spacer(modifier = Modifier.width(12.dp))
                Button(
                    onClick = {
                        if (manualIp.isNotBlank()) {
                            val ip = manualIp.trim()
                            val port = manualPort.trim().toIntOrNull() ?: 7110
                            prefs.edit().apply {
                                putString("manual_ip", ip)
                                putString("manual_port", manualPort.trim())
                                apply()
                            }
                            onDeviceSelected(DiscoveredDevice(name = "Manual WiFi", ip = ip, port = port, isUsb = false))
                        }
                    },
                    modifier = Modifier.height(manualFieldHeight),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = GreenAccent)
                ) {
                    Text("Connect", fontWeight = FontWeight.Bold, color = Color.White)
                }
            }
        }
    }
}

@Composable
fun DeviceItem(device: DiscoveredDevice, onClick: () -> Unit) {
    val configuration = LocalConfiguration.current
    val isTablet = configuration.smallestScreenWidthDp >= 600
    val isLandscapeMobile = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE && !isTablet

    val verticalPadding = if (isLandscapeMobile) 14.dp else 18.dp
    val horizontalPadding = if (isLandscapeMobile) 16.dp else 18.dp
    val titleFontSize = if (isLandscapeMobile) 16.sp else 18.sp

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(CardDark)
            .border(1.dp, BorderDark, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
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
                device.ip, 
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
    val panelPadding = if (isTablet) 28.dp else 20.dp
    val titleSize = if (isTablet) 22.sp else 18.sp
    val spacingHeight = if (isTablet) 24.dp else 16.dp

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(panelPadding)
            .verticalScroll(rememberScrollState())
    ) {
        Text("Resolution Settings", fontSize = titleSize, fontWeight = FontWeight.Bold, color = Color.White)
        Spacer(modifier = Modifier.height(spacingHeight))

        ResolutionCard(
            title = "Native",
            subtitle = "${nativeW} × ${nativeH} (${if (isTablet) "Tablet" else "Phone"} Screen)",
            isSelected = selectedOption == "native",
            onClick = { selectedOption = "native" }
        )

        ResolutionCard(
            title = "Medium",
            subtitle = "${mediumW} × ${mediumH} (0.75x Scale)",
            isSelected = selectedOption == "medium",
            onClick = { selectedOption = "medium" }
        )

        ResolutionCard(
            title = "Low",
            subtitle = "${lowW} × ${lowH} (0.5x Scale)",
            isSelected = selectedOption == "low",
            onClick = { selectedOption = "low" }
        )

        ResolutionCard(
            title = "Custom",
            subtitle = "Manually enter dimensions",
            isSelected = selectedOption == "custom",
            onClick = { selectedOption = "custom" }
        )

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
    displayWidth: Int,
    displayHeight: Int,
    status: String,
    onBack: () -> Unit,
    onSurfaceCreated: (String, Surface, Int, Int) -> Unit,
    onSurfaceDestroyed: () -> Unit,
    onInputEvent: (android.view.MotionEvent, Float, Float) -> Unit
) {
    BackHandler(onBack = onBack)
    Box(
        modifier = Modifier.fillMaxSize().background(Color.Black),
        contentAlignment = Alignment.Center
    ) {
        
        
        Box(modifier = Modifier.aspectRatio(displayWidth.toFloat() / displayHeight.toFloat())) {
            StreamSurface(
                modifier = Modifier.fillMaxSize(),
                width = width,
                height = height,
                hostIp = hostIp,
                onSurfaceCreated = onSurfaceCreated,
                onSurfaceDestroyed = onSurfaceDestroyed,
                onInputEvent = onInputEvent
            )
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
    hostIp: String,
    onSurfaceCreated: (String, Surface, Int, Int) -> Unit,
    onSurfaceDestroyed: () -> Unit,
    onInputEvent: (android.view.MotionEvent, Float, Float) -> Unit
) {
    AndroidView(
        factory = { ctx ->
            android.view.SurfaceView(ctx).apply {
                isClickable = true
                holder.addCallback(object : SurfaceHolder.Callback {
                    override fun surfaceCreated(holder: SurfaceHolder) {
                        onSurfaceCreated(hostIp, holder.surface, width, height)
                    }
                    override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, ht: Int) {}
                    override fun surfaceDestroyed(h: SurfaceHolder) { onSurfaceDestroyed() }
                })
                setOnTouchListener { v, event ->
                    if (event.action == android.view.MotionEvent.ACTION_DOWN) v.performClick()
                    onInputEvent(event, v.width.toFloat(), v.height.toFloat())
                    true
                }
                setOnHoverListener { v, event ->
                    onInputEvent(event, v.width.toFloat(), v.height.toFloat())
                    true
                }
            }
        },
        modifier = modifier
    )
}
