import os

stream_receiver = """package com.example.monitorize

import android.util.Log
import java.net.ServerSocket
import java.net.Socket
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.io.ByteArrayOutputStream

class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val fps: Int,
    private val hostIp: String? = null
) {
    private var running = false
    private var serverSocket: ServerSocket? = null
    private var controlSocket: Socket? = null
    private var udpSocket: DatagramSocket? = null

    var onStatusChange: ((String) -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val PORT = 7110
    }

    fun start() {
        running = true
        Thread(::receiveLoop, "MonitorizeReceiver").start()
    }

    private fun receiveLoop() {
        if (hostIp.isNullOrEmpty()) {
            receiveLoopUsb()
        } else {
            receiveLoopWifi(hostIp)
        }
    }

    private fun receiveLoopWifi(targetIp: String) {
        try {
            onStatusChange?.invoke("Connecting to $targetIp (Control)…")
            var s: Socket? = null
            while (running && s == null) {
                try {
                    s = Socket(targetIp, PORT)
                } catch (e: Exception) {
                    Thread.sleep(1000)
                }
            }
            if (!running || s == null) return
            controlSocket = s

            onStatusChange?.invoke("Listening for UDP Stream on 7112…")
            udpSocket = DatagramSocket(7112)
            udpSocket!!.receiveBufferSize = 2 * 1024 * 1024
            udpSocket!!.soTimeout = 2000

            decoder.init(width, height, fps)
            onStatusChange?.invoke("")

            val buf = ByteArray(65535)
            val packet = DatagramPacket(buf, buf.size)
            
            val frameBuffer = ByteArrayOutputStream(1024 * 1024)
            var expectedSeq = -1
            var assembling = false

            while (running) {
                try {
                    udpSocket!!.receive(packet)
                } catch (e: java.net.SocketTimeoutException) {
                    continue
                }

                val length = packet.length
                if (length < 12) continue

                val seq = ((buf[2].toInt() and 0xFF) shl 8) or (buf[3].toInt() and 0xFF)
                
                if (expectedSeq != -1 && seq != expectedSeq) {
                    assembling = false
                    frameBuffer.reset()
                }
                expectedSeq = (seq + 1) % 65536

                val nalType = buf[12].toInt() and 0x1F
                
                if (nalType in 1..23) {
                    frameBuffer.reset()
                    frameBuffer.write(0x00)
                    frameBuffer.write(0x00)
                    frameBuffer.write(0x00)
                    frameBuffer.write(0x01)
                    frameBuffer.write(buf, 12, length - 12)
                    decoder.feedChunk(frameBuffer.toByteArray(), 0, frameBuffer.size())
                    frameBuffer.reset()
                } else if (nalType == 28) {
                    val fuHeader = buf[13].toInt() and 0xFF
                    val startBit = (fuHeader and 0x80) != 0
                    val endBit = (fuHeader and 0x40) != 0
                    val originalNalType = fuHeader and 0x1F
                    
                    if (startBit) {
                        assembling = true
                        frameBuffer.reset()
                        frameBuffer.write(0x00)
                        frameBuffer.write(0x00)
                        frameBuffer.write(0x00)
                        frameBuffer.write(0x01)
                        val nalHeader = (buf[12].toInt() and 0xE0) or originalNalType
                        frameBuffer.write(nalHeader)
                        frameBuffer.write(buf, 14, length - 14)
                    } else if (assembling) {
                        frameBuffer.write(buf, 14, length - 14)
                        if (endBit) {
                            decoder.feedChunk(frameBuffer.toByteArray(), 0, frameBuffer.size())
                            frameBuffer.reset()
                            assembling = false
                        }
                    }
                }
            }
        } catch (e: Exception) {
            if (running) {
                Log.e(TAG, "UDP Stream error", e)
                onStatusChange?.invoke("Error: ${e.message}")
            }
        } finally {
            try { controlSocket?.close() } catch (_: Exception) {}
            try { udpSocket?.close() } catch (_: Exception) {}
        }
    }

    private fun receiveLoopUsb() {
        try {
            serverSocket = ServerSocket(PORT)
            onStatusChange?.invoke("Waiting for USB connection…")

            val socket = serverSocket!!.accept()
            socket.tcpNoDelay = true
            socket.receiveBufferSize = 1024 * 1024

            onStatusChange?.invoke("Connected")

            decoder.init(width, height, fps)
            onStatusChange?.invoke("")

            val input = socket.getInputStream()
            val buf = ByteArray(4 * 1024 * 1024)
            var writePos = 0
            val readBuf = ByteArray(128 * 1024)

            while (running) {
                val bytesRead = input.read(readBuf)
                if (bytesRead <= 0) break

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
        } catch (e: Exception) {
            if (running) {
                Log.e(TAG, "Stream error", e)
                onStatusChange?.invoke("Error: ${e.message}")
            }
        } finally {
            try { serverSocket?.close() } catch (_: Exception) {}
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

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        try { controlSocket?.close() } catch (_: Exception) {}
        try { udpSocket?.close() } catch (_: Exception) {}
    }
}
"""

with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/StreamReceiver.kt", "w") as f:
    f.write(stream_receiver)
print("StreamReceiver.kt updated.")
