package com.example.monitorize

import android.content.Context
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
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.zIndex
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch


val BackgroundDark = Color(0xFF121214) 
val CardDark       = Color(0xFF1C1C1E)
val BorderDark     = Color(0xFF2C2C2E)
val AccentIndigo   = Color(0xFF6366F1)
val GreenAccent    = Color(0xFF10B981)
val TextPrimary    = Color(0xFFF1F5F9)
val TextSecondary  = Color(0xFF94A3B8)
val TextMuted      = Color(0xFF475569)

enum class Screen { Home, Receive }

class MainActivity : ComponentActivity() {

    private var decoder: H264Decoder? = null
    private var receiver: StreamReceiver? = null
    private var inputSender: InputEventSender? = null
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

        window.setBackgroundDrawableResource(android.R.color.black)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContent {
            var currentScreen by remember { mutableStateOf(Screen.Home) }
            var isSettingsOpen by remember { mutableStateOf(false) }

            var width by remember { mutableIntStateOf(prefs.getInt("width", 1280)) }
            var height by remember { mutableIntStateOf(prefs.getInt("height", 800)) }
            var fps by remember { mutableIntStateOf(prefs.getInt("fps", 60)) }
            
            var selectedDevice by remember { mutableStateOf<DiscoveredDevice?>(null) }
            var disconnectionMessage by remember { mutableStateOf<String?>(
                if (intent.getBooleanExtra("SHOW_DISCONNECTED", false)) "Disconnected" else null
            ) }
            
            val coroutineScope = rememberCoroutineScope()
            val context = androidx.compose.ui.platform.LocalContext.current

            
            if (disconnectionMessage != null) {
                LaunchedEffect(disconnectionMessage) {
                    kotlinx.coroutines.delay(5000)
                    disconnectionMessage = null
                }
            }

            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize(), color = BackgroundDark) {
                    Box(modifier = Modifier.fillMaxSize()) {
                        when (currentScreen) {
                            Screen.Home -> {
                                HomeScreen(
                                    devices = discovery.devices,
                                    onDeviceSelected = { device ->
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
                                    fps = fps,
                                    status = status.value,
                                    onBack = {
                                        coroutineScope.launch {
                                            stopStream()
                                        }
                                        triggerAppRestart(showDisconnected = true)
                                    },
                                    onSurfaceCreated = { ip, surface, w, h, f ->
                                        coroutineScope.launch {
                                            
                                            kotlinx.coroutines.delay(400)
                                            startStream(ip, surface, w, h, f) {
                                                runOnUiThread {
                                                    coroutineScope.launch { stopStream() }
                                                    triggerAppRestart(showDisconnected = true)
                                                }
                                            }
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
                            Box(modifier = Modifier.fillMaxHeight().fillMaxWidth(0.45f).background(CardDark).border(1.dp, BorderDark)) {
                                SettingsPanel(
                                    initialWidth = width,
                                    initialHeight = height,
                                    initialFps = fps,
                                    onSave = { w, h, f ->
                                        if (w != width || h != height || f != fps) {
                                            prefs.edit().putInt("width", w).putInt("height", h).putInt("fps", f).apply()
                                            isSettingsOpen = false
                                            triggerAppRestart(showDisconnected = false)
                                        } else {
                                            isSettingsOpen = false
                                        }
                                    },
                                    onClose = { isSettingsOpen = false }
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
                                        color = TextPrimary,
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

    private fun applyImmersiveMode() {
        val windowInsetsController = WindowCompat.getInsetsController(window, window.decorView)
        windowInsetsController.hide(WindowInsetsCompat.Type.systemBars())
        windowInsetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun startStream(hostIp: String, surface: Surface, width: Int, height: Int, fps: Int, onDisconnect: () -> Unit) {
        val d = H264Decoder(surface)
        decoder = d
        receiver = StreamReceiver(d, width, height, fps, hostIp.takeIf { it.isNotBlank() }).also {
            it.onStatusChange = { msg -> runOnUiThread { status.value = msg } }
            it.onDisconnect = onDisconnect
            it.start()
        }
        val metrics = resources.displayMetrics
        inputSender = InputEventSender(hostIp.takeIf { it.isNotBlank() }).also { it.start() }
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
    LaunchedEffect(Unit) {
        onStartDiscovery()
    }

    Box(modifier = Modifier.fillMaxSize()) {
        
        IconButton(
            onClick = onSettingsToggle,
            modifier = Modifier.align(Alignment.TopEnd).padding(24.dp).size(48.dp).background(CardDark, CircleShape)
        ) {
            Icon(Icons.Default.Settings, contentDescription = "Settings", tint = TextPrimary)
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 48.dp)
        ) {
            Spacer(modifier = Modifier.height(100.dp))
            
            
            Text(
                "DEVICES:",
                fontSize = 11.sp,
                color = TextMuted,
                fontWeight = FontWeight.Bold,
                letterSpacing = 2.sp
            )
            Spacer(modifier = Modifier.height(24.dp))

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
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding = PaddingValues(bottom = 16.dp)
                ) {
                    items(devices) { device ->
                        DeviceItem(device = device, onClick = { onDeviceSelected(device) })
                    }
                }
            }
            
            
            var manualIp by remember { mutableStateOf("") }
            Spacer(modifier = Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth().padding(bottom = 32.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = manualIp,
                    onValueChange = { manualIp = it },
                    placeholder = { Text("Enter IP manually", color = TextMuted) },
                    modifier = Modifier.weight(1f),
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
                            onDeviceSelected(DiscoveredDevice(name = "Manual WiFi", ip = manualIp.trim(), port = 7110, isUsb = false))
                        }
                    },
                    modifier = Modifier.height(56.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = GreenAccent)
                ) {
                    Text("Connect", fontWeight = FontWeight.Bold, color = Color.Black)
                }
            }
        }
    }
}

@Composable
fun DeviceItem(device: DiscoveredDevice, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(CardDark)
            .border(1.dp, BorderDark, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(18.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            
            Text(
                device.name, 
                color = TextPrimary, 
                fontWeight = FontWeight.Bold, 
                fontSize = 18.sp
            )
            
            Text(
                device.ip, 
                color = TextSecondary, 
                fontSize = 13.sp,
                modifier = Modifier.padding(top = 2.dp)
            )
        }
        
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(4.dp))
                .background(if (device.isUsb) AccentIndigo.copy(alpha = 0.2f) else GreenAccent.copy(alpha = 0.2f))
                .padding(horizontal = 10.dp, vertical = 4.dp)
        ) {
            Text(
                text = if (device.isUsb) "usb" else "wifi",
                color = if (device.isUsb) AccentIndigo else GreenAccent,
                fontSize = 10.sp,
                fontWeight = FontWeight.ExtraBold
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsPanel(
    initialWidth: Int,
    initialHeight: Int,
    initialFps: Int,
    onSave: (Int, Int, Int) -> Unit,
    onClose: () -> Unit
) {
    var wText by remember { mutableStateOf(initialWidth.toString()) }
    var hText by remember { mutableStateOf(initialHeight.toString()) }
    var fText by remember { mutableStateOf(initialFps.toString()) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(28.dp)
            .verticalScroll(rememberScrollState())
    ) {
        Text("Current Settings", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = TextPrimary)
        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = wText, onValueChange = { wText = it },
            label = { Text("Stream Width") },
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = AccentIndigo, unfocusedBorderColor = BorderDark)
        )
        Spacer(modifier = Modifier.height(16.dp))
        OutlinedTextField(
            value = hText, onValueChange = { hText = it },
            label = { Text("Stream Height") },
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = AccentIndigo, unfocusedBorderColor = BorderDark)
        )
        Spacer(modifier = Modifier.height(16.dp))
        OutlinedTextField(
            value = fText, onValueChange = { fText = it },
            label = { Text("Stream FPS") },
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = AccentIndigo, unfocusedBorderColor = BorderDark)
        )

        Spacer(modifier = Modifier.weight(1f))
        Spacer(modifier = Modifier.height(40.dp))

        Button(
            onClick = {
                onSave(wText.toIntOrNull() ?: 1280, hText.toIntOrNull() ?: 800, fText.toIntOrNull() ?: 60)
            },
            modifier = Modifier.fillMaxWidth().height(52.dp),
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(containerColor = AccentIndigo)
        ) { Text("SAVE & APPLY", fontWeight = FontWeight.Bold) }
        
        TextButton(onClick = onClose, modifier = Modifier.fillMaxWidth()) {
            Text("DISCARD", color = TextSecondary)
        }
    }
}

@Composable
fun ReceiveScreen(
    hostIp: String,
    width: Int,
    height: Int,
    fps: Int,
    status: String,
    onBack: () -> Unit,
    onSurfaceCreated: (String, Surface, Int, Int, Int) -> Unit,
    onSurfaceDestroyed: () -> Unit,
    onInputEvent: (android.view.MotionEvent, Float, Float) -> Unit
) {
    BackHandler(onBack = onBack)
    Box(
        modifier = Modifier.fillMaxSize().background(Color.Black),
        contentAlignment = Alignment.Center
    ) {
        
        
        Box(modifier = Modifier.aspectRatio(width.toFloat() / height.toFloat())) {
            StreamSurface(
                modifier = Modifier.fillMaxSize(),
                onSurfaceReady = { sv ->
                    sv.holder.addCallback(object : SurfaceHolder.Callback {
                        override fun surfaceCreated(holder: SurfaceHolder) {
                            holder.setFixedSize(width, height)
                            onSurfaceCreated(hostIp, holder.surface, width, height, fps)
                        }
                        override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, ht: Int) {}
                        override fun surfaceDestroyed(h: SurfaceHolder) { onSurfaceDestroyed() }
                    })
                }
            )

            
            AndroidView(
                factory = { ctx ->
                    android.view.View(ctx).apply {
                        layoutParams = android.view.ViewGroup.LayoutParams(
                            android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                            android.view.ViewGroup.LayoutParams.MATCH_PARENT
                        )
                        setBackgroundColor(android.graphics.Color.TRANSPARENT)
                        isClickable = true
                        setOnTouchListener { v, event -> 
                            if (event.action == android.view.MotionEvent.ACTION_DOWN) v.performClick()
                            onInputEvent(event, v.width.toFloat(), v.height.toFloat())
                            true 
                        }
                        setOnHoverListener { v, event -> onInputEvent(event, v.width.toFloat(), v.height.toFloat()); true }
                    }
                },
                modifier = Modifier.fillMaxSize().zIndex(2f)
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
                Text(text = status, color = GreenAccent, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
fun StreamSurface(modifier: Modifier, onSurfaceReady: (android.view.SurfaceView) -> Unit) {
    AndroidView(
        factory = { ctx -> android.view.SurfaceView(ctx).also { onSurfaceReady(it) } },
        modifier = modifier
    )
}



@Preview(showBackground = true, widthDp = 800, heightDp = 480)
@Composable
fun HomeScreenPreview() {
    val mockDevices = listOf(
        DiscoveredDevice("Main Desktop", "192.168.1.100", 7110, false),
        DiscoveredDevice("Local PC (USB)", "127.0.0.1", 7110, true)
    )
    MaterialTheme(colorScheme = darkColorScheme()) {
        Surface(color = BackgroundDark) {
            HomeScreen(devices = mockDevices, onDeviceSelected = {}, onSettingsToggle = {})
        }
    }
}
