package com.example.monitorize

import android.os.Bundle
import android.view.Surface
import android.view.SurfaceHolder
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class MainActivity : ComponentActivity() {

    private var decoder: H264Decoder? = null
    private var receiver: StreamReceiver? = null
    private val status = mutableStateOf("Ready")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        window.setBackgroundDrawableResource(android.R.color.black)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // Force Fullscreen
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE

        setContent {
            Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
                StreamSurface(
                    modifier = Modifier.fillMaxSize(),
                    onSurfaceReady = { sv ->
                        sv.holder.addCallback(object : SurfaceHolder.Callback {
                            override fun surfaceCreated(holder: SurfaceHolder) {
                                // Force the Surface to match the stream resolution exactly.
                                // This bypasses the hardware scaler for minimum latency.
                                holder.setFixedSize(StreamReceiver.STREAM_WIDTH, StreamReceiver.STREAM_HEIGHT)
                                startStream(holder.surface)
                            }
                            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, ht: Int) {}
                            override fun surfaceDestroyed(h: SurfaceHolder) { stopStream() }
                        })
                    }
                )
                Text(
                    text = status.value,
                    color = Color.Green,
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(32.dp)
                        .zIndex(1f)
                )
            }
        }
    }

    private fun startStream(surface: Surface) {
        val d = H264Decoder(surface)
        decoder = d
        receiver = StreamReceiver(d).also {
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
