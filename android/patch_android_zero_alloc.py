import re

with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/StreamReceiver.kt", "r") as f:
    content = f.read()

old_loop = '''            val buf = ByteArray(65535)
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
            }'''

new_loop = '''            val buf = ByteArray(65535)
            val packet = DatagramPacket(buf, buf.size)
            
            // ZERO ALLOCATION during loop to prevent GC stuttering
            val frameBuffer = ByteArray(2 * 1024 * 1024)
            var frameLen = 0
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
                    // Packet loss detected. Drop current accumulation.
                    assembling = false
                    frameLen = 0
                }
                expectedSeq = (seq + 1) % 65536

                val nalType = buf[12].toInt() and 0x1F
                
                if (nalType in 1..23) {
                    frameLen = 0
                    frameBuffer[frameLen++] = 0x00
                    frameBuffer[frameLen++] = 0x00
                    frameBuffer[frameLen++] = 0x00
                    frameBuffer[frameLen++] = 0x01
                    val pLen = length - 12
                    System.arraycopy(buf, 12, frameBuffer, frameLen, pLen)
                    frameLen += pLen
                    decoder.feedChunk(frameBuffer, 0, frameLen)
                    frameLen = 0
                } else if (nalType == 28) {
                    val fuHeader = buf[13].toInt() and 0xFF
                    val startBit = (fuHeader and 0x80) != 0
                    val endBit = (fuHeader and 0x40) != 0
                    val originalNalType = fuHeader and 0x1F
                    
                    if (startBit) {
                        assembling = true
                        frameLen = 0
                        frameBuffer[frameLen++] = 0x00
                        frameBuffer[frameLen++] = 0x00
                        frameBuffer[frameLen++] = 0x00
                        frameBuffer[frameLen++] = 0x01
                        val nalHeader = ((buf[12].toInt() and 0xE0) or originalNalType).toByte()
                        frameBuffer[frameLen++] = nalHeader
                        val pLen = length - 14
                        System.arraycopy(buf, 14, frameBuffer, frameLen, pLen)
                        frameLen += pLen
                    } else if (assembling) {
                        val pLen = length - 14
                        if (frameLen + pLen <= frameBuffer.size) {
                            System.arraycopy(buf, 14, frameBuffer, frameLen, pLen)
                            frameLen += pLen
                        } else {
                            assembling = false
                            frameLen = 0
                        }
                        if (endBit && assembling) {
                            decoder.feedChunk(frameBuffer, 0, frameLen)
                            frameLen = 0
                            assembling = false
                        }
                    }
                }
            }'''

content = content.replace(old_loop, new_loop)

# Also need to remove import java.io.ByteArrayOutputStream if present
content = content.replace("import java.io.ByteArrayOutputStream", "")

with open("/home/vinnavan/user/MegaProjects/Monitorize/android/app/src/main/java/com/example/monitorize/StreamReceiver.kt", "w") as f:
    f.write(content)
print("Android StreamReceiver.kt updated for zero-allocation.")
