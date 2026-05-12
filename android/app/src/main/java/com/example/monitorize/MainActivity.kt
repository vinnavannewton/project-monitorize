package com.example.monitorize

import android.content.Context
import android.os.Bundle
import android.view.Surface
import android.view.SurfaceHolder
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

enum class Screen { Home, Settings, Receive }

class MainActivity : ComponentActivity() {

    private var decoder: H264Decoder? = null
    private var receiver: StreamReceiver? = null
    private val status = mutableStateOf("Ready")

    private val prefs by lazy { getSharedPreferences("monitorize_prefs", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.setBackgroundDrawableResource(android.R.color.black)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContent {
            var currentScreen by remember { mutableStateOf(Screen.Home) }

            // Load saved settings
            var width by remember { mutableIntStateOf(prefs.getInt("width", 1280)) }
            var height by remember { mutableIntStateOf(prefs.getInt("height", 800)) }
            var fps by remember { mutableIntStateOf(prefs.getInt("fps", 60)) }

            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) {
                    when (currentScreen) {
                        Screen.Home -> HomeScreen(
                            onReceiveClick = { currentScreen = Screen.Receive },
                            onSettingsClick = { currentScreen = Screen.Settings }
                        )
                        Screen.Settings -> SettingsScreen(
                            initialWidth = width,
                            initialHeight = height,
                            initialFps = fps,
                            onSave = { w, h, f ->
                                width = w
                                height = h
                                fps = f
                                prefs.edit().putInt("width", w).putInt("height", h).putInt("fps", f).apply()
                                currentScreen = Screen.Home
                            },
                            onBack = { currentScreen = Screen.Home }
                        )
                        Screen.Receive -> {
                            // Apply Fullscreen only in Receive mode
                            val windowInsetsController = WindowCompat.getInsetsController(window, window.decorView)
                            windowInsetsController.hide(WindowInsetsCompat.Type.systemBars())
                            windowInsetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                            
                            ReceiveScreen(
                                width = width,
                                height = height,
                                fps = fps,
                                status = status.value,
                                onBack = {
                                    stopStream()
                                    // Restore status bars
                                    windowInsetsController.show(WindowInsetsCompat.Type.systemBars())
                                    currentScreen = Screen.Home
                                }
                            )
                        }
                    }
                }
            }
        }
    }

    @Composable
    fun HomeScreen(onReceiveClick: () -> Unit, onSettingsClick: () -> Unit) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("Monitorize", fontSize = 48.sp, fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(48.dp))
            Button(
                onClick = onReceiveClick,
                modifier = Modifier.width(240.dp).height(64.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2E7D32))
            ) {
                Text("RECEIVE STREAM", fontSize = 18.sp)
            }
            Spacer(modifier = Modifier.height(16.dp))
            Button(
                onClick = onSettingsClick,
                modifier = Modifier.width(240.dp).height(64.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF455A64))
            ) {
                Text("SETTINGS", fontSize = 18.sp)
            }
        }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    fun SettingsScreen(
        initialWidth: Int,
        initialHeight: Int,
        initialFps: Int,
        onSave: (Int, Int, Int) -> Unit,
        onBack: () -> Unit
    ) {
        val resolutions = listOf(
            "1280x720", "1280x800", "1920x1080", "1920x1200",
            "2560x1440", "2560x1600", "3840x2160"
        )
        val fpsOptions = listOf(30, 60, 90, 120)

        var selectedRes by remember { mutableStateOf("${initialWidth}x${initialHeight}") }
        var selectedFps by remember { mutableIntStateOf(initialFps) }

        var resExpanded by remember { mutableStateOf(false) }
        var fpsExpanded by remember { mutableStateOf(false) }

        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp).verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("Settings", fontSize = 32.sp, fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(32.dp))

            Text("⚠️ WARNING: The Resolution and FPS set here MUST EXACTLY MATCH the settings in the Linux desktop app, or the stream will crash/corrupt!",
                color = Color.Yellow, fontWeight = FontWeight.Bold)

            Spacer(modifier = Modifier.height(32.dp))

            // Resolution Dropdown
            Text("Resolution", color = Color.Gray)
            ExposedDropdownMenuBox(
                expanded = resExpanded,
                onExpandedChange = { resExpanded = !resExpanded }
            ) {
                TextField(
                    value = selectedRes,
                    onValueChange = {},
                    readOnly = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = resExpanded) },
                    modifier = Modifier.menuAnchor()
                )
                ExposedDropdownMenu(expanded = resExpanded, onDismissRequest = { resExpanded = false }) {
                    resolutions.forEach { res ->
                        DropdownMenuItem(
                            text = { Text(res) },
                            onClick = { selectedRes = res; resExpanded = false }
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // FPS Dropdown
            Text("FPS", color = Color.Gray)
            ExposedDropdownMenuBox(
                expanded = fpsExpanded,
                onExpandedChange = { fpsExpanded = !fpsExpanded }
            ) {
                TextField(
                    value = selectedFps.toString(),
                    onValueChange = {},
                    readOnly = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = fpsExpanded) },
                    modifier = Modifier.menuAnchor()
                )
                ExposedDropdownMenu(expanded = fpsExpanded, onDismissRequest = { fpsExpanded = false }) {
                    fpsOptions.forEach { f ->
                        DropdownMenuItem(
                            text = { Text(f.toString()) },
                            onClick = { selectedFps = f; fpsExpanded = false }
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(48.dp))

            Button(
                onClick = {
                    val parts = selectedRes.split("x")
                    onSave(parts[0].toInt(), parts[1].toInt(), selectedFps)
                },
                modifier = Modifier.fillMaxWidth().height(56.dp)
            ) {
                Text("SAVE & BACK")
            }
            TextButton(onClick = onBack) {
                Text("CANCEL", color = Color.LightGray)
            }
        }
    }

    @Composable
    fun ReceiveScreen(width: Int, height: Int, fps: Int, status: String, onBack: () -> Unit) {
        BackHandler(onBack = onBack)
        Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
            StreamSurface(
                modifier = Modifier.fillMaxSize(),
                onSurfaceReady = { sv ->
                    sv.holder.addCallback(object : SurfaceHolder.Callback {
                        override fun surfaceCreated(holder: SurfaceHolder) {
                            holder.setFixedSize(width, height)
                            startStream(holder.surface, width, height, fps)
                        }
                        override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, ht: Int) {}
                        override fun surfaceDestroyed(h: SurfaceHolder) { stopStream() }
                    })
                }
            )
            Text(
                text = status,
                color = Color.Green,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(32.dp)
                    .zIndex(1f)
                    .clickable { onBack() } // Emergency exit if swipe doesn't work
            )
        }
    }

    private fun startStream(surface: Surface, width: Int, height: Int, fps: Int) {
        val d = H264Decoder(surface)
        decoder = d
        receiver = StreamReceiver(d, width, height, fps).also {
            it.onStatusChange = { msg -> runOnUiThread { status.value = msg } }
            it.start()
        }
    }

    private fun stopStream() {
        receiver?.stop()
        receiver = null
        decoder?.release()
        decoder = null
    }
}
