package com.example.monitorize

import java.net.ServerSocket
import android.util.Log

class StreamReceiver(private val decoder: H264Decoder) {
    private var running = false
    private var serverSocket: ServerSocket? = null
    private val TAG = "StreamReceiver"
    var onStatusChange: ((String) -> Unit)? = null

    fun start() {
        running = true
        Thread {
            try {
                serverSocket = ServerSocket(7110)
                Log.d(TAG, "Server listening on 7110")
                onStatusChange?.invoke("Ready")
                
                while (running) {
                    val socket = serverSocket?.accept() ?: break
                    Log.d(TAG, "Connection accepted")
                    onStatusChange?.invoke("Streaming")
                    
                    try {
                        val inputStream = socket.getInputStream()
                        val buffer = ByteArray(128 * 1024)
                        
                        // Use 1280x720 for stability
                        decoder.init(1280, 720)

                        while (running) {
                            val read = inputStream.read(buffer)
                            if (read <= 0) break
                            decoder.decode(buffer, 0, read)
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Stream error", e)
                    } finally {
                        socket.close()
                        onStatusChange?.invoke("Ready")
                    }
                }
            } catch (e: Exception) {
                if (running) Log.e(TAG, "Server error", e)
            }
        }.start()
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (e: Exception) {}
        serverSocket = null
    }
}
