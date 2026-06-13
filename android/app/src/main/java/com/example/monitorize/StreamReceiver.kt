package com.example.monitorize

import android.util.Log
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetSocketAddress

class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val hostIp: String? = null
) {
    private var running = false
    private var controlSocket: Socket? = null
    private var udpSocket: DatagramSocket? = null

    var onStatusChange: ((String) -> Unit)? = null
    var onDisconnect: (() -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val PORT = 7110
    }

    fun start() {
        running = true
        Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
            try {
                receiveLoop()
            } catch (e: Exception) {
                Log.e(TAG, "receiveLoop crashed", e)
            } finally {
                
                if (running) {
                    running = false
                    onDisconnect?.invoke()
                }
                cleanup()
            }
        }, "MonitorizeReceiver").start()
    }

    private fun receiveLoop() {
        if (hostIp.isNullOrEmpty()) {
            receiveLoopWifi("127.0.0.1")
        } else {
            receiveLoopWifi(hostIp)
        }
    }

    private fun receiveLoopWifi(targetIp: String) {
        val streamType = if (targetIp == "127.0.0.1") "USB" else "WiFi"
        while (running) {
            onStatusChange?.invoke(if (streamType == "USB") "Waiting for USB connection…" else "Connecting to $targetIp…")
            var socket: Socket? = null
            while (running && socket == null) {
                try {
                    socket = Socket()
                    socket.connect(InetSocketAddress(targetIp, PORT), 2000)
                } catch (e: Exception) {
                    socket = null
                    Thread.sleep(1000)
                }
            }
            if (socket == null || !running) break

            socket.tcpNoDelay = true
            socket.receiveBufferSize = 1024 * 1024
            controlSocket = socket

            onStatusChange?.invoke("Connected")
            decoder.init(width, height)
            onStatusChange?.invoke("")

            processStreamLoop(socket.getInputStream(), streamType)
            try { socket.close() } catch (e: Exception) {}
        }
    }


    
    private fun processStreamLoop(input: java.io.InputStream, streamType: String) {
        val buf = ByteArray(4 * 1024 * 1024)
        var writePos = 0
        val readBuf = ByteArray(128 * 1024)
        var hasReceivedVideo = false

        while (running) {
            val bytesRead = try {
                input.read(readBuf)
            } catch (e: Exception) {
                Log.w(TAG, "$streamType stream read error: ${e.message}")
                -1
            }
            
            if (bytesRead > 0) {
                hasReceivedVideo = true
            } else if (bytesRead <= 0) {
                if (hasReceivedVideo && running) {
                    Log.w(TAG, "$streamType stream ended permanently.")
                    running = false
                    onDisconnect?.invoke()
                } else {
                    Log.w(TAG, "$streamType stream probe ended. Re-listening...")
                }
                break
            }

            if (writePos + bytesRead > buf.size) {
                writePos = 0
            }
            System.arraycopy(readBuf, 0, buf, writePos, bytesRead)
            writePos += bytesRead

            var readStart = 0
            while (readStart < writePos - 4) {
                val sc1 = findStartCode(buf, readStart, writePos)
                if (sc1 < 0) break

                val sc2 = findStartCode(buf, sc1 + 4, writePos)
                if (sc2 < 0) {
                    val remaining = writePos - sc1
                    if (sc1 > 0) {
                        System.arraycopy(buf, sc1, buf, 0, remaining)
                    }
                    writePos = remaining
                    readStart = 0
                    break
                }

                decoder.feedChunk(buf, sc1, sc2 - sc1)
                readStart = sc2
            }

            if (readStart > 0 && readStart < writePos) {
                val remaining = writePos - readStart
                System.arraycopy(buf, readStart, buf, 0, remaining)
                writePos = remaining
            } else if (readStart >= writePos) {
                writePos = 0
            }
        }
    }

    private fun findStartCode(buf: ByteArray, from: Int, limit: Int): Int {
        val end = limit - 3
        var i = from
        while (i < end) {
            if (buf[i].toInt() != 0) {
                i++
                continue
            }
            if (buf[i + 1].toInt() == 0 && buf[i + 2].toInt() == 0 && buf[i + 3].toInt() == 1) {
                return i
            }
            i++
        }
        return -1
    }

    private fun cleanup() {
        try { controlSocket?.close() } catch (_: Exception) {}
        try { udpSocket?.close() } catch (_: Exception) {}
        controlSocket = null
        udpSocket = null
    }

    fun stop() {
        running = false
        cleanup()
    }
}
