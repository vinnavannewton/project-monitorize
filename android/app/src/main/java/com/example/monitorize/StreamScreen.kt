package com.example.monitorize

import android.view.MotionEvent
import android.view.SurfaceView
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView


@Composable
fun StreamSurface(
    modifier: Modifier = Modifier,
    onSurfaceReady: (SurfaceView) -> Unit,
    onTouch: ((MotionEvent) -> Unit)? = null
) {
    AndroidView(
        factory = { ctx ->
            SurfaceView(ctx).also { sv ->
                
                sv.setZOrderOnTop(false)
                
                sv.setZOrderMediaOverlay(false)
                
                if (onTouch != null) {
                    sv.setOnTouchListener { _, event ->
                        onTouch(event)
                        true
                    }
                }
                
                onSurfaceReady(sv)
            }
        },
        modifier = modifier
    )
}
