import os, re

with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/MainActivity.kt", "r") as f:
    content = f.read()

# Add hostIp to setContent state
content = content.replace(
    '''            var width by remember { mutableIntStateOf(prefs.getInt("width", 1280)) }''',
    '''            var hostIp by remember { mutableStateOf(prefs.getString("hostIp", "") ?: "") }
            var width by remember { mutableIntStateOf(prefs.getInt("width", 1280)) }'''
)

# Pass hostIp and onHostIpChange to HomeScreen
content = content.replace(
    '''                        Screen.Home -> HomeScreen(
                            onReceiveClick = { currentScreen = Screen.Receive },
                            onSettingsClick = { currentScreen = Screen.Settings }
                        )''',
    '''                        Screen.Home -> HomeScreen(
                            hostIp = hostIp,
                            onHostIpChange = { 
                                hostIp = it
                                prefs.edit().putString("hostIp", it).apply()
                            },
                            onReceiveClick = { currentScreen = Screen.Receive },
                            onSettingsClick = { currentScreen = Screen.Settings }
                        )'''
)

# Pass hostIp to ReceiveScreen
content = content.replace(
    '''                            ReceiveScreen(
                                width = width,
                                height = height,
                                fps = fps,
                                status = status.value,''',
    '''                            ReceiveScreen(
                                hostIp = hostIp,
                                width = width,
                                height = height,
                                fps = fps,
                                status = status.value,'''
)

# Update ReceiveScreen signature
content = content.replace(
    '''    fun ReceiveScreen(width: Int, height: Int, fps: Int, status: String, onBack: () -> Unit) {''',
    '''    fun ReceiveScreen(hostIp: String, width: Int, height: Int, fps: Int, status: String, onBack: () -> Unit) {'''
)

# Update startStream call inside ReceiveScreen
content = content.replace(
    '''                            startStream(holder.surface, width, height, fps)''',
    '''                            startStream(hostIp, holder.surface, width, height, fps)'''
)

# Update startStream signature and logic
content = content.replace(
    '''    private fun startStream(surface: Surface, width: Int, height: Int, fps: Int) {
        val d = H264Decoder(surface)
        decoder = d
        receiver = StreamReceiver(d, width, height, fps).also {
            it.onStatusChange = { msg -> runOnUiThread { status.value = msg } }
            it.start()
        }
        val displayMetrics = resources.displayMetrics
        inputSender = InputEventSender(
            screenW = displayMetrics.widthPixels.toFloat(),
            screenH = displayMetrics.heightPixels.toFloat()
        ).also { it.start() }
    }''',
    '''    private fun startStream(hostIp: String, surface: Surface, width: Int, height: Int, fps: Int) {
        val d = H264Decoder(surface)
        decoder = d
        receiver = StreamReceiver(d, width, height, fps, hostIp.takeIf { it.isNotBlank() }).also {
            it.onStatusChange = { msg -> runOnUiThread { status.value = msg } }
            it.start()
        }
        val displayMetrics = resources.displayMetrics
        inputSender = InputEventSender(
            screenW = displayMetrics.widthPixels.toFloat(),
            screenH = displayMetrics.heightPixels.toFloat(),
            hostIp = hostIp.takeIf { it.isNotBlank() }
        ).also { it.start() }
    }'''
)

# Update HomeScreen signature and add IP text field
home_screen_old = '''    @Composable
    fun HomeScreen(onReceiveClick: () -> Unit, onSettingsClick: () -> Unit) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 32.dp, vertical = 48.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(modifier = Modifier.weight(1f))'''

home_screen_new = '''    @Composable
    fun HomeScreen(hostIp: String, onHostIpChange: (String) -> Unit, onReceiveClick: () -> Unit, onSettingsClick: () -> Unit) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 32.dp, vertical = 48.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(modifier = Modifier.weight(1f))'''

content = content.replace(home_screen_old, home_screen_new)

# Add the text field above RECEIVE STREAM button
btn_old = '''            Spacer(modifier = Modifier.height(48.dp))

            Button(
                onClick = onReceiveClick,
                modifier = Modifier
                    .fillMaxWidth(0.7f)
                    .height(56.dp),'''

btn_new = '''            Spacer(modifier = Modifier.height(32.dp))

            OutlinedTextField(
                value = hostIp,
                onValueChange = onHostIpChange,
                label = { Text("PC IP Address (optional)") },
                placeholder = { Text("Leave blank for USB") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(0.85f),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = AccentIndigo,
                    unfocusedBorderColor = BorderDark,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary
                )
            )

            Spacer(modifier = Modifier.height(24.dp))

            Button(
                onClick = onReceiveClick,
                modifier = Modifier
                    .fillMaxWidth(0.7f)
                    .height(56.dp),'''

content = content.replace(btn_old, btn_new)

# Replace bottom text
bottom_text_old = '''            Text(
                "Connect via USB · Open source",
                fontSize = 11.sp,
                color = TextMuted
            )'''
            
bottom_text_new = '''            Text(
                "Connect via USB or Wi-Fi · Open source",
                fontSize = 11.sp,
                color = TextMuted
            )'''

content = content.replace(bottom_text_old, bottom_text_new)


with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/MainActivity.kt", "w") as f:
    f.write(content)
print("MainActivity.kt patched.")
