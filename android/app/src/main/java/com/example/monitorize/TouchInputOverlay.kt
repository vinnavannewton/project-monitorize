package com.example.monitorize

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.layout.onSizeChanged

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
 * The [InputEventSender] is created, started, and stopped automatically
 * for the lifetime of this composable.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun rememberInputSender(): InputEventSender {
    val sender = remember { InputEventSender() }
    DisposableEffect(Unit) {
        sender.start()
        onDispose { sender.stop() }
    }
    return sender
}

/**
 * Extension function that adds touch capture and size tracking to a Modifier.
 * Apply this to the overlay Box so events are forwarded to [sender].
 */
@OptIn(ExperimentalComposeUiApi::class)
fun Modifier.touchInputOverlay(sender: InputEventSender): Modifier =
    this
        .fillMaxSize()
        // Track rendered pixel size so the sender can normalize coordinates
        .onSizeChanged { size ->
            sender.viewWidth  = size.width.coerceAtLeast(1)
            sender.viewHeight = size.height.coerceAtLeast(1)
        }
        // Intercept all MotionEvents (finger + stylus + hover)
        .pointerInteropFilter { event ->
            sender.send(event)
            // Return true to consume the event and receive subsequent MOVE/UP events.
            // If we return false on ACTION_DOWN, Android will not send us the rest of the gesture!
            true
        }
