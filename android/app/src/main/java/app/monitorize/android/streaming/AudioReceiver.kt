package app.monitorize.android.streaming

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.Locale
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong


internal const val AUDIO_PORT = 7120
internal const val AUDIO_SAMPLE_RATE = 48_000
internal const val AUDIO_PACKET_MS = 10
internal const val AUDIO_BLOCK_BYTES = AUDIO_SAMPLE_RATE / 1000 * AUDIO_PACKET_MS * 2
internal const val AUDIO_RTP_PAYLOAD_TYPE = 97
internal const val AUDIO_MIN_PRIME_BLOCKS = 8
internal const val AUDIO_MAX_PRIME_BLOCKS = 12
internal const val AUDIO_PLAYBACK_WAIT_MS = 150L
internal const val WIFI_AUDIO_MIN_PRIME_BLOCKS = 20
internal const val WIFI_AUDIO_MAX_PRIME_BLOCKS = 40
internal const val WIFI_AUDIO_PLAYBACK_WAIT_MS = 300L
internal const val WIFI_AUDIO_QUEUE_BLOCKS = 64
private const val AUDIO_TRANSPORT = "rtp-opus-udp-v1"

internal fun audioHardwarePrimeBlocks(targetBlocks: Int, bufferSizeInFrames: Int): Int {
    val framesPerBlock = AUDIO_SAMPLE_RATE * AUDIO_PACKET_MS / 1000
    val writableBlocks = (bufferSizeInFrames / framesPerBlock - 1).coerceAtLeast(1)
    return targetBlocks.coerceAtMost(writableBlocks)
}

internal data class RtpAudioPacket(
    val sequence: Int,
    val timestamp: Long,
    val opus: ByteArray,
)

internal fun parseRtpOpus(data: ByteArray, length: Int): RtpAudioPacket? {
    if (length < 13 || length > data.size || ((data[0].toInt() and 0xff) ushr 6) != 2) {
        return null
    }
    if ((data[1].toInt() and 0x7f) != AUDIO_RTP_PAYLOAD_TYPE) return null
    var offset = 12 + (data[0].toInt() and 0x0f) * 4
    if (offset > length) return null
    if (data[0].toInt() and 0x10 != 0) {
        if (offset + 4 > length) return null
        val extensionWords = ((data[offset + 2].toInt() and 0xff) shl 8) or
            (data[offset + 3].toInt() and 0xff)
        offset += 4 + extensionWords * 4
    }
    val hasPadding = data[0].toInt() and 0x20 != 0
    val padding = if (hasPadding) data[length - 1].toInt() and 0xff else 0
    val payloadLength = length - offset - padding
    if (
        offset > length || (hasPadding && padding == 0) || padding >= length ||
        payloadLength <= 0
    ) {
        return null
    }
    val sequence = ((data[2].toInt() and 0xff) shl 8) or (data[3].toInt() and 0xff)
    val timestamp = ((data[4].toLong() and 0xff) shl 24) or
        ((data[5].toLong() and 0xff) shl 16) or
        ((data[6].toLong() and 0xff) shl 8) or (data[7].toLong() and 0xff)
    return RtpAudioPacket(sequence, timestamp, data.copyOfRange(offset, offset + payloadLength))
}

internal fun audioSequenceDistance(sequence: Int, expected: Int): Int =
    (sequence - expected) and 0xffff

internal class AudioJitterBuffer(
    private val capacity: Int = 16,
    private val primePackets: Int = AUDIO_MIN_PRIME_BLOCKS,
) {
    private val pending = mutableMapOf<Int, ByteArray>()
    private var expected: Int? = null
    private var started = false
    var lostPackets = 0L
        private set
    var latePackets = 0L
        private set

    fun offer(packet: RtpAudioPacket): List<ByteArray?> {
        var currentExpected = expected ?: packet.sequence.also { expected = it }
        val distance = audioSequenceDistance(packet.sequence, currentExpected)
        if (distance >= 0x8000 || pending.containsKey(packet.sequence)) {
            latePackets++
            return emptyList()
        }
        if (distance > 64) {
            lostPackets += distance.toLong()
            pending.clear()
            currentExpected = packet.sequence
            expected = currentExpected
            started = false
        }
        pending[packet.sequence] = packet.opus
        if (!started && pending.size < primePackets) return emptyList()
        started = true

        val output = mutableListOf<ByteArray?>()
        while (pending.isNotEmpty() && output.size < capacity) {
            val payload = pending.remove(currentExpected)
            if (payload != null) {
                output += payload
            } else if (pending.size >= primePackets) {
                output.add(null)
                lostPackets++
            } else {
                break
            }
            currentExpected = (currentExpected + 1) and 0xffff
            expected = currentExpected
        }
        if (pending.size > capacity) reset()
        return output
    }

    fun reset() {
        pending.clear()
        expected = null
        started = false
    }
}

internal class AudioBufferTuner(
    private val minimum: Int = AUDIO_MIN_PRIME_BLOCKS,
    private val maximum: Int = AUDIO_MAX_PRIME_BLOCKS,
) {
    var targetBlocks = minimum
        private set
    private var stableWindows = 0

    @Synchronized
    fun onUnderrun(): Int {
        stableWindows = 0
        targetBlocks = (targetBlocks + 1).coerceAtMost(maximum)
        return targetBlocks
    }

    @Synchronized
    fun onStableWindow(): Int {
        stableWindows++
        if (stableWindows >= 10 && targetBlocks > minimum) {
            targetBlocks--
            stableWindows = 0
        }
        return targetBlocks
    }

    @Synchronized fun resetStability() { stableWindows = 0 }
}

internal fun readPcmBlock(input: java.io.InputStream, block: ByteArray): Boolean {
    var offset = 0
    while (offset < block.size) {
        val read = input.read(block, offset, block.size - offset)
        if (read < 0) return false
        if (read == 0) continue
        offset += read
    }
    return true
}

internal fun shouldRetryAudio(everConnected: Boolean, failedAttempts: Int): Boolean =
    everConnected || failedAttempts < 5

internal fun audioRetryDelayMs(isUsb: Boolean, everConnected: Boolean): Long = when {
    isUsb && everConnected -> 50
    everConnected -> 750
    else -> 500
}

internal fun enqueueLatest(queue: ArrayBlockingQueue<ByteArray>, block: ByteArray): Boolean {
    if (queue.offer(block)) return false
    queue.poll()
    queue.offer(block)
    return true
}

private fun readAsciiLine(socket: Socket, maxBytes: Int = 1024): String {
    val line = ByteArrayOutputStream()
    val input = socket.getInputStream()
    while (line.size() < maxBytes) {
        val value = input.read()
        if (value < 0 || value == '\n'.code) break
        line.write(value)
    }
    if (line.size() >= maxBytes) throw IOException("audio control response too large")
    return line.toString(Charsets.UTF_8.name())
}

private fun validAudioReady(response: String): Boolean =
    response.startsWith("MZA1 ") &&
        response.contains("\"status\":\"ready\"") &&
        response.contains("\"transport\":\"$AUDIO_TRANSPORT\"") &&
        response.contains("\"codec\":\"OPUS\"") &&
        response.contains("\"sampleRate\":$AUDIO_SAMPLE_RATE") &&
        response.contains("\"channels\":1") &&
        response.contains("\"packetMs\":$AUDIO_PACKET_MS") &&
        response.contains("\"rtpPt\":$AUDIO_RTP_PAYLOAD_TYPE")

internal class NativeOpusDecoder : Closeable {
    private var handle = 0L

    init {
        LOAD_ERROR?.let { throw IOException("native libopus unavailable", it) }
        handle = nativeCreate()
        if (handle == 0L) throw IOException("native libopus decoder creation failed")
        Log.i("AudioReceiver", "Opus decoder: native ${nativeVersion()}")
    }

    fun decode(packet: ByteArray?): ByteArray {
        if (handle == 0L) throw IOException("native libopus decoder is closed")
        return nativeDecode(handle, packet)
    }

    override fun close() {
        if (handle == 0L) return
        nativeDestroy(handle)
        handle = 0L
    }

    private external fun nativeCreate(): Long
    private external fun nativeDecode(handle: Long, packet: ByteArray?): ByteArray
    private external fun nativeVersion(): String
    private external fun nativeDestroy(handle: Long)

    private companion object {
        val LOAD_ERROR: Throwable? = try {
            System.loadLibrary("monitorize_audio")
            null
        } catch (error: Throwable) {
            error
        }
    }
}

class AudioReceiver(
    private val hostIp: String? = null,
    private val hostPort: Int = AUDIO_PORT,
) {
    private val isUsbTransport = hostIp.isNullOrBlank()
    private val minimumPrimeBlocks = if (isUsbTransport) {
        AUDIO_MIN_PRIME_BLOCKS
    } else WIFI_AUDIO_MIN_PRIME_BLOCKS
    private val maximumPrimeBlocks = if (isUsbTransport) {
        AUDIO_MAX_PRIME_BLOCKS
    } else WIFI_AUDIO_MAX_PRIME_BLOCKS
    private val running = AtomicBoolean(false)
    private val playbackQueue = ArrayBlockingQueue<ByteArray>(
        if (isUsbTransport) 24 else WIFI_AUDIO_QUEUE_BLOCKS
    )
    private val reprimePlayback = AtomicBoolean(true)
    private val tuner = AudioBufferTuner(minimumPrimeBlocks, maximumPrimeBlocks)
    private val packetsReceived = AtomicLong()
    private val packetsLost = AtomicLong()
    private val packetsLate = AtomicLong()
    private val queueDrops = AtomicLong()
    private val appUnderruns = AtomicLong()
    @Volatile private var worker: Thread? = null
    @Volatile private var playbackWorker: Thread? = null
    @Volatile private var udpSocket: DatagramSocket? = null
    @Volatile private var tcpSocket: Socket? = null
    @Volatile private var audioTrack: AudioTrack? = null
    @Volatile private var everConnected = false
    @Volatile private var transportName = "unavailable"

    companion object {
        private const val TAG = "AudioReceiver"
    }

    @Synchronized
    fun start() {
        if (!running.compareAndSet(false, true)) return
        worker = Thread({ receiveLoop() }, "MonitorizeAudioReceiver").also { it.start() }
    }

    private fun receiveLoop() {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
        var failedAttempts = 0
        val target = hostIp.takeUnless { it.isNullOrBlank() } ?: "127.0.0.1"
        val isUsb = target == "127.0.0.1"
        try {
            while (running.get() && shouldRetryAudio(everConnected, failedAttempts)) {
                try {
                    if (isUsb) receiveUsb(target) else receiveWifi(target)
                } catch (e: Exception) {
                    if (running.get() && (failedAttempts == 0 || everConnected)) {
                        Log.d(TAG, "Audio unavailable: ${e.message}")
                    }
                } finally {
                    closeSockets()
                    requestReprime()
                }
                if (!running.get()) break
                failedAttempts++
                try {
                    Thread.sleep(audioRetryDelayMs(isUsb, everConnected))
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    break
                }
            }
        } finally {
            running.set(false)
            closeSockets()
            releasePlayback()
            worker = null
        }
    }

    private fun receiveWifi(target: String) {
        val udp = DatagramSocket()
        udpSocket = udp
        udp.soTimeout = 1000
        udp.receiveBufferSize = 128 * 1024
        try { udp.trafficClass = 0xc0 } catch (_: Exception) {}

        Socket().use { control ->
            tcpSocket = control
            control.connect(InetSocketAddress(target, hostPort), 500)
            control.soTimeout = 1000
            control.tcpNoDelay = true
            val hello = "MZA1 {\"transport\":\"$AUDIO_TRANSPORT\",\"type\":\"start\"," +
                "\"port\":${udp.localPort}}\n"
            control.getOutputStream().apply {
                write(hello.toByteArray(Charsets.UTF_8))
                flush()
            }
            if (!validAudioReady(readAsciiLine(control))) throw IOException("invalid audio reply")
            tcpSocket = null
        }

        udp.connect(InetSocketAddress(target, hostPort))
        NativeOpusDecoder().use { decoder ->
            transportName = "wifi-opus"
            everConnected = true
            ensurePlayback()
            val jitter = AudioJitterBuffer()
            var previousLost = 0L
            var previousLate = 0L
            val buffer = ByteArray(1500)
            val datagram = DatagramPacket(buffer, buffer.size)
            while (running.get()) {
                try {
                    datagram.length = buffer.size
                    udp.receive(datagram)
                } catch (e: SocketTimeoutException) {
                    throw IOException("audio packets stopped", e)
                }
                val packet = parseRtpOpus(buffer, datagram.length) ?: continue
                packetsReceived.incrementAndGet()
                val ordered = jitter.offer(packet)
                packetsLost.addAndGet(jitter.lostPackets - previousLost)
                packetsLate.addAndGet(jitter.latePackets - previousLate)
                previousLost = jitter.lostPackets
                previousLate = jitter.latePackets
                ordered.forEach { opus ->
                    enqueue(decoder.decode(opus))
                }
            }
        }
    }

    private fun receiveUsb(target: String) {
        val socket = Socket()
        tcpSocket = socket
        socket.connect(InetSocketAddress(target, hostPort), 500)
        socket.soTimeout = 1000
        socket.tcpNoDelay = true
        transportName = "usb-pcm"
        everConnected = true
        ensurePlayback()
        val input = socket.getInputStream()
        while (running.get()) {
            val block = ByteArray(AUDIO_BLOCK_BYTES)
            if (!readPcmBlock(input, block)) throw IOException("USB audio ended")
            enqueue(block)
        }
    }

    private fun enqueue(block: ByteArray) {
        if (enqueueLatest(playbackQueue, block)) queueDrops.incrementAndGet()
    }

    private fun requestReprime() {
        reprimePlayback.set(true)
        playbackQueue.clear()
        tuner.resetStability()
    }

    @Synchronized
    private fun ensurePlayback() {
        if (playbackWorker != null) return
        val minimum = AudioTrack.getMinBufferSize(
            AUDIO_SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minimum <= 0) throw IOException("AudioTrack does not support mono 48 kHz PCM")
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(AUDIO_SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(minimum, AUDIO_BLOCK_BYTES * 16))
            .build()
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            track.release()
            throw IOException("AudioTrack initialization failed")
        }
        track.setBufferSizeInFrames(
            maxOf(minimum / 2, AUDIO_SAMPLE_RATE * maximumPrimeBlocks / 100)
        )
        audioTrack = track
        reprimePlayback.set(true)
        playbackWorker = Thread({ playbackLoop(track) }, "MonitorizeAudioPlayback").also {
            it.start()
        }
    }

    private fun playbackLoop(track: AudioTrack) {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
        var playing = false
        var lastUnderruns = track.underrunCount
        var lastAppUnderruns = appUnderruns.get()
        var lastTuneAt = System.nanoTime()
        var lastLogAt = lastTuneAt
        try {
            while (running.get()) {
                if (reprimePlayback.getAndSet(false)) {
                    pauseAndFlush(track)
                    playing = false
                }
                if (!playing) {
                    val targetBlocks = tuner.targetBlocks
                    if (playbackQueue.size < targetBlocks) {
                        Thread.sleep(5)
                        continue
                    }
                    val hardwarePrimeBlocks = audioHardwarePrimeBlocks(
                        targetBlocks, track.bufferSizeInFrames,
                    )
                    var primedBlocks = 0
                    while (primedBlocks < hardwarePrimeBlocks) {
                        val primeBlock = playbackQueue.poll() ?: break
                        writeBlock(track, primeBlock)
                        primedBlocks++
                    }
                    if (primedBlocks < hardwarePrimeBlocks || reprimePlayback.get()) {
                        pauseAndFlush(track)
                        continue
                    }
                    track.play()
                    playing = true
                    lastUnderruns = track.underrunCount
                }
                val playbackWaitMs = if (isUsbTransport) {
                    AUDIO_PLAYBACK_WAIT_MS
                } else WIFI_AUDIO_PLAYBACK_WAIT_MS
                val block = playbackQueue.poll(playbackWaitMs, TimeUnit.MILLISECONDS)
                if (block == null) {
                    appUnderruns.incrementAndGet()
                    tuner.onUnderrun()
                    reprimePlayback.set(true)
                    continue
                }
                writeBlock(track, block)

                val now = System.nanoTime()
                if (now - lastTuneAt >= TimeUnit.SECONDS.toNanos(1)) {
                    val underruns = track.underrunCount
                    val starved = appUnderruns.get()
                    if (underruns > lastUnderruns) {
                        tuner.onUnderrun()
                    } else if (starved <= lastAppUnderruns) {
                        tuner.onStableWindow()
                    }
                    lastUnderruns = underruns
                    lastAppUnderruns = starved
                    lastTuneAt = now
                }
                if (now - lastLogAt >= TimeUnit.SECONDS.toNanos(5)) {
                    logStats(track)
                    lastLogAt = now
                }
            }
        } catch (e: Exception) {
            if (running.get()) {
                Log.w(TAG, "Playback stopped: ${e.message}")
                closeSockets()
            }
        } finally {
            synchronized(this) {
                if (playbackWorker === Thread.currentThread()) {
                    playbackWorker = null
                    if (audioTrack === track) {
                        audioTrack = null
                        stopAndRelease(track)
                    }
                }
            }
        }
    }

    private fun logStats(track: AudioTrack) {
        val received = packetsReceived.get()
        val lost = packetsLost.get()
        val lossPercent = if (received + lost == 0L) 0.0
            else lost * 100.0 / (received + lost)
        Log.i(
            TAG,
            "Stats transport=$transportName rx=$received loss=${String.format(Locale.US, "%.2f", lossPercent)}% " +
                "late=${packetsLate.get()} queue=${playbackQueue.size} " +
                "target=${tuner.targetBlocks} underruns=${track.underrunCount} " +
                "starved=${appUnderruns.get()} drops=${queueDrops.get()}",
        )
    }

    private fun pauseAndFlush(track: AudioTrack) {
        try { track.pause() } catch (_: Exception) {}
        try { track.flush() } catch (_: Exception) {}
    }

    private fun writeBlock(track: AudioTrack, block: ByteArray) {
        var offset = 0
        while (offset < block.size && running.get()) {
            val written = track.write(
                block, offset, block.size - offset, AudioTrack.WRITE_BLOCKING
            )
            if (written < 0) throw IOException("AudioTrack write failed: $written")
            if (written == 0) {
                Thread.yield()
                continue
            }
            offset += written
        }
    }

    private fun closeSockets() {
        try { udpSocket?.close() } catch (_: Exception) {}
        udpSocket = null
        try { tcpSocket?.close() } catch (_: Exception) {}
        tcpSocket = null
    }

    @Synchronized
    private fun releasePlayback() {
        playbackQueue.clear()
        playbackWorker?.interrupt()
        playbackWorker = null
        val track = audioTrack
        audioTrack = null
        if (track != null) stopAndRelease(track)
    }

    private fun stopAndRelease(track: AudioTrack) {
        try { track.pause() } catch (_: Exception) {}
        try { track.flush() } catch (_: Exception) {}
        try { track.stop() } catch (_: Exception) {}
        track.release()
    }

    @Synchronized
    fun stop() {
        if (!running.getAndSet(false)) return
        closeSockets()
        worker?.interrupt()
        releasePlayback()
        if (Thread.currentThread() !== worker) {
            try { worker?.join(500) } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }
        worker = null
    }
}
