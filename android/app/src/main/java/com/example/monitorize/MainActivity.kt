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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
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
            "2560x1440", "2560x1600", "3840x2160", "Custom…"
        )
        val fpsOptions = listOf("30", "60", "90", "120", "Custom…")

        val initRes = "${initialWidth}x${initialHeight}"
        val isInitResCustom = initRes !in resolutions
        var selectedRes by remember { mutableStateOf(if (isInitResCustom) "Custom…" else initRes) }
        var customWidth  by remember { mutableStateOf(if (isInitResCustom) initialWidth.toString()  else "") }
        var customHeight by remember { mutableStateOf(if (isInitResCustom) initialHeight.toString() else "") }

        val isInitFpsCustom = initialFps.toString() !in fpsOptions
        var selectedFps by remember { mutableStateOf(if (isInitFpsCustom) "Custom…" else initialFps.toString()) }
        var customFps by remember { mutableStateOf(if (isInitFpsCustom) initialFps.toString() else "") }

        var resExpanded by remember { mutableStateOf(false) }
        var fpsExpanded by remember { mutableStateOf(false) }
        var saveError  by remember { mutableStateOf("") }

        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp).verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("Settings", fontSize = 32.sp, fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(32.dp))

            Text(
                "⚠️ WARNING: The Resolution and FPS set here MUST EXACTLY MATCH the settings " +
                "in the Linux desktop app, or the stream will crash/corrupt!",
                color = Color.Yellow, fontWeight = FontWeight.Bold
            )

            Spacer(modifier = Modifier.height(32.dp))

            // ---- Resolution Dropdown ----
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

            // Custom resolution input fields
            if (selectedRes == "Custom…") {
                Spacer(modifier = Modifier.height(12.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    OutlinedTextField(
                        value = customWidth,
                        onValueChange = { customWidth = it },
                        label = { Text("Width") },
                        placeholder = { Text("e.g. 1920") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.width(120.dp)
                    )
                    Text(
                        " × ",
                        color = Color.Gray,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 8.dp)
                    )
                    OutlinedTextField(
                        value = customHeight,
                        onValueChange = { customHeight = it },
                        label = { Text("Height") },
                        placeholder = { Text("e.g. 1080") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.width(120.dp)
                    )
                }
                Text("500 – 4000 for each value", color = Color(0xFF9E9E9E), fontSize = 12.sp)
            }

            Spacer(modifier = Modifier.height(16.dp))

            // ---- FPS Dropdown ----
            Text("FPS", color = Color.Gray)
            ExposedDropdownMenuBox(
                expanded = fpsExpanded,
                onExpandedChange = { fpsExpanded = !fpsExpanded }
            ) {
                TextField(
                    value = selectedFps,
                    onValueChange = {},
                    readOnly = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = fpsExpanded) },
                    modifier = Modifier.menuAnchor()
                )
                ExposedDropdownMenu(expanded = fpsExpanded, onDismissRequest = { fpsExpanded = false }) {
                    fpsOptions.forEach { f ->
                        DropdownMenuItem(
                            text = { Text(f) },
                            onClick = { selectedFps = f; fpsExpanded = false }
                        )
                    }
                }
            }

            // Custom FPS input
            if (selectedFps == "Custom…") {
                Spacer(modifier = Modifier.height(12.dp))
                OutlinedTextField(
                    value = customFps,
                    onValueChange = { customFps = it },
                    label = { Text("Custom FPS") },
                    placeholder = { Text("e.g. 144") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.width(160.dp)
                )
                Text("24 – 240", color = Color(0xFF9E9E9E), fontSize = 12.sp)
            }

            // Inline save error message
            if (saveError.isNotEmpty()) {
                Spacer(modifier = Modifier.height(12.dp))
                Text(saveError, color = Color.Red, fontWeight = FontWeight.Bold)
            }

            Spacer(modifier = Modifier.height(48.dp))

            Button(
                onClick = {
                    saveError = ""
                    // Resolve final width/height
                    val (finalW, finalH) = if (selectedRes == "Custom…") {
                        val w = customWidth.trim().toIntOrNull()
                        val h = customHeight.trim().toIntOrNull()
                        if (w == null || h == null) {
                            saveError = "❌ Width and Height must be numbers."
                            return@Button
                        }
                        if (w !in 500..4000) {
                            saveError = "❌ Width must be between 500 and 4000. Got: $w"
                            return@Button
                        }
                        if (h !in 500..4000) {
                            saveError = "❌ Height must be between 500 and 4000. Got: $h"
                            return@Button
                        }
                        Pair(w, h)
                    } else {
                        val parts = selectedRes.split("x")
                        Pair(parts[0].toInt(), parts[1].toInt())
                    }
                    // Resolve final FPS
                    val finalFps = if (selectedFps == "Custom…") {
                        val f = customFps.trim().toIntOrNull()
                        if (f == null) {
                            saveError = "❌ FPS must be a number."
                            return@Button
                        }
                        if (f !in 24..240) {
                            saveError = "❌ FPS must be between 24 and 240. Got: $f"
                            return@Button
                        }
                        f
                    } else {
                        selectedFps.toInt()
                    }
                    onSave(finalW, finalH, finalFps)
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
            if (status.isNotEmpty()) {
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
            // First-time display config note
            Box(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 24.dp, start = 16.dp, end = 16.dp)
                    .background(
                        color = Color(0xCC1A1A2E),
                        shape = androidx.compose.foundation.shape.RoundedCornerShape(10.dp)
                    )
                    .padding(horizontal = 16.dp, vertical = 10.dp)
                    .zIndex(1f)
            ) {
                Text(
                    text = "💡 First time? After the stream starts, go to Display Config on your Android device " +
                           "and set up the virtual display first for the best experience.",
                    color = Color(0xFFB0B0D8),
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium
                )
            }
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
