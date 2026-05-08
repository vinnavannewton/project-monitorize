package com.example.monitorize

import java.net.ServerSocket
import java.io.InputStream

class StreamReceiver(private val decoder: H264Decoder) {

    private var running = false
    private var serverSocket: ServerSocket? = null
    var onStatusChange: ((String) -> Unit)? = null

    fun start() {
        running = true
        Thread {
            try {
                serverSocket = ServerSocket(7110)
                onStatusChange?.invoke("Waiting for connection...")

                while (running) {
                    val socket = serverSocket?.accept() ?: break
                    onStatusChange?.invoke("Connected: ${socket.inetAddress}")

                    try {
                        val inputStream = socket.getInputStream()
                        val buffer = ByteArray(65536)
                        
                        // Initialize decoder with expected resolution
                        decoder.init(1920, 1080)

                        while (running) {
                            val bytesRead = inputStream.read(buffer)
                            if (bytesRead == -1) break
                            
                            decoder.decode(buffer, 0, bytesRead, System.nanoTime() / 1000)
                        }
                    } catch (e: Exception) {
                        onStatusChange?.invoke("Disconnected: ${e.message}")
                    } finally {
                        socket.close()
                    }
                }
            } catch (e: Exception) {
                if (running) onStatusChange?.invoke("Error: ${e.message}")
            }
        }.start()
    }

    fun stop() {
        running = false
        serverSocket?.close()
        serverSocket = null
    }
}
