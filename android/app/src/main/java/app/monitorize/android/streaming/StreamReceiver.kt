package app.monitorize.android.streaming

import android.util.Log
import app.monitorize.android.security.connectTls
import app.monitorize.android.security.readAsciiLine
import java.net.Socket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val fps: Int = 60,
    private val hostIp: String? = null,
    private val hostPort: Int = 7110,
    private val encrypted: Boolean = false,
    private val trustedFingerprint: String? = null,
    private val authToken: String? = null,
    private val videoTransport: String? = null,
    private val videoControlPort: Int = 0
) {
    private val running = AtomicBoolean(false)
    @Volatile private var worker: Thread? = null
    @Volatile
    private var controlSocket: Socket? = null
    @Volatile
    private var rtpSocket: DatagramSocket? = null
    private val idrRequestInFlight = AtomicBoolean(false)

    var onStatusChange: ((String) -> Unit)? = null
    var onDisconnect: (() -> Unit)? = null
    var onPairingRequired: (((String) -> Unit) -> Unit)? = null
    var onCredentials: ((String, String) -> Unit)? = null
    var onPlainTransportReady: (() -> Unit)? = null

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
                val target = hostIp.takeUnless { it.isNullOrEmpty() } ?: "127.0.0.1"
                if (target != "127.0.0.1" && videoTransport == "rtp-udp-v1") {
                    if (!receiveLoopRtp(target) && running.get()) {
                        Log.i(TAG, "RTP handshake unavailable; falling back to TCP/TLS")
                        receiveLoopWifi(target, forceEncrypted = true)
                    }
                } else {
                    receiveLoopWifi(target)
                }
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

    private fun receiveLoopRtp(targetIp: String): Boolean {
        val socket = DatagramSocket()
        rtpSocket = socket
        controlSocket = null
        socket.receiveBufferSize = 512 * 1024
        socket.soTimeout = 4
        try { socket.trafficClass = 0xC0 } catch (_: Exception) {}
        val host = InetAddress.getByName(targetIp)
        val controlPort = hostPort
        val hello = "MZRP1 {\"transport\":\"rtp-udp-v1\",\"port\":${socket.localPort}," +
            "\"fps\":$fps,\"width\":$width,\"height\":$height," +
            "\"decoderProfiles\":[\"high\",\"constrained-baseline\"]}"
        val helloBytes = hello.toByteArray(Charsets.UTF_8)
        Log.i(TAG, "RTP negotiation: UDP port ${socket.localPort}, target $targetIp:$controlPort")
        onStatusChange?.invoke("Negotiating low-latency UDP video…")
        val ready = try {
            Socket().use { control ->
                control.connect(InetSocketAddress(targetIp, controlPort), 1500)
                control.soTimeout = 1500
                control.tcpNoDelay = true
                control.getOutputStream().apply {
                    write(helloBytes)
                    write('\n'.code)
                    flush()
                }
                val response = readAsciiLine(control)
                Log.i(TAG, "RTP server reply: $response")
                response.startsWith("MZRP1 ") && response.contains("\"status\":\"ready\"")
            }
        } catch (e: Exception) {
            Log.e(TAG, "RTP TCP handshake failed: ${e.message}", e)
            false
        }
        if (!ready) {
            Log.w(TAG, "RTP handshake not ready — falling back")
            socket.close()
            return false
        }
        Log.i(TAG, "RTP handshake succeeded, starting receive loop")
        socket.soTimeout = 4
        onPlainTransportReady?.invoke()
        decoder.init(width, height, fps)
        onStatusChange?.invoke("")
        val assembler = RtpH264Assembler()
        val fecRecovery = RtpUlpFecRecovery()
        val buffer = ByteArray(2048)
        var waitingForIdr = true
        val frameDeadlineNanos = 1_500_000_000L / fps.coerceAtLeast(1)
        var expectedSequence = -1
        var lostPackets = 0
        var recoveredPackets = 0
        var incompleteFrames = 0
        var receivedPackets = 0
        var totalReceivedPackets = 0L
        var totalFramesDecoded = 0L
        var firstPacketLogged = false
        var lastStats = android.os.SystemClock.uptimeMillis()
        var noPacketDeadline = android.os.SystemClock.uptimeMillis() + 5000
        while (running.get()) {
            try {
                val packet = DatagramPacket(buffer, buffer.size)
                socket.receive(packet)
                val rtp = RtpH264Assembler.parse(packet.data, packet.length) ?: continue
                receivedPackets++
                totalReceivedPackets++
                noPacketDeadline = 0L
                if (!firstPacketLogged) {
                    firstPacketLogged = true
                    val nalType = if (rtp.payload.isNotEmpty()) rtp.payload[0].toInt() and 0x1f else -1
                    Log.i(TAG, "RTP first packet: seq=${rtp.sequence} ts=${rtp.timestamp} " +
                        "pt=${rtp.payloadType} marker=${rtp.marker} " +
                        "payloadSize=${rtp.payload.size} nalType=$nalType " +
                        "from ${packet.address}:${packet.port}")
                }
                if (expectedSequence < 0) {
                    expectedSequence = (rtp.sequence + 1) and 0xffff
                } else {
                    val gap = (rtp.sequence - expectedSequence) and 0xffff
                    if (gap == 0) {
                        expectedSequence = (expectedSequence + 1) and 0xffff
                    } else if (gap in 1..1024) {
                        lostPackets += gap
                        expectedSequence = (rtp.sequence + 1) and 0xffff
                    }
                }
                val mediaPacket = if (rtp.payloadType == 122) {
                    fecRecovery.recover(rtp)?.also { recoveredPackets++ } ?: continue
                } else {
                    fecRecovery.remember(rtp)
                    rtp
                }
                val frame = assembler.offer(mediaPacket)
                if (assembler.droppedFrame) {
                    requestIdrViaTcp(targetIp, controlPort, socket.localPort)
                    waitingForIdr = true
                    incompleteFrames++
                }
                if (frame != null) {
                    val isIdr = containsIdr(frame)
                    if (!waitingForIdr || isIdr) {
                        val fed = decoder.feedChunk(frame, 0, frame.size, isIdr)
                        if (fed) {
                            totalFramesDecoded++
                            if (totalFramesDecoded <= 3) {
                                Log.i(TAG, "RTP frame #$totalFramesDecoded fed to decoder: " +
                                    "size=${frame.size} idr=$isIdr")
                            }
                        } else {
                            Log.w(TAG, "RTP decoder rejected frame: size=${frame.size} idr=$isIdr")
                        }
                        if (isIdr) waitingForIdr = false
                    } else if (totalFramesDecoded == 0L) {
                        Log.d(TAG, "RTP skipping non-IDR frame while waiting for keyframe")
                    }
                }
            } catch (_: SocketTimeoutException) {
                if (noPacketDeadline > 0 && android.os.SystemClock.uptimeMillis() > noPacketDeadline) {
                    Log.w(TAG, "RTP no packets received within 5s — requesting IDR")
                    requestIdrViaTcp(targetIp, controlPort, socket.localPort)
                    noPacketDeadline = android.os.SystemClock.uptimeMillis() + 5000
                }
                if (assembler.expire(System.nanoTime(), frameDeadlineNanos)) {
                    waitingForIdr = true
                    incompleteFrames++
                    requestIdrViaTcp(targetIp, controlPort, socket.localPort)
                }
            }
            val statsNow = android.os.SystemClock.uptimeMillis()
            if (statsNow - lastStats >= 250) {
                val stats = decoder.takeStats()
                if (totalReceivedPackets < 100 || receivedPackets > 0) {
                    Log.d(TAG, "RTP stats: recv=$receivedPackets lost=$lostPackets " +
                        "recovered=$recoveredPackets incomplete=$incompleteFrames " +
                        "rendered=${stats.renderedFrames} totalFrames=$totalFramesDecoded")
                }
                receivedPackets = 0
                lostPackets = 0
                recoveredPackets = 0
                incompleteFrames = 0
                lastStats = statsNow
            }
        }
        Log.i(TAG, "RTP receive loop ended: totalPackets=$totalReceivedPackets " +
            "totalFrames=$totalFramesDecoded")
        socket.close()
        return true
    }

    
    private fun requestIdrViaTcp(hostIp: String, controlPort: Int, localUdpPort: Int) {
        if (!running.get()) return
        if (!idrRequestInFlight.compareAndSet(false, true)) return
        Thread({
            try {
                Socket().use { control ->
                    control.connect(InetSocketAddress(hostIp, controlPort), 1000)
                    control.soTimeout = 1000
                    control.tcpNoDelay = true
                    val hello = "MZRP1 {\"transport\":\"rtp-udp-v1\",\"port\":$localUdpPort," +
                        "\"fps\":$fps,\"width\":$width,\"height\":$height," +
                        "\"decoderProfiles\":[\"high\",\"constrained-baseline\"]}"
                    control.getOutputStream().apply {
                        write(hello.toByteArray(Charsets.UTF_8))
                        write('\n'.code)
                        flush()
                    }
                    try { readAsciiLine(control) } catch (_: Exception) {}
                }
            } catch (_: Exception) {
            } finally {
                idrRequestInFlight.set(false)
            }
        }, "MonitorizeIdrRequest").start()
    }

    private fun containsIdr(frame: ByteArray): Boolean {
        for (index in 0 until frame.size - 4) {
            if (frame[index].toInt() == 0 && frame[index + 1].toInt() == 0 &&
                ((frame[index + 2].toInt() == 1) ||
                    (frame[index + 2].toInt() == 0 && frame[index + 3].toInt() == 1))) {
                val header = if (frame[index + 2].toInt() == 1) index + 3 else index + 4
                if (header < frame.size && frame[header].toInt() and 0x1f == 5) return true
            }
        }
        return false
    }

    private fun receiveLoopWifi(targetIp: String, forceEncrypted: Boolean = false) {
        val streamType = if (targetIp == "127.0.0.1") "USB" else "WiFi"
        val secureTransport = encrypted || (forceEncrypted && streamType == "WiFi")
        var expectedFingerprint = trustedFingerprint
        var token = authToken
        var hasConnected = false
        while (running.get()) {
            onStatusChange?.invoke(if (streamType == "USB") "Waiting for USB connection…" else "Connecting to $targetIp:$hostPort…")
            var socket: Socket? = null
            while (running.get() && socket == null) {
                try {
                    if (secureTransport) {
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
            socket.receiveBufferSize = 256 * 1024
            try {
                
                socket.trafficClass = 0xC0
            } catch (e: Exception) {
                Log.w(TAG, "Failed to set socket traffic class: ${e.message}")
            }
            controlSocket = socket

            onStatusChange?.invoke(if (hasConnected) "Reconnected" else "Connected")
            decoder.init(width, height, fps)
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
        val codecConfig = ByteArray(256 * 1024)
        var accessUnitSize = 0
        var accessUnitHasVcl = false
        var accessUnitHasIdr = false
        var accessUnitHasConfig = false
        var codecConfigSize = 0
        var waitingForKeyFrame = true
        var idleReads = 0
        var decoderFailed = false

        fun flushAccessUnit() {
            if (decoderFailed) {
                accessUnitSize = 0
                accessUnitHasVcl = false
                accessUnitHasIdr = false
                accessUnitHasConfig = false
                return
            }
            if (accessUnitSize > 0 && accessUnitHasVcl) {
                if (waitingForKeyFrame && !accessUnitHasIdr) {
                    accessUnitSize = 0
                    accessUnitHasVcl = false
                    accessUnitHasIdr = false
                    accessUnitHasConfig = false
                    return
                }
                if (waitingForKeyFrame && !accessUnitHasConfig && codecConfigSize > 0) {
                    if (codecConfigSize + accessUnitSize <= accessUnit.size) {
                        System.arraycopy(accessUnit, 0, accessUnit, codecConfigSize, accessUnitSize)
                        System.arraycopy(codecConfig, 0, accessUnit, 0, codecConfigSize)
                        accessUnitSize += codecConfigSize
                        accessUnitHasConfig = true
                    } else {
                        Log.w(TAG, "$streamType codec config too large to prepend, using IDR only")
                    }
                }
                waitingForKeyFrame = false
                if (!decoder.feedChunk(accessUnit, 0, accessUnitSize, accessUnitHasIdr)) {
                    Log.w(TAG, "$streamType decoder rejected frame; reconnecting")
                    decoderFailed = true
                }
            }
            accessUnitSize = 0
            accessUnitHasVcl = false
            accessUnitHasIdr = false
            accessUnitHasConfig = false
        }

        fun rememberCodecConfig(nalStart: Int, nalEnd: Int, nalType: Int) {
            val nalSize = nalEnd - nalStart
            if (nalSize <= 0 || nalSize > codecConfig.size) return
            if (nalType == 7) {
                codecConfigSize = 0
            }
            if (codecConfigSize + nalSize > codecConfig.size) {
                codecConfigSize = 0
                if (nalSize > codecConfig.size) return
            }
            System.arraycopy(buf, nalStart, codecConfig, codecConfigSize, nalSize)
            codecConfigSize += nalSize
        }

        fun appendNalToAccessUnit(nalStart: Int, nalEnd: Int) {
            if (decoderFailed) return
            val startCodeLen = startCodeLength(buf, nalStart, nalEnd)
            val nalHeader = nalStart + startCodeLen
            if (nalHeader >= nalEnd) return

            val nalType = buf[nalHeader].toInt() and 0x1F
            val isCodecConfig = nalType == 7 || nalType == 8
            if (isCodecConfig) {
                rememberCodecConfig(nalStart, nalEnd, nalType)
            }
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
            if (isCodecConfig) accessUnitHasConfig = true
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
        try { rtpSocket?.close() } catch (_: Exception) {}
        rtpSocket = null
    }

    @Synchronized
    fun stop() {
        if (!running.getAndSet(false)) return
        cleanup()
        worker?.interrupt()
        if (Thread.currentThread() !== worker) {
            try {
                worker?.join(500)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }
        worker = null
    }
}
