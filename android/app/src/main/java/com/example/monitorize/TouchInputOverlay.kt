package com.example.monitorize

import android.content.res.Resources
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.platform.LocalContext

/**
 * TouchInputOverlay
 *
 * A transparent, full-screen Compose modifier chain that captures all
 * finger and stylus MotionEvents and forwards them to an [InputEventSender].
 *
 * Usage: Apply [touchInputOverlay] to any Modifier inside the Receive screen Box.
 * The overlay must sit ABOVE the StreamSurface in the Compose z-order so it
 * receives events first.
 *
 * The [InputEventSender] is created with the current screen pixel dimensions,
 * started, and stopped automatically for the lifetime of this composable.
 *
 * NOTE: InputEventSender requires screenW and screenH at construction time.
 * We read them from DisplayMetrics — the same source used in MainActivity.startStream().
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun rememberInputSender(): InputEventSender {
    val context = LocalContext.current
    val metrics = remember { context.resources.displayMetrics }
    val sender = remember {
        InputEventSender(
            screenW = metrics.widthPixels.toFloat(),
            screenH = metrics.heightPixels.toFloat()
        )
    }
    DisposableEffect(Unit) {
        sender.start()
        onDispose { sender.stop() }
    }
    return sender
}

/**
 * Extension function that adds touch and hover capture to a Modifier.
 * Apply this to the overlay Box so all finger and stylus events are forwarded to [sender].
 */
@OptIn(ExperimentalComposeUiApi::class)
fun Modifier.touchInputOverlay(sender: InputEventSender): Modifier =
    this
        .fillMaxSize()
        // Intercept all MotionEvents (finger + stylus + hover)
        .pointerInteropFilter { event ->
            sender.send(event)
            // Return true to consume the event and receive subsequent MOVE/UP events.
            // If we return false on ACTION_DOWN, Android will not send us the rest of the gesture!
            true
        }
