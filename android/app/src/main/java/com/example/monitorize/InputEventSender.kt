package com.example.monitorize

import android.view.MotionEvent
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetAddress
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import java.util.concurrent.atomic.AtomicInteger

class ByteArrayPool(private val itemSize: Int) {
    private val pool = java.util.concurrent.ConcurrentLinkedQueue<ByteArray>()
    private val poolSize = AtomicInteger(0)

    fun obtain(): ByteArray {
        val fromPool = pool.poll()
        return if (fromPool != null) {
            poolSize.decrementAndGet()
            fromPool
        } else {
            ByteArray(itemSize)
        }
    }

    fun recycle(array: ByteArray) {
        
        if (poolSize.get() < 32) {
            if (pool.offer(array)) {
                poolSize.incrementAndGet()
            }
        }
    }
}

class InputEventSender(
    private val hostIp: String? = null
) {
    companion object {
        private const val PORT_TCP = 7111
        private const val PORT_UDP = 7113
        private const val HOST = "127.0.0.1"
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
                        s = Socket(HOST, PORT_TCP)
                        s.tcpNoDelay = true
                        s.sendBufferSize = 64 * 1024
                        socket = s
                        out = s.getOutputStream()
                        android.util.Log.i("InputEventSender", "Connected to touch_daemon TCP on $HOST:$PORT_TCP")
                        
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
                    android.util.Log.i("InputEventSender", "UDP touch ready for $hostIp:$PORT_UDP")
                    for (frame in sendChannel) {
                        val packet = DatagramPacket(frame, frame.size, addr, PORT_UDP)
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
                buildAndQueue(event, idx, action, viewW, viewH)
            }
        }
    }

    private fun buildAndQueue(event: MotionEvent, pointerIndex: Int, action: Int, viewW: Float, viewH: Float) {
        val toolType = event.getToolType(pointerIndex)
        val pktType: Byte = if (toolType == MotionEvent.TOOL_TYPE_STYLUS ||
                                 toolType == MotionEvent.TOOL_TYPE_ERASER) 0x04 else 0x03
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
        val tx = (event.getAxisValue(MotionEvent.AXIS_TILT, pointerIndex) * 100f)
                    .toInt().coerceIn(-9000, 9000).toShort()
        val btnState = event.buttonState.toShort()

        val frame = pool.obtain()
        frame[0] = 0
        frame[1] = 0
        frame[2] = 0
        frame[3] = 13
        frame[4] = pktType
        frame[5] = action.toByte()
        frame[6] = tool
        frame[7] = contactId
        
        frame[8] = (x.toInt() shr 8).toByte()
        frame[9] = x.toByte()
        frame[10] = (y.toInt() shr 8).toByte()
        frame[11] = y.toByte()
        frame[12] = (pr.toInt() shr 8).toByte()
        frame[13] = pr.toByte()
        frame[14] = (tx.toInt() shr 8).toByte()
        frame[15] = tx.toByte()
        frame[16] = (btnState.toInt() shr 8).toByte()
        frame[17] = btnState.toByte()

        val sent = sendChannel.trySend(frame)
        if (!sent.isSuccess) {
            pool.recycle(frame)
        }
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
