package com.example.monitorize

import android.view.MotionEvent
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.net.Socket
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel

/**
 * Opens a TCP connection to 127.0.0.1:7111 (ADB-forwarded to Linux touch_daemon.py)
 * and serializes Android MotionEvents into the binary packet format that
 * touch_daemon.py expects.
 *
 * Packet framing: [4 bytes uint32 BE length=13][1 byte type][13 bytes payload]
 * Payload struct (big-endian ">BBBHHHhh"):
 *   u8  action     0=DOWN 1=MOVE 2=UP 3=HOVER
 *   u8  tool       0=FINGER 1=PEN 2=ERASER
 *   u8  contactId  pointer ID from Android (0-9)
 *   u16 x          normalized 0-65535
 *   u16 y          normalized 0-65535
 *   u16 pressure   normalized 0-65535
 *   i16 tiltX      hundredths of degrees -9000..9000
 *   i16 tiltY      always 0 (Android doesn't expose per-axis tilt on most devices)
 */
class InputEventSender(
    private val screenW: Float,
    private val screenH: Float
) {
    companion object {
        private const val PORT = 7111
        private const val HOST = "127.0.0.1"
    }

    private var socket: Socket? = null
    private var out: OutputStream? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val sendChannel = Channel<ByteArray>(capacity = 256, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /**
     * Call once after entering ReceiveScreen.
     * Connects asynchronously — does not block the UI thread.
     * Retries every 2 seconds until connected or stopped.
     */
    fun start() {
        scope.launch {
            while (isActive) {
                try {
                    val s = Socket(HOST, PORT)
                    s.tcpNoDelay = true
                    s.sendBufferSize = 64 * 1024
                    socket = s
                    out = s.getOutputStream()
                    android.util.Log.i("InputEventSender", "Connected to touch_daemon on $HOST:$PORT")
                    // Drain the send channel while connected
                    for (frame in sendChannel) {
                        out?.write(frame) ?: break
                        out?.flush()
                    }
                } catch (e: Exception) {
                    android.util.Log.w("InputEventSender", "touch_daemon not ready, retrying in 2s: ${e.message}")
                    socket = null
                    out = null
                    delay(2000)
                }
            }
        }
    }

    /**
     * Call from the UI thread inside onTouchEvent / onHoverEvent.
     * Non-blocking — queues the serialized packet.
     */
    fun send(event: MotionEvent) {
        val outIsNull = out == null
        if (event.actionMasked != MotionEvent.ACTION_MOVE) {
            android.util.Log.d("InputEventSender", "send() called: action=${event.actionMasked} pointers=${event.pointerCount} out_isNull=${outIsNull}")
        }
        if (outIsNull) return  // not connected yet, drop silently

        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> {
                // MOVE: report ALL active pointers in one batch
                for (i in 0 until event.pointerCount) {
                    buildAndQueue(event, i, 1)
                }
            }
            else -> {
                val idx = event.actionIndex
                val action = when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN,
                    MotionEvent.ACTION_POINTER_DOWN -> 0
                    MotionEvent.ACTION_UP,
                    MotionEvent.ACTION_POINTER_UP,
                    MotionEvent.ACTION_CANCEL -> 2
                    MotionEvent.ACTION_HOVER_MOVE,
                    MotionEvent.ACTION_HOVER_ENTER,
                    MotionEvent.ACTION_HOVER_EXIT -> 3
                    else -> return
                }
                buildAndQueue(event, idx, action)
            }
        }
    }

    private fun buildAndQueue(event: MotionEvent, pointerIndex: Int, action: Int) {
        val toolType = event.getToolType(pointerIndex)
        val pktType: Byte = if (toolType == MotionEvent.TOOL_TYPE_STYLUS ||
                                 toolType == MotionEvent.TOOL_TYPE_ERASER) 0x04 else 0x03
        val tool: Byte = when (toolType) {
            MotionEvent.TOOL_TYPE_STYLUS  -> 1
            MotionEvent.TOOL_TYPE_ERASER  -> 2
            else -> 0
        }.toByte()

        // Android pointer IDs increment over time. The binary packet expects a 1-byte ID (0-255).
        // If we coerceIn(0, 9), multiple fingers with IDs >= 9 will collide into slot 9.
        val contactId = (event.getPointerId(pointerIndex) % 256).toByte()

        val rawX = event.getX(pointerIndex)
        val rawY = event.getY(pointerIndex)
        val x  = ((rawX / screenW) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val y  = ((rawY / screenH) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val pr = (event.getPressure(pointerIndex) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val tx = (event.getAxisValue(MotionEvent.AXIS_TILT, pointerIndex) * 100f)
                    .toInt().coerceIn(-9000, 9000).toShort()

        // 13-byte payload matching struct ">BBBHHHhh"
        // Frame = 4 bytes length + 1 byte type + 13 bytes payload = 18 bytes total
        val frame = ByteBuffer.allocate(18).order(ByteOrder.BIG_ENDIAN).apply {
            putInt(13)           // payload length
            put(pktType)         // 0x03 or 0x04
            put(action.toByte()) // 0=DOWN 1=MOVE 2=UP 3=HOVER
            put(tool)            // 0=FINGER 1=PEN 2=ERASER
            put(contactId)       // Android pointer ID
            putShort(x)          // normalized X 0-65535
            putShort(y)          // normalized Y 0-65535
            putShort(pr)         // pressure 0-65535
            putShort(tx)         // tilt X hundredths of degrees
            putShort(0)          // tilt Y (always 0)
        }.array()

        val success = sendChannel.trySend(frame).isSuccess
        if (action != 1) { // Log down/up events
            android.util.Log.d("InputEventSender", "Queued frame: action=$action contactId=$contactId pktType=$pktType success=$success")
        }
    }

    fun stop() {
        scope.cancel()
        try { socket?.close() } catch (_: Exception) {}
        socket = null
        out = null
    }
}
