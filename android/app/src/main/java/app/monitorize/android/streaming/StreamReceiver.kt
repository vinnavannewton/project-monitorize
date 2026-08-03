package app.monitorize.android.streaming

import android.util.Log
import java.io.ByteArrayOutputStream
import java.net.Socket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.atomic.AtomicBoolean

private const val RTP_TRANSPORT = "rtp-udp-v1"
private const val IDR_REQUEST_COOLDOWN_MS = 1_000L
private const val HARD_IDR_RETRY_MS = 250L
private const val MAX_CAPTURE_TO_RENDER_NS = 1_000_000_000L

internal data class RtpStreamConfig(
    val width: Int,
    val height: Int,
    val fps: Int,
    val fecPayloadType: Int = 122,
    val fecPercent: Int = 0,
)

internal fun rtpFrameDeadlineNanos(fps: Int): Long {
    return (6_000_000_000L / fps.coerceAtLeast(1))
        .coerceIn(100_000_000L, 250_000_000L)
}

internal fun recoveryIdrAllowed(
    lastRequestMs: Long,
    nowMs: Long,
    cooldownMs: Long = IDR_REQUEST_COOLDOWN_MS,
): Boolean {
    return nowMs - lastRequestMs >= cooldownMs
}

internal fun percentile95(values: List<Float>): Float {
    if (values.isEmpty()) return 0f
    val sorted = values.sorted()
    return sorted[((sorted.size * 95 + 99) / 100 - 1).coerceIn(sorted.indices)]
}

internal fun buildRtpControlMessage(
    localUdpPort: Int,
    fps: Int,
    width: Int,
    height: Int,
    requestIdr: Boolean = false,
): String {
    val type = if (requestIdr) "idr" else "start"
    return "MZRP1 {\"transport\":\"$RTP_TRANSPORT\",\"port\":$localUdpPort," +
        "\"type\":\"$type\"," +
        "\"fps\":$fps,\"width\":$width,\"height\":$height," +
        "\"decoderProfiles\":[\"high\",\"constrained-baseline\"]," +
        "\"fecModes\":[\"ulp-rfc5109\"]}"
}

data class StreamStats(
    val receivedKbps: Int = 0,
    val packetsPerSecond: Int = 0,
    val lossPercent: Float = 0f,
    val incompleteFrames: Int = 0,
    val inputFps: Float = 0f,
    val decodedFps: Float = 0f,
    val renderedFps: Float = 0f,
    val decodeMs: Float = 0f,
    val renderMs: Float = 0f,
    val queueDepth: Int = 0,
    val decoderDroppedFrames: Int = 0,
    val lostPackets: Int = 0,
    val inputFrames: Int = 0,
    val decodedFrames: Int = 0,
    val renderedFrames: Int = 0,
    val measurementMs: Long = 0,
    val mediaPackets: Int = 0,
    val fecPackets: Int = 0,
    val fecRecovered: Int = 0,
    val fecUnrecoverable: Int = 0,
    val residualLost: Int = 0,
    val assemblyP95Ms: Float = 0f,
    val lateFrames: Int = 0,
    internal val assemblySamplesMs: List<Float> = emptyList(),
    val endToEndMs: Float? = null,
    val clockErrorMs: Float? = null,
)

internal data class RenderedRtpFrame(val timestamp: Long, val renderedAtNs: Long)

internal class RtpClockSync {
    private val lock = Any()
    private var rendered: RenderedRtpFrame? = null
    private var estimate: Pair<Float, Float>? = null

    fun recordRendered(timestamp: Long, renderedAtNs: Long) = synchronized(lock) {
        rendered = RenderedRtpFrame(timestamp, renderedAtNs)
    }

    fun renderedFrame(): RenderedRtpFrame? = synchronized(lock) { rendered }

    fun latest(): Pair<Float, Float>? = synchronized(lock) { estimate }

    fun applyResponse(
        clientSentNs: Long, clientReceivedNs: Long, response: String,
        frame: RenderedRtpFrame,
    ) {
        val hostReceivedNs = clockField(response, "hostRecvNs") ?: return
        val hostSentNs = clockField(response, "hostSendNs") ?: return
        val captureNs = clockField(response, "captureNs") ?: return
        if (clockField(response, "rtpTimestamp") != frame.timestamp) return
        val roundTripNs = clientReceivedNs - clientSentNs - (hostSentNs - hostReceivedNs)
        if (roundTripNs < 0) return
        val hostMinusClientNs = (
            (hostReceivedNs - clientSentNs) + (hostSentNs - clientReceivedNs)
        ) / 2
        val endToEndNs = frame.renderedAtNs - captureNs + hostMinusClientNs
        if (endToEndNs !in 0..MAX_CAPTURE_TO_RENDER_NS) return
        synchronized(lock) {
            estimate = endToEndNs / 1_000_000f to roundTripNs / 2_000_000f
        }
    }
}

private fun clockField(message: String, name: String): Long? =
    Regex("\\\"$name\\\"\\s*:\\s*(-?\\d+)").find(message)
        ?.groupValues?.get(1)?.toLongOrNull()

internal class RtpFeedbackAccumulator(private val periodMs: Long = 1_000) {
    private var elapsedMs = 0L
    private var receivedBytes = 0L
    private var receivedPackets = 0
    private var lostPackets = 0
    private var incompleteFrames = 0
    private var inputFrames = 0
    private var decodedFrames = 0
    private var renderedFrames = 0
    private var decodeMicros = 0L
    private var renderMicros = 0L
    private var decoderDroppedFrames = 0
    private var queueDepth = 0
    private var mediaPackets = 0
    private var fecPackets = 0
    private var fecRecovered = 0
    private var fecUnrecoverable = 0
    private var residualLost = 0
    private val assemblySamplesMs = ArrayList<Float>()
    private var lateFrames = 0
    private var endToEndMs: Float? = null
    private var clockErrorMs: Float? = null

    fun add(
        window: StreamStats,
        windowReceivedBytes: Long,
        windowReceivedPackets: Int,
        windowLostPackets: Int,
    ): StreamStats? {
        elapsedMs += window.measurementMs
        receivedBytes += windowReceivedBytes
        receivedPackets += windowReceivedPackets
        lostPackets += windowLostPackets
        mediaPackets += window.mediaPackets
        fecPackets += window.fecPackets
        fecRecovered += window.fecRecovered
        fecUnrecoverable += window.fecUnrecoverable
        residualLost += window.residualLost
        assemblySamplesMs.addAll(window.assemblySamplesMs)
        lateFrames += window.lateFrames
        window.endToEndMs?.let { endToEndMs = it }
        window.clockErrorMs?.let { clockErrorMs = it }
        incompleteFrames += window.incompleteFrames
        inputFrames += window.inputFrames
        decodedFrames += window.decodedFrames
        renderedFrames += window.renderedFrames
        decodeMicros += (window.decodeMs * 1_000 * window.decodedFrames).toLong()
        renderMicros += (window.renderMs * 1_000 * window.renderedFrames).toLong()
        decoderDroppedFrames += window.decoderDroppedFrames
        queueDepth = maxOf(queueDepth, window.queueDepth)
        if (elapsedMs < periodMs) return null

        val result = StreamStats(
            receivedKbps = ((receivedBytes * 8) / elapsedMs).toInt(),
            packetsPerSecond = ((receivedPackets * 1_000L) / elapsedMs).toInt(),
            lossPercent = residualLost * 100f /
                (mediaPackets + fecRecovered + residualLost).coerceAtLeast(1),
            incompleteFrames = incompleteFrames,
            inputFps = inputFrames * 1_000f / elapsedMs,
            decodedFps = decodedFrames * 1_000f / elapsedMs,
            renderedFps = renderedFrames * 1_000f / elapsedMs,
            decodeMs = if (decodedFrames == 0) 0f else decodeMicros / decodedFrames / 1_000f,
            renderMs = if (renderedFrames == 0) 0f else renderMicros / renderedFrames / 1_000f,
            queueDepth = queueDepth,
            decoderDroppedFrames = decoderDroppedFrames,
            lostPackets = lostPackets,
            inputFrames = inputFrames,
            decodedFrames = decodedFrames,
            renderedFrames = renderedFrames,
            measurementMs = elapsedMs,
            mediaPackets = mediaPackets,
            fecPackets = fecPackets,
            fecRecovered = fecRecovered,
            fecUnrecoverable = fecUnrecoverable,
            residualLost = residualLost,
            assemblyP95Ms = percentile95(assemblySamplesMs),
            lateFrames = lateFrames,
            assemblySamplesMs = assemblySamplesMs.toList(),
            endToEndMs = endToEndMs,
            clockErrorMs = clockErrorMs,
        )
        reset()
        return result
    }

    private fun reset() {
        elapsedMs = 0
        receivedBytes = 0
        receivedPackets = 0
        lostPackets = 0
        incompleteFrames = 0
        inputFrames = 0
        decodedFrames = 0
        renderedFrames = 0
        decodeMicros = 0
        renderMicros = 0
        decoderDroppedFrames = 0
        queueDepth = 0
        mediaPackets = 0
        fecPackets = 0
        fecRecovered = 0
        fecUnrecoverable = 0
        residualLost = 0
        assemblySamplesMs.clear()
        lateFrames = 0
        endToEndMs = null
        clockErrorMs = null
    }
}

internal fun parseRtpReady(response: String): RtpStreamConfig? {
    if (!response.startsWith("MZRP1 ")) return null
    val ready = response.removePrefix("MZRP1 ")
    fun textField(name: String) = Regex("\\\"$name\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"")
        .find(ready)?.groupValues?.get(1)
    fun integerField(name: String) = Regex("\\\"$name\\\"\\s*:\\s*(\\d+)")
        .find(ready)?.groupValues?.get(1)?.toIntOrNull()
    val width = integerField("width") ?: return null
    val height = integerField("height") ?: return null
    val fps = integerField("fps") ?: return null
    val fecPercent = integerField("fecPercent") ?: 0
    val fecPayloadType = integerField("fecPt") ?: 122
    return if (
        textField("transport") != RTP_TRANSPORT ||
        textField("status") != "ready" ||
        width !in 320..7680 || height !in 240..4320 ||
        width % 2 != 0 || height % 2 != 0 || fps !in 24..240 ||
        fecPercent !in setOf(0, 10) || fecPayloadType !in 0..127
    ) null else RtpStreamConfig(width, height, fps, fecPayloadType, fecPercent)
}

private fun readAsciiLine(socket: Socket, maxBytes: Int): String {
    val line = ByteArrayOutputStream()
    val input = socket.getInputStream()
    while (line.size() < maxBytes) {
        val value = input.read()
        if (value < 0 || value == '\n'.code) break
        line.write(value)
    }
    if (line.size() >= maxBytes) throw java.io.IOException("control response too large")
    return line.toString(Charsets.UTF_8.name())
}

class StreamReceiver(
    private val decoder: H264Decoder,
    private val width: Int,
    private val height: Int,
    private val fps: Int = 60,
    private val hostIp: String? = null,
    private val hostPort: Int = 7110
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
    var onPlainTransportReady: (() -> Unit)? = null
    var onStats: ((StreamStats) -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val RTP_CONTROL_RESPONSE_LIMIT = 1024
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
                if (target != "127.0.0.1") {
                    if (!receiveLoopRtp(target) && running.get()) onDisconnect?.invoke()
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
        socket.receiveBufferSize = MAX_STREAM_BUFFER
        Log.i(TAG, "RTP UDP receive buffer: ${socket.receiveBufferSize} bytes")
        socket.soTimeout = 4
        try { socket.trafficClass = 0xC0 } catch (_: Exception) {}
        val host = InetAddress.getByName(targetIp)
        val controlPort = hostPort
        val hello = buildRtpControlMessage(socket.localPort, fps, width, height)
        val helloBytes = hello.toByteArray(Charsets.UTF_8)
        Log.i(TAG, "RTP negotiation: UDP port ${socket.localPort}, target $targetIp:$controlPort")
        onStatusChange?.invoke("Negotiating low-latency UDP video…")
        val ready: RtpStreamConfig? = try {
            Socket().use { control ->
                control.connect(InetSocketAddress(targetIp, controlPort), 1500)
                control.soTimeout = 1500
                control.tcpNoDelay = true
                control.getOutputStream().apply {
                    write(helloBytes)
                    write('\n'.code)
                    flush()
                }
                val response = readAsciiLine(control, RTP_CONTROL_RESPONSE_LIMIT)
                Log.i(TAG, "RTP server reply: $response")
                parseRtpReady(response)
            }
        } catch (e: Exception) {
            Log.e(TAG, "RTP TCP handshake failed: ${e.message}", e)
            null
        }
        if (ready == null) {
            Log.w(TAG, "RTP handshake not ready")
            socket.close()
            return false
        }
        Log.i(TAG, "RTP handshake succeeded, starting receive loop")
        socket.soTimeout = 4
        onPlainTransportReady?.invoke()
        decoder.init(
            ready.width, ready.height, ready.fps,
            balancedOutput = false, inputFrameCapacity = 5,
            replaceInputOnOverflow = false,
        )
        onStatusChange?.invoke("")
        val assembler = RtpH264Assembler(detectCrossFrameGaps = ready.fecPercent == 0)
        val clockSync = RtpClockSync()
        decoder.setFrameRenderedTimingCallback(clockSync::recordRendered)
        val fecRecovery = if (ready.fecPercent == 10) {
            RtpUlpFecRecovery(ready.fecPayloadType)
        } else null
        val buffer = ByteArray(2048)
        val packet = DatagramPacket(buffer, buffer.size)
        var waitingForIdr = true
        val frameDeadlineNanos = rtpFrameDeadlineNanos(ready.fps)
        var lostPackets = 0
        var recoveredPackets = 0
        var mediaPackets = 0
        var fecPackets = 0
        var fecUnrecoverable = 0
        var incompleteFrames = 0
        var receivedPackets = 0
        var totalReceivedPackets = 0L
        var totalFramesDecoded = 0L
        var startupFramesLogged = 0
        var firstPacketLogged = false
        var lastStats = android.os.SystemClock.uptimeMillis()
        var receivedBytes = 0L
        val assemblySamplesMs = ArrayList<Float>()
        var lateFrames = 0
        val feedback = RtpFeedbackAccumulator()
        var noPacketDeadline = android.os.SystemClock.uptimeMillis() + 5000
        var lastIdrRequestMs = -IDR_REQUEST_COOLDOWN_MS

        fun requestRecoveryIdr(cooldownMs: Long = IDR_REQUEST_COOLDOWN_MS) {
            val now = android.os.SystemClock.uptimeMillis()
            if (!recoveryIdrAllowed(lastIdrRequestMs, now, cooldownMs)) return
            lastIdrRequestMs = now
            requestIdrViaTcp(targetIp, controlPort, socket.localPort)
        }

        fun feedCompletedFrame(frame: ByteArray, sequenceGap: Int) {
            val isIdr = containsIdr(frame)
            if (sequenceGap > 0 && !isIdr) {
                lostPackets += sequenceGap
                incompleteFrames++
                if (!waitingForIdr) {
                    Log.w(TAG, "RTP cross-frame sequence gap=$sequenceGap; requesting soft recovery IDR")
                    requestRecoveryIdr()
                }
            }
            if (!waitingForIdr || isIdr) {
                val result = decoder.feedChunk(
                    frame, 0, frame.size, isIdr,
                    assembler.completedTimestamp ?: -1,
                )
                when (result) {
                    H264Decoder.SubmissionResult.ACCEPTED -> {
                        totalFramesDecoded++
                        if (startupFramesLogged < 3) {
                            startupFramesLogged++
                            Log.i(TAG, "RTP startup frame #$startupFramesLogged fed to decoder: " +
                                "size=${frame.size} idr=$isIdr")
                        }
                        if (isIdr) waitingForIdr = false
                    }
                    H264Decoder.SubmissionResult.DROPPED -> {
                        if (isIdr) {
                            Log.w(TAG, "RTP decoder dropped IDR; waiting for recovery IDR")
                            waitingForIdr = true
                            requestRecoveryIdr(HARD_IDR_RETRY_MS)
                        } else {
                            Log.w(TAG, "RTP decoder input burst full; requesting soft recovery IDR")
                            requestRecoveryIdr()
                        }
                    }
                    H264Decoder.SubmissionResult.FAILED -> {
                        Log.w(TAG, "RTP decoder failed frame: size=${frame.size} idr=$isIdr")
                    }
                }
            } else {
                requestRecoveryIdr(HARD_IDR_RETRY_MS)
            }
        }

        fun drainCompletedFrames(first: ByteArray?) {
            var frame = first
            while (frame != null) {
                assembler.completedAssemblyNanos?.let { duration ->
                    assemblySamplesMs += duration / 1_000_000f
                    if (duration > 1_000_000_000L / ready.fps.coerceAtLeast(1)) {
                        lateFrames++
                    }
                }
                feedCompletedFrame(frame, assembler.completedSequenceGap)
                frame = assembler.pollCompleted()
            }
        }

        while (running.get()) {
            try {
                packet.length = buffer.size
                socket.receive(packet)
                val rtp = RtpH264Assembler.parse(packet.data, packet.length) ?: continue
                receivedPackets++
                receivedBytes += packet.length
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
                val mediaPacket = if (
                    fecRecovery != null && rtp.payloadType == ready.fecPayloadType
                ) {
                    fecPackets++
                    val result = fecRecovery.recover(rtp)
                    when (result.status) {
                        FecRecoveryStatus.RECOVERED -> {
                            recoveredPackets++
                            result.packet ?: continue
                        }
                        FecRecoveryStatus.UNRECOVERABLE,
                        FecRecoveryStatus.MALFORMED -> {
                            fecUnrecoverable++
                            continue
                        }
                        FecRecoveryStatus.NOT_NEEDED -> continue
                    }
                } else if (rtp.payloadType == 96) {
                    mediaPackets++
                    fecRecovery?.remember(rtp)
                    rtp
                } else continue
                val frame = assembler.offer(mediaPacket)
                lostPackets += assembler.lostPackets
                if (assembler.droppedFrame) {
                    waitingForIdr = true
                    incompleteFrames++
                    requestRecoveryIdr(HARD_IDR_RETRY_MS)
                }
                drainCompletedFrames(frame)
            } catch (_: SocketTimeoutException) {
                if (noPacketDeadline > 0 && android.os.SystemClock.uptimeMillis() > noPacketDeadline) {
                    Log.w(TAG, "RTP no packets received within 5s — requesting IDR")
                    requestRecoveryIdr()
                    noPacketDeadline = android.os.SystemClock.uptimeMillis() + 5000
                }
                if (assembler.expire(System.nanoTime(), frameDeadlineNanos)) {
                    lostPackets += assembler.lostPackets
                    waitingForIdr = true
                    incompleteFrames++
                    requestRecoveryIdr(HARD_IDR_RETRY_MS)
                    drainCompletedFrames(assembler.pollCompleted())
                }
            }
            val statsNow = android.os.SystemClock.uptimeMillis()
            if (statsNow - lastStats >= 250) {
                val stats = decoder.takeStats()
                val elapsedMs = (statsNow - lastStats).coerceAtLeast(1)
                val receivedKbps = ((receivedBytes * 8L) / elapsedMs).toInt()
                val packetsPerSecond = ((receivedPackets * 1_000L) / elapsedMs).toInt()
                val inputFps = totalFramesDecoded.toFloat() * 1_000f / elapsedMs
                val decodedFps = stats.decodedFrames.toFloat() * 1_000f / elapsedMs
                val renderedFps = stats.renderedFrames.toFloat() * 1_000f / elapsedMs
                val lossPercent = lostPackets * 100f /
                    (mediaPackets + recoveredPackets + lostPackets).coerceAtLeast(1)
                val latency = clockSync.latest()
                val snapshot = StreamStats(
                    receivedKbps = receivedKbps,
                    packetsPerSecond = packetsPerSecond,
                    lossPercent = lossPercent,
                    incompleteFrames = incompleteFrames,
                    inputFps = inputFps,
                    decodedFps = decodedFps,
                    renderedFps = renderedFps,
                    decodeMs = stats.decodeMicros / 1_000f,
                    renderMs = stats.renderMicros / 1_000f,
                    queueDepth = stats.queueDepth,
                    decoderDroppedFrames = stats.droppedFrames,
                    lostPackets = lostPackets,
                    inputFrames = totalFramesDecoded.toInt(),
                    decodedFrames = stats.decodedFrames,
                    renderedFrames = stats.renderedFrames,
                    measurementMs = elapsedMs,
                    mediaPackets = mediaPackets,
                    fecPackets = fecPackets,
                    fecRecovered = recoveredPackets,
                    fecUnrecoverable = fecUnrecoverable,
                    residualLost = lostPackets,
                    assemblyP95Ms = percentile95(assemblySamplesMs),
                    lateFrames = lateFrames,
                    assemblySamplesMs = assemblySamplesMs.toList(),
                    endToEndMs = latency?.first,
                    clockErrorMs = latency?.second,
                )
                onStats?.invoke(snapshot)
                if (totalReceivedPackets < 100 || receivedPackets > 0) {
                    Log.d(TAG, "RTP stats: rx=${receivedKbps}kbps pps=$packetsPerSecond " +
                        "loss=${String.format(java.util.Locale.US, "%.1f", lossPercent)}% " +
                        "incomplete=$incompleteFrames input=${String.format(java.util.Locale.US, "%.1f", inputFps)} " +
                        "decode=${String.format(java.util.Locale.US, "%.1f", decodedFps)} " +
                        "render=${String.format(java.util.Locale.US, "%.1f", renderedFps)} " +
                        "decodeMs=${String.format(java.util.Locale.US, "%.1f", snapshot.decodeMs)} " +
                        "renderMs=${String.format(java.util.Locale.US, "%.1f", snapshot.renderMs)} " +
                        "queue=${stats.queueDepth} dropped=${stats.droppedFrames} recovered=$recoveredPackets")
                }
                feedback.add(snapshot, receivedBytes, receivedPackets, lostPackets)?.let {
                    sendStatsViaTcp(
                        targetIp, controlPort, socket.localPort, it, clockSync,
                    )
                }
                receivedPackets = 0
                receivedBytes = 0
                lostPackets = 0
                recoveredPackets = 0
                mediaPackets = 0
                fecPackets = 0
                fecUnrecoverable = 0
                incompleteFrames = 0
                assemblySamplesMs.clear()
                lateFrames = 0
                totalFramesDecoded = 0
                lastStats = statsNow
            }
        }
        Log.i(TAG, "RTP receive loop ended: totalPackets=$totalReceivedPackets " +
            "totalFrames=$totalFramesDecoded")
        socket.close()
        decoder.setFrameRenderedTimingCallback(null)
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
                    val hello = buildRtpControlMessage(
                        localUdpPort, fps, width, height, requestIdr = true
                    )
                    control.getOutputStream().apply {
                        write(hello.toByteArray(Charsets.UTF_8))
                        write('\n'.code)
                        flush()
                    }
                    try { readAsciiLine(control, RTP_CONTROL_RESPONSE_LIMIT) } catch (_: Exception) {}
                }
            } catch (_: Exception) {
            } finally {
                idrRequestInFlight.set(false)
            }
        }, "MonitorizeIdrRequest").start()
    }

    private fun sendStatsViaTcp(
        hostIp: String, controlPort: Int, localUdpPort: Int, stats: StreamStats,
        clockSync: RtpClockSync,
    ) {
        Thread({
            try {
                Socket().use { control ->
                    control.connect(InetSocketAddress(hostIp, controlPort), 750)
                    control.soTimeout = 750
                    control.tcpNoDelay = true
                    val rendered = clockSync.renderedFrame()
                    val sentNs = System.nanoTime()
                    val message = "MZRP1 {\"transport\":\"rtp-udp-v1\",\"port\":$localUdpPort," +
                        "\"type\":\"stats\",\"receivedKbps\":${stats.receivedKbps}," +
                        "\"packetsPerSecond\":${stats.packetsPerSecond}," +
                        "\"lossPercent\":${stats.lossPercent},\"incomplete\":${stats.incompleteFrames}," +
                        "\"lost\":${stats.lostPackets}," +
                        "\"renderedFrames\":${stats.renderedFrames},\"queueDepth\":${stats.queueDepth}," +
                        "\"decodeMs\":${stats.decodeMs},\"renderMs\":${stats.renderMs}," +
                        "\"decoderDropped\":${stats.decoderDroppedFrames}," +
                        "\"mediaPackets\":${stats.mediaPackets}," +
                        "\"fecPackets\":${stats.fecPackets}," +
                        "\"fecRecovered\":${stats.fecRecovered}," +
                        "\"fecUnrecoverable\":${stats.fecUnrecoverable}," +
                        "\"residualLost\":${stats.residualLost}," +
                        "\"assemblyP95Ms\":${stats.assemblyP95Ms}," +
                        "\"lateFrames\":${stats.lateFrames}," +
                        "\"endToEndMs\":${stats.endToEndMs ?: -1}," +
                        "\"clockErrorMs\":${stats.clockErrorMs ?: -1}," +
                        "\"renderedRtpTimestamp\":${rendered?.timestamp ?: -1}," +
                        "\"clockSendNs\":$sentNs," +
                        "\"intervalMs\":${stats.measurementMs}}"
                    control.getOutputStream().apply {
                        write(message.toByteArray(Charsets.UTF_8))
                        write('\n'.code)
                        flush()
                    }
                    val response = readAsciiLine(control, RTP_CONTROL_RESPONSE_LIMIT)
                    if (rendered != null) {
                        clockSync.applyResponse(sentNs, System.nanoTime(), response, rendered)
                    }
                }
            } catch (_: Exception) {
            }
        }, "MonitorizeStats").start()
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

    private fun receiveLoopWifi(targetIp: String) {
        val streamType = "USB"
        var hasConnected = false
        while (running.get()) {
            onStatusChange?.invoke(if (streamType == "USB") "Waiting for USB connection…" else "Connecting to $targetIp:$hostPort…")
            var socket: Socket? = null
            while (running.get() && socket == null) {
                try {
                    socket = Socket()
                    socket.connect(InetSocketAddress(targetIp, hostPort), CONNECT_TIMEOUT_MS)
                } catch (e: SecurityException) {
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
                when (decoder.feedChunk(accessUnit, 0, accessUnitSize, accessUnitHasIdr)) {
                    H264Decoder.SubmissionResult.FAILED -> {
                        Log.w(TAG, "$streamType decoder rejected frame; reconnecting")
                        decoderFailed = true
                    }
                    H264Decoder.SubmissionResult.DROPPED ->
                        Log.w(TAG, "$streamType decoder input full; dropped frame")
                    H264Decoder.SubmissionResult.ACCEPTED -> Unit
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
