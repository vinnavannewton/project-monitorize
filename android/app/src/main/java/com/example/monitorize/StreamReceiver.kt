package com.example.monitorize

import android.util.Log
import java.net.ServerSocket

/**
 * Reads raw H.264 Annex B bytes from TCP and buffers them until complete NAL units
 * (Access Units) are found. This prevents feeding partial frames to MediaCodec,
 * which causes corruption during static scenes.
 */
class StreamReceiver(private val decoder: H264Decoder) {

    private var running = false
    private var serverSocket: ServerSocket? = null

    var onStatusChange: ((String) -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val PORT = 7110

        // Must match linux/monitorize_fallback.py
        private const val STREAM_WIDTH  = 1280
        private const val STREAM_HEIGHT = 800
        private const val STREAM_FPS    = 60
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
            socket.receiveBufferSize = 512 * 1024
            onStatusChange?.invoke("Connected")

            decoder.init(STREAM_WIDTH, STREAM_HEIGHT, STREAM_FPS)
            onStatusChange?.invoke("Stream: ${STREAM_WIDTH}×${STREAM_HEIGHT} @ ${STREAM_FPS}fps")

            val input = socket.getInputStream()

            // A buffer to hold incoming data until we find a full frame
            val ringBuffer = ByteArray(2 * 1024 * 1024)
            var bufferLength = 0
            val readBuffer = ByteArray(64 * 1024)

            while (running) {
                val bytesRead = input.read(readBuffer)
                if (bytesRead <= 0) break

                // Append new data to our ring buffer
                if (bufferLength + bytesRead > ringBuffer.size) {
                    // This should not happen with normal H.264 desktop streams
                    // unless the decoder falls way behind or the stream is huge.
                    Log.w(TAG, "Buffer overflow, resetting")
                    bufferLength = 0
                }
                System.arraycopy(readBuffer, 0, ringBuffer, bufferLength, bytesRead)
                bufferLength += bytesRead

                // Search for Annex B start codes (0x00 00 00 01)
                var searchIndex = 0
                while (searchIndex < bufferLength - 4) {
                    if (ringBuffer[searchIndex] == 0.toByte() &&
                        ringBuffer[searchIndex + 1] == 0.toByte() &&
                        ringBuffer[searchIndex + 2] == 0.toByte() &&
                        ringBuffer[searchIndex + 3] == 1.toByte()
                    ) {
                        // We found a start code. Find the NEXT start code to get a complete unit.
                        var nextStartIndex = -1
                        for (i in searchIndex + 4 until bufferLength - 3) {
                            if (ringBuffer[i] == 0.toByte() &&
                                ringBuffer[i + 1] == 0.toByte() &&
                                ringBuffer[i + 2] == 0.toByte() &&
                                ringBuffer[i + 3] == 1.toByte()) {
                                nextStartIndex = i
                                break
                            }
                        }

                        if (nextStartIndex != -1) {
                            // We found a complete NAL unit between searchIndex and nextStartIndex
                            val frameSize = nextStartIndex - searchIndex

                            // Send exactly one complete access unit to the decoder
                            decoder.feedChunk(ringBuffer, searchIndex, frameSize)

                            // Move the remaining unparsed bytes to the front of the buffer
                            val remaining = bufferLength - nextStartIndex
                            System.arraycopy(ringBuffer, nextStartIndex, ringBuffer, 0, remaining)
                            bufferLength = remaining
                            searchIndex = 0 // Reset search to start of shifted buffer
                        } else {
                            // No next start code found yet. We need to read more from TCP.
                            break
                        }
                    } else {
                        searchIndex++
                    }
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

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
    }
}
