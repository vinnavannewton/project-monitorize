package com.example.monitorize

import android.view.MotionEvent
import android.view.SurfaceView
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

/**
 * A full-screen [SurfaceView] wrapper.
 *
 * Important: we call setZOrderOnTop(false) and do NOT call setFixedSize()
 * here because the correct surface dimensions must match what MediaCodec
 * negotiates with the hardware decoder. Fixing the size independently can
 * cause a second scaling pass that re-introduces artifacts. Instead, we
 * keep the surface at MATCH_PARENT and let the codec drive it.
 */
@Composable
fun StreamSurface(
    modifier: Modifier = Modifier,
    onSurfaceReady: (SurfaceView) -> Unit,
    onTouch: ((MotionEvent) -> Unit)? = null
) {
    AndroidView(
        factory = { ctx ->
            SurfaceView(ctx).also { sv ->
                // Render below other views (keeps the status text overlay visible)
                sv.setZOrderOnTop(false)
                // Keep the surface pixels intact when the view is not drawn
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
