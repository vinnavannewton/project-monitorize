package com.example.monitorize

import android.view.MotionEvent
import java.io.OutputStream
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetAddress
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import java.util.concurrent.atomic.AtomicInteger

class ByteArrayPool(private val itemSize: Int) {
    private val pool = java.util.concurrent.ConcurrentLinkedQueue<ByteArray>()
    private val poolSize = AtomicInteger(0)

    fun obtain(size: Int = itemSize): ByteArray {
        if (size != itemSize) {
            return ByteArray(size)
        }
        val fromPool = pool.poll()
        return if (fromPool != null) {
            poolSize.decrementAndGet()
            fromPool
        } else {
            ByteArray(itemSize)
        }
    }

    fun recycle(array: ByteArray) {
        if (array.size != itemSize) {
            return
        }
        
        if (poolSize.get() < 32) {
            if (pool.offer(array)) {
                poolSize.incrementAndGet()
            }
        }
    }
}

class InputEventSender(
    private val hostIp: String? = null,
    private val hostPort: Int = 7110
) {
    private val portTcp = hostPort + 1
    private val portUdp = hostPort + 3

    companion object {
        private const val HOST = "127.0.0.1"
        private const val PKT_TOUCH: Byte = 0x03
        private const val PKT_PEN_EXT: Byte = 0x05
        private const val LEGACY_PAYLOAD_SIZE = 13
        private const val LEGACY_FRAME_SIZE = 18
        private const val PEN_EXT_PAYLOAD_SIZE = 19
        private const val PEN_EXT_FRAME_SIZE = 24
        private const val PEN_FLAG_CANCELED = 1
        private const val PEN_FLAG_HOVER_EXIT = 1 shl 1
        private const val PEN_FLAG_HOVER_ENTER = 1 shl 2
        private const val ANDROID_FLAG_CANCELED = 0x20
    }

    private var socket: Socket? = null
    private var udpSocket: DatagramSocket? = null
    private var out: OutputStream? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val pool = ByteArrayPool(18)
    private val sendChannel = Channel<ByteArray>(capacity = 256, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    fun start() {
        scope.launch {
            if (hostIp.isNullOrEmpty()) {
                while (isActive) {
                    var s: Socket? = null
                    try {
                        s = Socket(HOST, portTcp)
                        s.tcpNoDelay = true
                        s.sendBufferSize = 64 * 1024
                        socket = s
                        out = s.getOutputStream()
                        android.util.Log.i("InputEventSender", "Connected to touch_daemon TCP on $HOST:$portTcp")
                        
                        val inputStream = s.getInputStream()
                        val buffer = ByteArray(16)
                        while (isActive) {
                            val read = inputStream.read(buffer)
                            if (read == -1) {
                                break
                            }
                        }
                    } catch (e: Exception) {
                        
                    } finally {
                        out = null
                        socket = null
                        try { s?.close() } catch (_: Exception) {}
                        delay(2000)
                    }
                }
            } else {
                try {
                    val u = DatagramSocket()
                    val addr = InetAddress.getByName(hostIp)
                    udpSocket = u
                    android.util.Log.i("InputEventSender", "UDP touch ready for $hostIp:$portUdp")
                    for (frame in sendChannel) {
                        val packet = DatagramPacket(frame, frame.size, addr, portUdp)
                        udpSocket?.send(packet)
                        pool.recycle(frame)
                    }
                } catch (e: Exception) {
                    android.util.Log.e("InputEventSender", "UDP error", e)
                }
            }
        }

        if (hostIp.isNullOrEmpty()) {
            scope.launch {
                for (frame in sendChannel) {
                    val currentOut = out
                    if (currentOut != null) {
                        try {
                            currentOut.write(frame)
                            currentOut.flush()
                        } catch (e: Exception) {
                            out = null
                            try { socket?.close() } catch (_: Exception) {}
                            socket = null
                        }
                    }
                    pool.recycle(frame)
                }
            }
        }
    }

    fun send(event: MotionEvent, viewW: Float, viewH: Float) {
        val notConnected = (hostIp.isNullOrEmpty() && out == null) || (!hostIp.isNullOrEmpty() && udpSocket == null)
        if (notConnected) return

        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    buildAndQueue(event, i, 1, viewW, viewH)
                }
            }
            MotionEvent.ACTION_CANCEL -> {
                for (i in 0 until event.pointerCount) {
                    buildAndQueue(event, i, 2, viewW, viewH, forceCanceled = true)
                }
            }
            else -> {
                val idx = event.actionIndex
                val action = when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN,
                    MotionEvent.ACTION_POINTER_DOWN -> 0
                    MotionEvent.ACTION_UP,
                    MotionEvent.ACTION_POINTER_UP -> 2
                    MotionEvent.ACTION_HOVER_MOVE,
                    MotionEvent.ACTION_HOVER_ENTER,
                    MotionEvent.ACTION_HOVER_EXIT -> 3
                    else -> return
                }
                buildAndQueue(event, idx, action, viewW, viewH)
            }
        }
    }

    private fun buildAndQueue(event: MotionEvent, pointerIndex: Int, action: Int, viewW: Float, viewH: Float, forceCanceled: Boolean = false) {
        val toolType = event.getToolType(pointerIndex)
        val isPen = toolType == MotionEvent.TOOL_TYPE_STYLUS || toolType == MotionEvent.TOOL_TYPE_ERASER
        val pktType: Byte = if (isPen) PKT_PEN_EXT else PKT_TOUCH
        val tool: Byte = when (toolType) {
            MotionEvent.TOOL_TYPE_STYLUS  -> 1
            MotionEvent.TOOL_TYPE_ERASER  -> 2
            else -> 0
        }.toByte()

        val contactId = (event.getPointerId(pointerIndex) % 256).toByte()

        val rawX = event.getX(pointerIndex)
        val rawY = event.getY(pointerIndex)
        
        val w = viewW.coerceAtLeast(1f)
        val h = viewH.coerceAtLeast(1f)
        
        val x  = ((rawX / w) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val y  = ((rawY / h) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val pr = (event.getPressure(pointerIndex) * 65535f).toInt().coerceIn(0, 65535).toShort()
        val btnState = event.buttonState.toShort()

        val frame = if (isPen) {
            val tilt = event.getAxisValue(MotionEvent.AXIS_TILT, pointerIndex)
            val orientation = event.getOrientation(pointerIndex)
            val tiltDegrees = Math.toDegrees(tilt.toDouble()).coerceIn(0.0, 90.0)
            val tiltX = (sin(orientation.toDouble()) * tiltDegrees).roundToInt().coerceIn(-90, 90)
            val tiltY = (-cos(orientation.toDouble()) * tiltDegrees).roundToInt().coerceIn(-90, 90)
            val distance = normalizedDistance(event, pointerIndex)
            val flags = penFlags(event, forceCanceled)

            pool.obtain(PEN_EXT_FRAME_SIZE).also {
                it[0] = 0
                it[1] = 0
                it[2] = 0
                it[3] = PEN_EXT_PAYLOAD_SIZE.toByte()
                it[4] = pktType
                it[5] = action.toByte()
                it[6] = tool
                it[7] = contactId
                writeShort(it, 8, x.toInt())
                writeShort(it, 10, y.toInt())
                writeShort(it, 12, pr.toInt())
                writeShort(it, 14, tiltX)
                writeShort(it, 16, tiltY)
                writeShort(it, 18, distance)
                writeShort(it, 20, btnState.toInt())
                writeShort(it, 22, flags)
            }
        } else {
            pool.obtain(LEGACY_FRAME_SIZE).also {
                it[0] = 0
                it[1] = 0
                it[2] = 0
                it[3] = LEGACY_PAYLOAD_SIZE.toByte()
                it[4] = pktType
                it[5] = action.toByte()
                it[6] = tool
                it[7] = contactId
                writeShort(it, 8, x.toInt())
                writeShort(it, 10, y.toInt())
                writeShort(it, 12, pr.toInt())
                writeShort(it, 14, 0)
                writeShort(it, 16, btnState.toInt())
            }
        }

        val sent = sendChannel.trySend(frame)
        if (!sent.isSuccess) {
            pool.recycle(frame)
        }
    }

    private fun penFlags(event: MotionEvent, forceCanceled: Boolean): Int {
        var flags = 0
        if (forceCanceled || (event.flags and ANDROID_FLAG_CANCELED) != 0) {
            flags = flags or PEN_FLAG_CANCELED
        }
        if (event.actionMasked == MotionEvent.ACTION_HOVER_EXIT) {
            flags = flags or PEN_FLAG_HOVER_EXIT
        } else if (event.actionMasked == MotionEvent.ACTION_HOVER_ENTER) {
            flags = flags or PEN_FLAG_HOVER_ENTER
        }
        return flags
    }

    private fun normalizedDistance(event: MotionEvent, pointerIndex: Int): Int {
        val rawDistance = event.getAxisValue(MotionEvent.AXIS_DISTANCE, pointerIndex)
        val range = event.device?.getMotionRange(MotionEvent.AXIS_DISTANCE, event.source)
        val normalized = if (range != null && range.range > 0f) {
            ((rawDistance - range.min) / range.range).coerceIn(0f, 1f)
        } else {
            rawDistance.coerceIn(0f, 1f)
        }
        return (normalized * 1024f).roundToInt().coerceIn(0, 1024)
    }

    private fun writeShort(frame: ByteArray, offset: Int, value: Int) {
        frame[offset] = ((value shr 8) and 0xff).toByte()
        frame[offset + 1] = (value and 0xff).toByte()
    }

    fun stop() {
        scope.cancel()
        try { socket?.close() } catch (_: Exception) {}
        try { udpSocket?.close() } catch (_: Exception) {}
        socket = null
        udpSocket = null
        out = null
    }
}
