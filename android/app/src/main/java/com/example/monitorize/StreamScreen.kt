package com.example.monitorize

import android.view.SurfaceView
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

@Composable
fun StreamSurface(modifier: Modifier = Modifier, onSurfaceReady: (SurfaceView) -> Unit) {
    AndroidView(
        factory = { ctx ->
            SurfaceView(ctx).also { onSurfaceReady(it) }
        },
        modifier = modifier
    )
}
