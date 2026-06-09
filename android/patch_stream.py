import java.io.File

with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/StreamReceiver.kt", "r") as f:
    content = f.read()


old_loop_pattern = "    private fun receiveLoop() {.*"
new_loop = '''    private fun receiveLoop() {
        if (hostIp.isNullOrEmpty()) {
            receiveLoopUsb()
        } else {
            receiveLoopWifi(hostIp)
        }
    }

    private fun receiveLoopWifi(targetIp: String) {
        var controlSocket: java.net.Socket? = null
        var udpSocket: java.net.DatagramSocket? = null
        try {
            onStatusChange?.invoke("Connecting to $targetIp (Control)…")
            var s: java.net.Socket? = null
            while (running && s == null) {
                try {
                    s = java.net.Socket(targetIp, 7110)
                } catch (e: Exception) {
                    Thread.sleep(1000)
                }
            }
            if (!running || s == null) return
            controlSocket = s

            onStatusChange?.invoke("Listening for UDP Stream on 7112…")
            udpSocket = java.net.DatagramSocket(7112)
            udpSocket.receiveBufferSize = 2 * 1024 * 1024
            udpSocket.soTimeout = 2000

            decoder.init(width, height, fps)
            onStatusChange?.invoke("")

            val buf = ByteArray(65535)
            val packet = java.net.DatagramPacket(buf, buf.size)
            
            val frameBuffer = java.io.ByteArrayOutputStream(1024 * 1024)
            var expectedSeq = -1
            var assembling = false

            while (running) {
                try {
                    udpSocket.receive(packet)
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
                android.util.Log.e(TAG, "UDP Stream error", e)
                onStatusChange?.invoke("Error: ${e.message}")
            }
        } finally {
            try { controlSocket?.close() } catch (_: Exception) {}
            try { udpSocket?.close() } catch (_: Exception) {}
        }
    }

    private fun receiveLoopUsb() {'''

import re




content = content.replace("    private fun receiveLoop() {", new_loop)



