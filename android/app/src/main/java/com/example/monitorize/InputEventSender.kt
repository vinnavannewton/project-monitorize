package com.example.monitorize

import android.view.InputDevice
import android.view.MotionEvent
import java.io.OutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.ArrayDeque
import java.util.LinkedHashMap
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
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
        private const val ACTION_DOWN_WIRE = 0
        private const val ACTION_MOVE_WIRE = 1
        private const val ACTION_UP_WIRE = 2
        private const val ACTION_HOVER_WIRE = 3
        private const val TOOL_FINGER: Byte = 0
        private const val TOOL_STYLUS: Byte = 1
        private const val TOOL_ERASER: Byte = 2
        private const val TOOL_MOUSE: Byte = 3
        private const val LEGACY_PAYLOAD_SIZE = 13
        private const val LEGACY_FRAME_SIZE = 18
        private const val PEN_EXT_PAYLOAD_SIZE = 19
        private const val PEN_EXT_FRAME_SIZE = 24
        private const val PEN_FLAG_CANCELED = 1
        private const val PEN_FLAG_HOVER_EXIT = 1 shl 1
        private const val PEN_FLAG_HOVER_ENTER = 1 shl 2
        private const val ANDROID_FLAG_CANCELED = 0x20
        private const val MAX_PENDING_FRAMES = 8
        private const val UDP_UP_REPEATS = 3
        private const val UDP_MAGIC = 0x4D5A4955
        private const val UDP_VERSION: Byte = 1
        private const val UDP_HEADER_SIZE = 21
        private const val UDP_KEY_CONTEXT = "Monitorize UDP input v1\u0000"
        private const val UDP_KEY_ID_CONTEXT = "Monitorize UDP input key id v1\u0000"
    }

    @Volatile private var socket: Socket? = null
    @Volatile private var udpSocket: DatagramSocket? = null
    @Volatile private var out: OutputStream? = null
    private val started = AtomicBoolean(false)
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val sendDispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private val sendScope = CoroutineScope(sendDispatcher + SupervisorJob())
    private val pendingFrames = ArrayDeque<ByteArray>()
    private val activeContactFrames = LinkedHashMap<FrameKey, ByteArray>()
    private val sendWake = Channel<Unit>(Channel.CONFLATED)

    private data class FrameKey(val packetType: Byte, val tool: Byte, val contactId: Byte)

    private fun frameKey(frame: ByteArray) = FrameKey(frame[4], frame[6], frame[7])

    private fun frameAction(frame: ByteArray) = frame[5].toInt()

    private fun isMotionFrame(frame: ByteArray): Boolean {
        val action = frameAction(frame)
        return action == ACTION_MOVE_WIRE || action == ACTION_HOVER_WIRE
    }

    private fun isRequiredFrame(frame: ByteArray): Boolean {
        val action = frameAction(frame)
        return action == ACTION_DOWN_WIRE || action == ACTION_UP_WIRE
    }

    private fun queueFrame(frame: ByteArray) {
        synchronized(pendingFrames) {
            val action = frameAction(frame)
            val key = frameKey(frame)
            if (isMotionFrame(frame)) {
                val iterator = pendingFrames.descendingIterator()
                while (iterator.hasNext()) {
                    val queued = iterator.next()
                    if (frameKey(queued) != key) continue
                    if (isRequiredFrame(queued)) break
                    if (isMotionFrame(queued)) {
                        iterator.remove()
                    }
                }
            }
            when (action) {
                ACTION_DOWN_WIRE, ACTION_MOVE_WIRE -> activeContactFrames[key] = frame.copyOf()
                ACTION_UP_WIRE -> activeContactFrames.remove(key)
            }
            while (pendingFrames.size >= MAX_PENDING_FRAMES) {
                if (!dropQueuedFrame()) break
            }
            if (pendingFrames.size >= MAX_PENDING_FRAMES && !isRequiredFrame(frame)) {
                return@synchronized
            }
            pendingFrames.addLast(frame)
        }
        sendWake.trySend(Unit)
    }

    private fun dropQueuedFrame(): Boolean {
        if (pendingFrames.isEmpty()) return false
        val staleMotion = pendingFrames.iterator()
        while (staleMotion.hasNext()) {
            if (isMotionFrame(staleMotion.next())) {
                staleMotion.remove()
                return true
            }
        }
        return false
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
                    if (!dropQueuedFrame()) break
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
        if (hostIp.isNullOrEmpty()) {
            scope.launch {
                while (isActive) {
                    var s: Socket? = null
                    try {
                        s = Socket().apply {
                            connect(InetSocketAddress(HOST, portTcp), 2500)
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
                            currentOut.flush()
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
                        val udpKey = if (encrypted) deriveUdpKey(authToken!!, fingerprint!!) else null
                        val keyId = udpKey?.let { udpKeyId(it) }
                        val noncePrefix = ByteArray(4)
                        if (udpKey != null) SecureRandom().nextBytes(noncePrefix)
                        var counter = 1L
                        udpSocket = u
                        android.util.Log.i("InputEventSender", "UDP touch ready for $hostIp:$portUdp")
                        sendWake.trySend(Unit)
                        sendQueuedUntilFailure { frame ->
                            try {
                                val repeats = if (frame[5].toInt() == ACTION_UP_WIRE) UDP_UP_REPEATS else 1
                                repeat(repeats) {
                                    val payload = if (udpKey != null && keyId != null) {
                                        encryptUdpFrame(frame, udpKey, keyId, noncePrefix, counter++)
                                    } else {
                                        frame
                                    }
                                    val packet = DatagramPacket(payload, payload.size, addr, portUdp)
                                    u.send(packet)
                                }
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
                    buildAndQueue(event, i, ACTION_MOVE_WIRE, viewW, viewH)
                }
            }
            MotionEvent.ACTION_CANCEL -> {
                for (i in 0 until event.pointerCount) {
                    buildAndQueue(event, i, ACTION_UP_WIRE, viewW, viewH, forceCanceled = true)
                }
            }
            else -> {
                val idx = event.actionIndex
                val action = when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN,
                    MotionEvent.ACTION_POINTER_DOWN -> ACTION_DOWN_WIRE
                    MotionEvent.ACTION_UP,
                    MotionEvent.ACTION_POINTER_UP -> ACTION_UP_WIRE
                    MotionEvent.ACTION_HOVER_MOVE,
                    MotionEvent.ACTION_HOVER_ENTER,
                    MotionEvent.ACTION_HOVER_EXIT -> ACTION_HOVER_WIRE
                    else -> return
                }
                buildAndQueue(event, idx, action, viewW, viewH)
            }
        }
    }

    private fun buildAndQueue(event: MotionEvent, pointerIndex: Int, requestedAction: Int, viewW: Float, viewH: Float, forceCanceled: Boolean = false) {
        val toolType = event.getToolType(pointerIndex)
        val isPen = toolType == MotionEvent.TOOL_TYPE_STYLUS || toolType == MotionEvent.TOOL_TYPE_ERASER
        val pktType: Byte = if (isPen) PKT_PEN_EXT else PKT_TOUCH
        val tool: Byte = when (toolType) {
            MotionEvent.TOOL_TYPE_STYLUS  -> TOOL_STYLUS
            MotionEvent.TOOL_TYPE_ERASER  -> TOOL_ERASER
            MotionEvent.TOOL_TYPE_MOUSE -> TOOL_MOUSE
            else -> if (isMouseLike(event)) TOOL_MOUSE else TOOL_FINGER
        }.toByte()
        val action = packetAction(event, requestedAction, tool)

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

    private fun isMouseLike(event: MotionEvent): Boolean {
        return (event.source and InputDevice.SOURCE_MOUSE) == InputDevice.SOURCE_MOUSE ||
            (event.source and InputDevice.SOURCE_TOUCHPAD) == InputDevice.SOURCE_TOUCHPAD
    }

    private fun packetAction(event: MotionEvent, requestedAction: Int, tool: Byte): Int {
        if (tool != TOOL_MOUSE) return requestedAction
        val primaryDown = event.buttonState and MotionEvent.BUTTON_PRIMARY != 0
        return when (requestedAction) {
            ACTION_DOWN_WIRE,
            ACTION_MOVE_WIRE -> if (primaryDown) requestedAction else ACTION_HOVER_WIRE
            ACTION_UP_WIRE -> ACTION_UP_WIRE
            else -> ACTION_HOVER_WIRE
        }
    }

    private fun deriveUdpKey(token: String, fingerprint: String): ByteArray {
        val tokenBytes = token.trim().lowercase(Locale.US).toByteArray(Charsets.US_ASCII)
        val fingerprintBytes = fingerprint.trim().uppercase(Locale.US).toByteArray(Charsets.US_ASCII)
        return sha256(
            UDP_KEY_CONTEXT.toByteArray(Charsets.UTF_8) +
                tokenBytes + byteArrayOf(0) + fingerprintBytes
        )
    }

    private fun udpKeyId(key: ByteArray): ByteArray {
        return sha256(UDP_KEY_ID_CONTEXT.toByteArray(Charsets.UTF_8) + key).copyOfRange(0, 4)
    }

    private fun encryptUdpFrame(
        frame: ByteArray,
        key: ByteArray,
        keyId: ByteArray,
        noncePrefix: ByteArray,
        counter: Long
    ): ByteArray {
        val header = ByteArray(UDP_HEADER_SIZE)
        writeInt(header, 0, UDP_MAGIC)
        header[4] = UDP_VERSION
        System.arraycopy(keyId, 0, header, 5, 4)
        System.arraycopy(noncePrefix, 0, header, 9, 4)
        writeLong(header, 13, counter)
        val nonce = noncePrefix + header.copyOfRange(13, 21)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(header)
        return header + cipher.doFinal(frame)
    }

    private fun sha256(data: ByteArray): ByteArray {
        return MessageDigest.getInstance("SHA-256").digest(data)
    }

    private fun writeInt(frame: ByteArray, offset: Int, value: Int) {
        frame[offset] = ((value ushr 24) and 0xff).toByte()
        frame[offset + 1] = ((value ushr 16) and 0xff).toByte()
        frame[offset + 2] = ((value ushr 8) and 0xff).toByte()
        frame[offset + 3] = (value and 0xff).toByte()
    }

    private fun writeLong(frame: ByteArray, offset: Int, value: Long) {
        for (index in 0 until 8) {
            frame[offset + index] = ((value ushr (56 - index * 8)) and 0xffL).toByte()
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
