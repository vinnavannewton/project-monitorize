package com.example.monitorize

import android.util.Log
import java.net.ServerSocket

/**
 * Reads raw H.264 Annex B bytes from TCP and buffers them until complete NAL units
 * (Access Units) are found. This prevents feeding partial frames to MediaCodec,
 * which causes corruption during static scenes.
 *
 * Optimised: uses read/write pointers instead of arraycopy shifts, and a larger
 * read buffer for fewer syscalls.
 */
class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val fps: Int
) {

    private var running = false
    private var serverSocket: ServerSocket? = null

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
        try {
            serverSocket = ServerSocket(PORT)
            onStatusChange?.invoke("Waiting for connection…")

            val socket = serverSocket!!.accept()
            socket.tcpNoDelay = true
            socket.receiveBufferSize = 1024 * 1024

            onStatusChange?.invoke("Connected")

            decoder.init(width, height, fps)
            onStatusChange?.invoke("")

            val input = socket.getInputStream()

            // Ring buffer with read/write pointers — avoids expensive arraycopy shifts
            val buf = ByteArray(4 * 1024 * 1024)
            var writePos = 0
            val readBuf = ByteArray(128 * 1024)  // larger reads = fewer syscalls

            while (running) {
                val bytesRead = input.read(readBuf)
                if (bytesRead <= 0) break

                // Compact if we'd overflow
                if (writePos + bytesRead > buf.size) {
                    // This should rarely happen with 4MB buffer
                    Log.w(TAG, "Buffer near capacity, compacting")
                    writePos = 0
                }
                System.arraycopy(readBuf, 0, buf, writePos, bytesRead)
                writePos += bytesRead

                // Parse NAL units from [readStart .. writePos)
                var readStart = 0
                while (readStart < writePos - 4) {
                    // Find first start code
                    val sc1 = findStartCode(buf, readStart, writePos)
                    if (sc1 < 0) break

                    // Find next start code
                    val sc2 = findStartCode(buf, sc1 + 4, writePos)
                    if (sc2 < 0) {
                        // Incomplete NAL — shift remaining data to front and wait
                        val remaining = writePos - sc1
                        if (sc1 > 0) {
                            System.arraycopy(buf, sc1, buf, 0, remaining)
                        }
                        writePos = remaining
                        readStart = 0
                        break
                    }

                    // Complete NAL unit: [sc1 .. sc2)
                    decoder.feedChunk(buf, sc1, sc2 - sc1)
                    readStart = sc2
                }

                // If we consumed everything up to writePos
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

    /**
     * Find the next Annex B start code (0x00 0x00 0x00 0x01) starting from [from].
     * Returns the index of the start code, or -1 if not found before [limit]-3.
     */
    private fun findStartCode(buf: ByteArray, from: Int, limit: Int): Int {
        val end = limit - 3
        var i = from
        while (i < end) {
            // Fast skip: if current byte is not 0, skip ahead
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
    }
}
