package com.example.monitorize

import android.view.MotionEvent
import java.io.OutputStream
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.ArrayDeque
import java.util.LinkedHashMap
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

class InputEventSender(
    private val hostIp: String? = null,
    private val hostPort: Int = 7110,
    private val encrypted: Boolean = false,
    private val fingerprint: String? = null,
    private val authToken: String? = null
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
        private const val MAX_PENDING_FRAMES = 128
    }

    @Volatile private var socket: Socket? = null
    @Volatile private var udpSocket: DatagramSocket? = null
    @Volatile private var out: OutputStream? = null
    private val started = AtomicBoolean(false)
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val sendDispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private val sendScope = CoroutineScope(sendDispatcher + SupervisorJob())
    private val pendingFrames = ArrayDeque<ByteArray>()
    private val activeContactFrames = LinkedHashMap<Byte, ByteArray>()
    private val sendWake = Channel<Unit>(Channel.CONFLATED)

    private fun queueFrame(frame: ByteArray) {
        synchronized(pendingFrames) {
            val action = frame[5].toInt()
            val contactId = frame[7]
            if (action == 1 || action == 3) {
                val iterator = pendingFrames.iterator()
                while (iterator.hasNext()) {
                    val queued = iterator.next()
                    if (queued[4] == frame[4] && queued[7] == contactId &&
                        (queued[5].toInt() == 1 || queued[5].toInt() == 3)
                    ) {
                        iterator.remove()
                    }
                }
            }
            when (action) {
                0, 1 -> activeContactFrames[contactId] = frame.copyOf()
                2 -> activeContactFrames.remove(contactId)
            }
            while (pendingFrames.size >= MAX_PENDING_FRAMES) {
                if (!dropQueuedFrameFor(action)) break
            }
            pendingFrames.addLast(frame)
        }
        sendWake.trySend(Unit)
    }

    private fun dropQueuedFrameFor(newAction: Int): Boolean {
        if (pendingFrames.isEmpty()) return false
        if (newAction == 2) {
            val iterator = pendingFrames.iterator()
            while (iterator.hasNext()) {
                val queued = iterator.next()
                if (queued[5].toInt() != 2) {
                    iterator.remove()
                    return true
                }
            }
        }
        pendingFrames.removeFirst()
        return true
    }

    private fun peekFrame(): ByteArray? = synchronized(pendingFrames) {
        pendingFrames.firstOrNull()
    }

    private fun removePeekedFrame(frame: ByteArray) = synchronized(pendingFrames) {
        if (pendingFrames.firstOrNull() === frame) {
            pendingFrames.removeFirst()
        } else {
            pendingFrames.remove(frame)
        }
    }

    private fun synthesizeCancelFrames() {
        synchronized(pendingFrames) {
            if (activeContactFrames.isEmpty()) return@synchronized
            val cancelFrames = activeContactFrames.values.map { frame ->
                frame.copyOf().also {
                    it[5] = 2
                    if (it.size >= PEN_EXT_FRAME_SIZE) {
                        writeShort(it, 22, PEN_FLAG_CANCELED)
                    }
                }
            }
            activeContactFrames.clear()
            for (frame in cancelFrames.asReversed()) {
                while (pendingFrames.size >= MAX_PENDING_FRAMES) {
                    if (!dropQueuedFrameFor(2)) break
                }
                pendingFrames.addFirst(frame)
            }
        }
        sendWake.trySend(Unit)
    }

    private suspend fun sendQueuedPersistent(send: (ByteArray) -> Boolean) {
        for (ignored in sendWake) {
            while (true) {
                val frame = peekFrame() ?: break
                if (!send(frame)) break
                removePeekedFrame(frame)
            }
        }
    }

    private suspend fun sendQueuedUntilFailure(send: (ByteArray) -> Boolean) {
        for (ignored in sendWake) {
            while (true) {
                val frame = peekFrame() ?: break
                if (!send(frame)) return
                removePeekedFrame(frame)
            }
        }
    }

    fun start() {
        if (!started.compareAndSet(false, true)) return
        if (encrypted && (hostIp.isNullOrBlank() || fingerprint.isNullOrBlank() || authToken.isNullOrBlank())) {
            android.util.Log.e("InputEventSender", "Encrypted input requires host, fingerprint, and token")
            started.set(false)
            return
        }
        if (hostIp.isNullOrEmpty() || encrypted) {
            scope.launch {
                while (isActive) {
                    var s: Socket? = null
                    try {
                        s = if (encrypted) {
                            val secure = connectTls(hostIp!!, portUdp, fingerprint)
                            secure.socket.apply {
                                outputStream.write("AUTH $authToken\n".toByteArray(Charsets.US_ASCII))
                                outputStream.flush()
                                if (readAsciiLine(this) != "OK") throw SecurityException("Input authentication failed")
                                soTimeout = 0
                            }
                        } else {
                            Socket().apply {
                                connect(InetSocketAddress(HOST, portTcp), 2500)
                            }
                        }
                        s.tcpNoDelay = true
                        s.keepAlive = true
                        s.sendBufferSize = 4 * 1024
                        try { s.trafficClass = 0xB8 } catch (_: Exception) {}
                        socket = s
                        out = s.getOutputStream()
                        android.util.Log.i("InputEventSender", "Input connected")
                        sendWake.trySend(Unit)
                        
                        val inputStream = s.getInputStream()
                        val buffer = ByteArray(16)
                        while (isActive) {
                            val read = inputStream.read(buffer)
                            if (read == -1) {
                                break
                            }
                        }
                    } catch (e: Exception) {
                        if (isActive) {
                            android.util.Log.w("InputEventSender", "Input connection lost: ${e.message}")
                        }
                    } finally {
                        if (isActive) synthesizeCancelFrames()
                        out = null
                        socket = null
                        try { s?.close() } catch (_: Exception) {}
                        delay(2000)
                    }
                }
            }
            sendScope.launch {
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
                sendQueuedPersistent { frame ->
                    val currentOut = out
                    if (currentOut != null) {
                        try {
                            currentOut.write(frame)
                            true
                        } catch (e: Exception) {
                            out = null
                            try { socket?.close() } catch (_: Exception) {}
                            socket = null
                            false
                        }
                    } else {
                        false
                    }
                }
            }
        } else {
            sendScope.launch {
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
                while (isActive) {
                    var u: DatagramSocket? = null
                    try {
                        u = DatagramSocket()
                        u.sendBufferSize = 4 * 1024
                        try { u.trafficClass = 0xB8 } catch (_: Exception) {}
                        val addr = InetAddress.getByName(hostIp)
                        udpSocket = u
                        android.util.Log.i("InputEventSender", "UDP touch ready for $hostIp:$portUdp")
                        sendWake.trySend(Unit)
                        sendQueuedUntilFailure { frame ->
                            try {
                                val packet = DatagramPacket(frame, frame.size, addr, portUdp)
                                u.send(packet)
                                true
                            } catch (e: Exception) {
                                android.util.Log.w("InputEventSender", "UDP send failed: ${e.message}")
                                false
                            }
                        }
                    } catch (e: Exception) {
                        android.util.Log.e("InputEventSender", "UDP error", e)
                    } finally {
                        if (udpSocket === u) udpSocket = null
                        try { u?.close() } catch (_: Exception) {}
                    }
                    delay(2000)
                }
            }
        }
    }

    fun send(event: MotionEvent, viewW: Float, viewH: Float) {
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
        
        val x = ((rawX / w) * 65535f).toInt().coerceIn(0, 65535)
        val y = ((rawY / h) * 65535f).toInt().coerceIn(0, 65535)
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

            ByteArray(PEN_EXT_FRAME_SIZE).also {
                it[0] = 0
                it[1] = 0
                it[2] = 0
                it[3] = PEN_EXT_PAYLOAD_SIZE.toByte()
                it[4] = pktType
                it[5] = action.toByte()
                it[6] = tool
                it[7] = contactId
                writeShort(it, 8, x)
                writeShort(it, 10, y)
                writeShort(it, 12, pr.toInt())
                writeShort(it, 14, tiltX)
                writeShort(it, 16, tiltY)
                writeShort(it, 18, distance)
                writeShort(it, 20, btnState.toInt())
                writeShort(it, 22, flags)
            }
        } else {
            ByteArray(LEGACY_FRAME_SIZE).also {
                it[0] = 0
                it[1] = 0
                it[2] = 0
                it[3] = LEGACY_PAYLOAD_SIZE.toByte()
                it[4] = pktType
                it[5] = action.toByte()
                it[6] = tool
                it[7] = contactId
                writeShort(it, 8, x)
                writeShort(it, 10, y)
                writeShort(it, 12, pr.toInt())
                writeShort(it, 14, 0)
                writeShort(it, 16, btnState.toInt())
            }
        }

        queueFrame(frame)
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
        if (!started.getAndSet(false)) return
        scope.cancel()
        sendScope.cancel()
        sendDispatcher.close()
        try { socket?.close() } catch (_: Exception) {}
        try { udpSocket?.close() } catch (_: Exception) {}
        socket = null
        udpSocket = null
        out = null
    }
}
