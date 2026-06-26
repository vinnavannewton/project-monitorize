package com.example.monitorize.streaming

import android.util.Log
import com.example.monitorize.security.connectTls
import com.example.monitorize.security.readAsciiLine
import java.net.Socket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val hostIp: String? = null,
    private val hostPort: Int = 7110,
    private val encrypted: Boolean = false,
    private val trustedFingerprint: String? = null,
    private val authToken: String? = null
) {
    private val running = AtomicBoolean(false)
    @Volatile private var worker: Thread? = null
    @Volatile
    private var controlSocket: Socket? = null

    var onStatusChange: ((String) -> Unit)? = null
    var onDisconnect: (() -> Unit)? = null
    var onPairingRequired: (((String) -> Unit) -> Unit)? = null
    var onCredentials: ((String, String) -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val MAX_STREAM_BUFFER = 4 * 1024 * 1024
        private const val MAX_ACCESS_UNIT = 2 * 1024 * 1024
        private const val CONNECT_TIMEOUT_MS = 2500
        private const val STREAM_IDLE_TIMEOUT_MS = 5000
        private const val MAX_IDLE_READS = 6
        private const val RETRY_DELAY_MS = 750L
    }

    @Synchronized
    fun start() {
        if (!running.compareAndSet(false, true)) return
        worker = Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
            try {
                receiveLoopWifi(hostIp.takeUnless { it.isNullOrEmpty() } ?: "127.0.0.1")
            } catch (e: Exception) {
                if (running.get()) {
                    Log.e(TAG, "Receiver stopped unexpectedly", e)
                    onDisconnect?.invoke()
                }
            } finally {
                running.set(false)
                cleanup()
                worker = null
            }
        }, "MonitorizeReceiver").also { it.start() }
    }

    private fun receiveLoopWifi(targetIp: String) {
        val streamType = if (targetIp == "127.0.0.1") "USB" else "WiFi"
        var expectedFingerprint = trustedFingerprint
        var token = authToken
        var hasConnected = false
        while (running.get()) {
            onStatusChange?.invoke(if (streamType == "USB") "Waiting for USB connection…" else "Connecting to $targetIp:$hostPort…")
            var socket: Socket? = null
            while (running.get() && socket == null) {
                try {
                    if (encrypted && streamType == "WiFi") {
                        val secure = connectTls(targetIp, hostPort, expectedFingerprint)
                        socket = secure.socket
                        val output = secure.socket.outputStream
                        if (token == null) {
                            val submitted = ArrayBlockingQueue<String>(1)
                            onPairingRequired?.invoke { submitted.offer(it) }
                                ?: throw SecurityException("Pairing UI unavailable")
                            val code = submitted.poll(30, TimeUnit.SECONDS)
                                ?: throw SecurityException("Pairing timed out")
                            if (code.isEmpty()) {
                                running.set(false)
                                socket.close()
                                return
                            }
                            output.write("PAIR $code\n".toByteArray(Charsets.US_ASCII))
                        } else {
                            output.write("AUTH $token\n".toByteArray(Charsets.US_ASCII))
                        }
                        output.flush()
                        val response = readAsciiLine(secure.socket)
                        if (!response.startsWith("OK")) {
                            token = null
                            socket.close()
                            socket = null
                            continue
                        }
                        if (token == null) {
                            token = response.substringAfter("OK ", "").takeIf { it.length == 64 }
                                ?: throw SecurityException("Invalid pairing response")
                        }
                        expectedFingerprint = secure.fingerprint
                        onCredentials?.invoke(secure.fingerprint, token!!)
                    } else {
                        socket = Socket()
                        socket.connect(InetSocketAddress(targetIp, hostPort), CONNECT_TIMEOUT_MS)
                    }
                } catch (e: SecurityException) {
                    expectedFingerprint = null
                    token = null
                    onCredentials?.invoke("", "")
                    try { socket?.close() } catch (_: Exception) {}
                    socket = null
                    sleepBeforeRetry()
                } catch (e: Exception) {
                    try { socket?.close() } catch (_: Exception) {}
                    socket = null
                    sleepBeforeRetry()
                }
            }
            if (socket == null || !running.get()) break

            socket.tcpNoDelay = true
            socket.keepAlive = true
            socket.soTimeout = STREAM_IDLE_TIMEOUT_MS
            socket.receiveBufferSize = 1024 * 1024
            try {
                
                socket.trafficClass = 0xC0
            } catch (e: Exception) {
                Log.w(TAG, "Failed to set socket traffic class: ${e.message}")
            }
            controlSocket = socket

            onStatusChange?.invoke(if (hasConnected) "Reconnected" else "Connected")
            decoder.init(width, height)
            onStatusChange?.invoke("")
            hasConnected = true

            processStreamLoop(socket.getInputStream(), streamType)
            try { socket.close() } catch (e: Exception) {}
            if (controlSocket === socket) controlSocket = null
            if (running.get()) {
                onStatusChange?.invoke("Connection lost. Reconnecting…")
                sleepBeforeRetry()
            }
        }
    }

    private fun sleepBeforeRetry() {
        try {
            Thread.sleep(RETRY_DELAY_MS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun processStreamLoop(input: java.io.InputStream, streamType: String) {
        val buf = ByteArray(MAX_STREAM_BUFFER)
        var writePos = 0
        val readBuf = ByteArray(128 * 1024)
        val accessUnit = ByteArray(MAX_ACCESS_UNIT)
        var accessUnitSize = 0
        var accessUnitHasVcl = false
        var accessUnitHasIdr = false
        var idleReads = 0
        var decoderFailed = false

        fun flushAccessUnit() {
            if (decoderFailed) {
                accessUnitSize = 0
                accessUnitHasVcl = false
                accessUnitHasIdr = false
                return
            }
            if (accessUnitSize > 0 && accessUnitHasVcl) {
                if (!decoder.feedChunk(accessUnit, 0, accessUnitSize, accessUnitHasIdr)) {
                    Log.w(TAG, "$streamType decoder rejected frame; reconnecting")
                    decoderFailed = true
                }
            }
            accessUnitSize = 0
            accessUnitHasVcl = false
            accessUnitHasIdr = false
        }

        fun appendNalToAccessUnit(nalStart: Int, nalEnd: Int) {
            if (decoderFailed) return
            val startCodeLen = startCodeLength(buf, nalStart, nalEnd)
            val nalHeader = nalStart + startCodeLen
            if (nalHeader >= nalEnd) return

            val nalType = buf[nalHeader].toInt() and 0x1F
            val isVcl = nalType in 1..5
            val startsNewAccessUnit = accessUnitHasVcl && (
                nalType in 6..9 ||
                    (isVcl && isFirstSlice(buf, nalHeader + 1, nalEnd))
                )

            if (startsNewAccessUnit) {
                flushAccessUnit()
            }

            val nalSize = nalEnd - nalStart
            if (nalSize > accessUnit.size) {
                Log.w(TAG, "$streamType NAL too large ($nalSize bytes), dropping")
                accessUnitSize = 0
                accessUnitHasVcl = false
                accessUnitHasIdr = false
                return
            }
            if (accessUnitSize + nalSize > accessUnit.size) {
                flushAccessUnit()
                if (decoderFailed) return
                if (nalSize > accessUnit.size) return
            }

            System.arraycopy(buf, nalStart, accessUnit, accessUnitSize, nalSize)
            accessUnitSize += nalSize
            if (isVcl) accessUnitHasVcl = true
            if (nalType == 5) accessUnitHasIdr = true
        }

        while (running.get()) {
            val bytesRead = try {
                input.read(readBuf)
            } catch (e: SocketTimeoutException) {
                idleReads++
                if (idleReads < MAX_IDLE_READS) {
                    onStatusChange?.invoke("Waiting for frames…")
                    continue
                }
                Log.w(TAG, "$streamType stream idle for ${STREAM_IDLE_TIMEOUT_MS * MAX_IDLE_READS}ms")
                -1
            } catch (e: Exception) {
                if (running.get()) Log.w(TAG, "$streamType stream read error: ${e.message}")
                -1
            }
            
            if (bytesRead <= 0) {
                if (running.get()) Log.w(TAG, "$streamType stream ended. Reconnecting…")
                break
            }

            if (idleReads > 0) {
                idleReads = 0
                onStatusChange?.invoke("")
            }

            if (writePos + bytesRead > buf.size) {
                val keep = minOf(writePos, 4)
                if (keep > 0) {
                    System.arraycopy(buf, writePos - keep, buf, 0, keep)
                }
                writePos = keep
            }
            System.arraycopy(readBuf, 0, buf, writePos, bytesRead)
            writePos += bytesRead

            var readStart = 0
            while (readStart < writePos - 3) {
                val sc1 = findStartCode(buf, readStart, writePos)
                if (sc1 < 0) {
                    val keep = minOf(writePos, 3)
                    if (keep > 0) {
                        System.arraycopy(buf, writePos - keep, buf, 0, keep)
                    }
                    writePos = keep
                    readStart = 0
                    break
                }

                val sc2 = findStartCode(buf, sc1 + startCodeLength(buf, sc1, writePos), writePos)
                if (sc2 < 0) {
                    val remaining = writePos - sc1
                    if (sc1 > 0) {
                        System.arraycopy(buf, sc1, buf, 0, remaining)
                    }
                    writePos = remaining
                    readStart = 0
                    break
                }

                appendNalToAccessUnit(sc1, sc2)
                if (decoderFailed) break
                readStart = sc2
            }
            if (decoderFailed) break

            if (readStart > 0 && readStart < writePos) {
                val remaining = writePos - readStart
                System.arraycopy(buf, readStart, buf, 0, remaining)
                writePos = remaining
            } else if (readStart >= writePos) {
                writePos = 0
            }
        }

        flushAccessUnit()
    }

    private fun findStartCode(buf: ByteArray, from: Int, limit: Int): Int {
        val end = limit - 3
        var i = from
        while (i <= end) {
            if (buf[i].toInt() != 0) {
                i++
                continue
            }
            if (buf[i + 1].toInt() == 0 && buf[i + 2].toInt() == 1) {
                return i
            }
            if (i + 3 < limit &&
                buf[i + 1].toInt() == 0 &&
                buf[i + 2].toInt() == 0 &&
                buf[i + 3].toInt() == 1
            ) {
                return i
            }
            i++
        }
        return -1
    }

    private fun startCodeLength(buf: ByteArray, index: Int, limit: Int): Int {
        return if (index + 3 < limit &&
            buf[index].toInt() == 0 &&
            buf[index + 1].toInt() == 0 &&
            buf[index + 2].toInt() == 0 &&
            buf[index + 3].toInt() == 1
        ) {
            4
        } else {
            3
        }
    }

    private fun isFirstSlice(buf: ByteArray, rbspStart: Int, limit: Int): Boolean {
        return H264BitReader(buf, rbspStart, limit).readUnsignedExpGolomb()?.let { it == 0 } ?: true
    }

    private class H264BitReader(
        private val data: ByteArray,
        private var pos: Int,
        private val limit: Int
    ) {
        private var currentByte = 0
        private var bitsLeft = 0
        private var zeroCount = 0

        fun readUnsignedExpGolomb(): Int? {
            var leadingZeros = 0
            while (true) {
                val bit = readBit() ?: return null
                if (bit == 1) break
                leadingZeros++
                if (leadingZeros > 30) return null
            }

            var value = (1 shl leadingZeros) - 1
            for (i in 0 until leadingZeros) {
                val bit = readBit() ?: return null
                value += bit shl (leadingZeros - i - 1)
            }
            return value
        }

        private fun readBit(): Int? {
            if (bitsLeft == 0) {
                currentByte = readByteSkippingEmulation() ?: return null
                bitsLeft = 8
            }

            bitsLeft--
            return (currentByte shr bitsLeft) and 1
        }

        private fun readByteSkippingEmulation(): Int? {
            while (pos < limit) {
                val value = data[pos++].toInt() and 0xFF
                if (zeroCount >= 2 && value == 0x03) {
                    zeroCount = 0
                    continue
                }

                zeroCount = if (value == 0) zeroCount + 1 else 0
                return value
            }
            return null
        }
    }

    private fun cleanup() {
        try { controlSocket?.close() } catch (_: Exception) {}
        controlSocket = null
    }

    @Synchronized
    fun stop() {
        if (!running.getAndSet(false)) return
        cleanup()
        worker?.interrupt()
        if (Thread.currentThread() !== worker) {
            try {
                worker?.join(2000)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }
        worker = null
    }
}
