package com.example.monitorize

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.Log
import android.view.View
import java.net.ServerSocket
import java.nio.ByteBuffer

/**
 * Receives cursor position via TCP and draws a cursor overlay on the stream.
 * Packet format: 4 bytes — 2 bytes X (uint16 BE) + 2 bytes Y (uint16 BE)
 * Sentinel 0xFFFF,0xFFFF means cursor is off-screen (hide it).
 *
 * Acts as a TCP server on port 7112 (Linux cursor_sender.py connects as client
 * via ADB forward tcp:7112 tcp:7112).
 */
class CursorOverlay(context: Context) : View(context) {

    @Volatile private var cursorX = -1f
    @Volatile private var cursorY = -1f
    @Volatile private var visible = false
    @Volatile private var running = false
    private var receiverThread: Thread? = null
    private var serverSocket: ServerSocket? = null

    // Stream resolution (set by caller)
    var streamWidth = 1f
    var streamHeight = 1f

    companion object {
        private const val TAG = "CursorOverlay"
        private const val PORT = 7112
    }

    // Arrow cursor paint
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.FILL
    }
    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.BLACK
        style = Paint.Style.STROKE
        strokeWidth = 1.5f
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!visible || cursorX < 0) return

        // Map stream coordinates to view coordinates
        val viewW = width.toFloat()
        val viewH = height.toFloat()
        val sx = cursorX / streamWidth * viewW
        val sy = cursorY / streamHeight * viewH

        // Draw arrow cursor (standard pointer shape, ~20dp)
        val scale = resources.displayMetrics.density
        val size = 20f * scale

        canvas.save()
        canvas.translate(sx, sy)

        val path = Path().apply {
            moveTo(0f, 0f)                          // tip
            lineTo(0f, size)                         // left edge down
            lineTo(size * 0.35f, size * 0.72f)       // notch
            lineTo(size * 0.58f, size * 1.1f)         // tail right
            lineTo(size * 0.72f, size * 1.0f)         // tail right top
            lineTo(size * 0.48f, size * 0.62f)        // notch inner
            lineTo(size * 0.78f, size * 0.62f)        // right wing
            close()
        }

        canvas.drawPath(path, fillPaint)
        canvas.drawPath(path, borderPaint)
        canvas.restore()
    }

    fun start() {
        running = true
        receiverThread = Thread({
            while (running) {
                try {
                    serverSocket = ServerSocket(PORT)
                    Log.i(TAG, "Listening on TCP port $PORT")

                    val client = serverSocket!!.accept()
                    client.tcpNoDelay = true
                    Log.i(TAG, "Cursor sender connected")

                    val input = client.getInputStream()
                    val buf = ByteArray(4)

                    while (running) {
                        var read = 0
                        // Read exactly 4 bytes
                        while (read < 4) {
                            val n = input.read(buf, read, 4 - read)
                            if (n <= 0) throw java.io.IOException("EOF")
                            read += n
                        }

                        val bb = ByteBuffer.wrap(buf)
                        val x = bb.short.toInt() and 0xFFFF
                        val y = bb.getShort(2).toInt() and 0xFFFF

                        if (x == 0xFFFF && y == 0xFFFF) {
                            if (visible) {
                                visible = false
                                postInvalidate()
                            }
                        } else {
                            cursorX = x.toFloat()
                            cursorY = y.toFloat()
                            visible = true
                            postInvalidate()
                        }
                    }
                } catch (e: Exception) {
                    if (running) {
                        Log.w(TAG, "Connection ended: ${e.message}, re-listening...")
                        try { Thread.sleep(500) } catch (_: Exception) {}
                    }
                } finally {
                    try { serverSocket?.close() } catch (_: Exception) {}
                    serverSocket = null
                }
            }
        }, "CursorReceiver").also {
            it.isDaemon = true
            it.start()
        }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        try { receiverThread?.join(1000) } catch (_: Exception) {}
        receiverThread = null
    }
}
