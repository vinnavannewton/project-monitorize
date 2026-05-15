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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

// ── Color Palette ────────────────────────────────────────────────────────────
private val BgDark       = Color(0xFF0C0D14)
private val SurfaceDark  = Color(0xFF12142A)
private val CardDark     = Color(0xFF161830)
private val BorderDark   = Color(0xFF252845)
private val AccentIndigo = Color(0xFF4C4FD0)
private val TextPrimary  = Color(0xFFD4D6F0)
private val TextSecondary= Color(0xFF6A6C96)
private val TextMuted    = Color(0xFF4A4C70)
private val GreenAccent  = Color(0xFF4CD68D)
private val AmberWarn    = Color(0xFFE8A840)
private val RedStop      = Color(0xFFA82028)

enum class Screen { Home, Settings, Receive }

private val MonitorizeColorScheme = darkColorScheme(
    primary          = AccentIndigo,
    onPrimary        = Color.White,
    secondary        = Color(0xFF3538B0),
    background       = BgDark,
    surface          = SurfaceDark,
    surfaceVariant   = CardDark,
    onBackground     = TextPrimary,
    onSurface        = TextPrimary,
    onSurfaceVariant = TextSecondary,
    outline          = BorderDark,
    error            = RedStop,
)

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

            MaterialTheme(colorScheme = MonitorizeColorScheme) {
                Surface(modifier = Modifier.fillMaxSize(), color = BgDark) {
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

    // ── Home Screen ──────────────────────────────────────────────────────────

    @Composable
    fun HomeScreen(onReceiveClick: () -> Unit, onSettingsClick: () -> Unit) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 40.dp, vertical = 48.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(modifier = Modifier.weight(1f))

            Text(
                "Monitorize",
                fontSize = 42.sp,
                fontWeight = FontWeight.ExtraBold,
                color = TextPrimary,
                letterSpacing = 2.sp
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "Linux → Android Display Bridge",
                fontSize = 14.sp,
                color = TextSecondary
            )

            Spacer(modifier = Modifier.height(48.dp))

            Button(
                onClick = onReceiveClick,
                modifier = Modifier
                    .fillMaxWidth(0.7f)
                    .height(56.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(containerColor = AccentIndigo)
            ) {
                Text(
                    "▶  RECEIVE STREAM",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp
                )
            }

            Spacer(modifier = Modifier.height(14.dp))

            OutlinedButton(
                onClick = onSettingsClick,
                modifier = Modifier
                    .fillMaxWidth(0.7f)
                    .height(56.dp),
                shape = RoundedCornerShape(14.dp),
                border = ButtonDefaults.outlinedButtonBorder.copy(
                    brush = Brush.linearGradient(listOf(BorderDark, BorderDark))
                ),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = TextPrimary)
            ) {
                Text(
                    "⚙  SETTINGS",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 1.sp
                )
            }

            Spacer(modifier = Modifier.weight(1f))

            Text(
                "Connect via USB · Open source",
                fontSize = 11.sp,
                color = TextMuted
            )
        }
    }

    // ── Settings Screen ─────────────────────────────────────────────────────

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
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 28.dp, vertical = 32.dp)
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                "Settings",
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary,
                letterSpacing = 1.sp
            )
            Spacer(modifier = Modifier.height(24.dp))

            // Warning card
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color(0x0FE8A840))
                    .padding(14.dp)
            ) {
                Text(
                    "⚠  Resolution and FPS must exactly match the Linux desktop app settings, or the stream will corrupt.",
                    color = AmberWarn,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    lineHeight = 18.sp
                )
            }

            Spacer(modifier = Modifier.height(28.dp))

            // ── Resolution Section ──
            SectionHeader("RESOLUTION")
            Spacer(modifier = Modifier.height(10.dp))

            ExposedDropdownMenuBox(
                expanded = resExpanded,
                onExpandedChange = { resExpanded = !resExpanded }
            ) {
                TextField(
                    value = selectedRes,
                    onValueChange = {},
                    readOnly = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = resExpanded) },
                    modifier = Modifier.menuAnchor().fillMaxWidth(),
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = CardDark,
                        unfocusedContainerColor = CardDark,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        focusedIndicatorColor = AccentIndigo,
                        unfocusedIndicatorColor = BorderDark
                    ),
                    shape = RoundedCornerShape(10.dp)
                )
                ExposedDropdownMenu(expanded = resExpanded, onDismissRequest = { resExpanded = false }) {
                    resolutions.forEach { res ->
                        DropdownMenuItem(
                            text = { Text(res, color = TextPrimary) },
                            onClick = { selectedRes = res; resExpanded = false }
                        )
                    }
                }
            }

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
                        modifier = Modifier.width(120.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentIndigo,
                            unfocusedBorderColor = BorderDark,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        )
                    )
                    Text(
                        " × ",
                        color = TextSecondary,
                        fontSize = 20.sp,
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
                        modifier = Modifier.width(120.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentIndigo,
                            unfocusedBorderColor = BorderDark,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        )
                    )
                }
                Text("500 – 4000 for each value", color = TextMuted, fontSize = 11.sp)
            }

            Spacer(modifier = Modifier.height(24.dp))

            // ── FPS Section ──
            SectionHeader("FRAMERATE")
            Spacer(modifier = Modifier.height(10.dp))

            ExposedDropdownMenuBox(
                expanded = fpsExpanded,
                onExpandedChange = { fpsExpanded = !fpsExpanded }
            ) {
                TextField(
                    value = selectedFps,
                    onValueChange = {},
                    readOnly = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = fpsExpanded) },
                    modifier = Modifier.menuAnchor().fillMaxWidth(),
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = CardDark,
                        unfocusedContainerColor = CardDark,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        focusedIndicatorColor = AccentIndigo,
                        unfocusedIndicatorColor = BorderDark
                    ),
                    shape = RoundedCornerShape(10.dp)
                )
                ExposedDropdownMenu(expanded = fpsExpanded, onDismissRequest = { fpsExpanded = false }) {
                    fpsOptions.forEach { f ->
                        DropdownMenuItem(
                            text = { Text(f, color = TextPrimary) },
                            onClick = { selectedFps = f; fpsExpanded = false }
                        )
                    }
                }
            }

            if (selectedFps == "Custom…") {
                Spacer(modifier = Modifier.height(12.dp))
                OutlinedTextField(
                    value = customFps,
                    onValueChange = { customFps = it },
                    label = { Text("Custom FPS") },
                    placeholder = { Text("e.g. 144") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.width(160.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = AccentIndigo,
                        unfocusedBorderColor = BorderDark,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary
                    )
                )
                Text("24 – 240", color = TextMuted, fontSize = 11.sp)
            }

            if (saveError.isNotEmpty()) {
                Spacer(modifier = Modifier.height(14.dp))
                Text(saveError, color = RedStop, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }

            Spacer(modifier = Modifier.height(40.dp))

            Button(
                onClick = {
                    saveError = ""
                    val (finalW, finalH) = if (selectedRes == "Custom…") {
                        val w = customWidth.trim().toIntOrNull()
                        val h = customHeight.trim().toIntOrNull()
                        if (w == null || h == null) {
                            saveError = "Width and Height must be numbers."
                            return@Button
                        }
                        if (w !in 500..4000) {
                            saveError = "Width must be between 500 and 4000. Got: $w"
                            return@Button
                        }
                        if (h !in 500..4000) {
                            saveError = "Height must be between 500 and 4000. Got: $h"
                            return@Button
                        }
                        Pair(w, h)
                    } else {
                        val parts = selectedRes.split("x")
                        Pair(parts[0].toInt(), parts[1].toInt())
                    }
                    val finalFps = if (selectedFps == "Custom…") {
                        val f = customFps.trim().toIntOrNull()
                        if (f == null) {
                            saveError = "FPS must be a number."
                            return@Button
                        }
                        if (f !in 24..240) {
                            saveError = "FPS must be between 24 and 240. Got: $f"
                            return@Button
                        }
                        f
                    } else {
                        selectedFps.toInt()
                    }
                    onSave(finalW, finalH, finalFps)
                },
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = AccentIndigo)
            ) {
                Text("SAVE & BACK", fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            }

            Spacer(modifier = Modifier.height(8.dp))

            TextButton(onClick = onBack) {
                Text("CANCEL", color = TextSecondary, letterSpacing = 1.sp)
            }
        }
    }

    @Composable
    private fun SectionHeader(title: String) {
        Text(
            title,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = TextSecondary,
            letterSpacing = 2.sp,
            modifier = Modifier.fillMaxWidth().padding(start = 4.dp)
        )
    }

    // ── Receive Screen ──────────────────────────────────────────────────────

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

            // Status overlay
            if (status.isNotEmpty()) {
                Text(
                    text = status,
                    color = GreenAccent,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(24.dp)
                        .zIndex(1f)
                        .background(
                            color = Color(0xAA0C0D14),
                            shape = RoundedCornerShape(8.dp)
                        )
                        .padding(horizontal = 12.dp, vertical = 6.dp)
                        .clickable { onBack() }
                )
            }

            // First-time hint
            Box(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 20.dp, start = 20.dp, end = 20.dp)
                    .background(
                        color = Color(0xCC0C0D14),
                        shape = RoundedCornerShape(10.dp)
                    )
                    .padding(horizontal = 14.dp, vertical = 10.dp)
                    .zIndex(1f)
            ) {
                Text(
                    text = "💡 First time? After the stream starts, go to Display Config and set up the virtual display.",
                    color = TextSecondary,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    lineHeight = 16.sp
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
