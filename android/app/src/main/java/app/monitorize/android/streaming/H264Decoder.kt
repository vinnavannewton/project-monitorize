package app.monitorize.android.streaming

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import android.view.Choreographer
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.ConcurrentHashMap

internal class PendingOutputQueue(private val capacity: Int) {
    private val values = java.util.ArrayDeque<Int>()

    @Synchronized
    fun offer(value: Int): Int? {
        val dropped = if (values.size >= capacity) values.removeFirst() else null
        values.addLast(value)
        return dropped
    }

    @Synchronized fun poll(): Int? = values.pollFirst()
    @Synchronized fun size(): Int = values.size

    @Synchronized
    fun drain(): List<Int> {
        val result = values.toList()
        values.clear()
        return result
    }
}

class H264Decoder(
    private val surface: Surface,
    private val onOutputSizeChanged: (Int, Int) -> Unit = { _, _ -> },
    private val onFirstFrameRendered: () -> Unit = {}
) {
    enum class SubmissionResult { ACCEPTED, DROPPED, FAILED }

    private var codec: MediaCodec? = null
    private var frameCount = 0L
    @Volatile private var initialized = false
    private var callbackThread: HandlerThread? = null
    private val decoderGeneration = AtomicInteger(0)
    private val fatalError = AtomicBoolean(false)
    private var pendingOutputBuffers = PendingOutputQueue(1)
    private val renderedSinceStats = AtomicInteger(0)
    private val decodedSinceStats = AtomicInteger(0)
    private val decodeMicrosSinceStats = AtomicLong(0)
    private val renderMicrosSinceStats = AtomicLong(0)
    private val droppedSinceStats = AtomicInteger(0)
    private val presentationRtpTimestamps = ConcurrentHashMap<Long, Long>()
    @Volatile private var onFrameRenderedTiming: ((Long, Long) -> Unit)? = null

    data class Stats(
        val renderedFrames: Int,
        val decodedFrames: Int,
        val queueDepth: Int,
        val decodeMicros: Long,
        val renderMicros: Long,
        val droppedFrames: Int,
    )

    class FrameChunk(val data: ByteArray) {
        var size: Int = 0
        var isKeyFrame: Boolean = false
        var receivedAtUs: Long = 0
        var rtpTimestamp: Long = -1
    }

    
    
    private var inputCapacity = 1
    private var replaceQueuedOnOverflow = true
    private var chunkPool = ArrayBlockingQueue<FrameChunk>(inputCapacity)
    private var chunkQueue = LinkedBlockingQueue<FrameChunk>(inputCapacity)
    
    init {
        for (i in 0 until inputCapacity) {
            chunkPool.offer(FrameChunk(ByteArray(MAX_INPUT)))
        }
    }

    companion object {
        private const val TAG = "H264Decoder"
        private const val MAX_INPUT = 2 * 1024 * 1024
    }

    @Synchronized
    fun init(
        width: Int, height: Int, fps: Int = 60, balancedOutput: Boolean = false,
        inputFrameCapacity: Int = 1, replaceInputOnOverflow: Boolean = true,
    ) {
        release()
        inputCapacity = inputFrameCapacity.coerceIn(1, 5)
        replaceQueuedOnOverflow = replaceInputOnOverflow
        chunkPool = ArrayBlockingQueue(inputCapacity)
        chunkQueue = LinkedBlockingQueue(inputCapacity)
        repeat(inputCapacity) { chunkPool.offer(FrameChunk(ByteArray(MAX_INPUT))) }
        pendingOutputBuffers = PendingOutputQueue(if (balancedOutput) 2 else 1)
        val generation = decoderGeneration.incrementAndGet()
        fatalError.set(false)
        val firstFrameReported = AtomicBoolean(false)
        try {
            Log.i(TAG, "Init: ${width}×${height}@${fps}")

            val format = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height
            ).apply {
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_INPUT)
                setInteger(MediaFormat.KEY_FRAME_RATE, fps.coerceIn(24, 240))
                setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt())
                setInteger(MediaFormat.KEY_PRIORITY, 0)
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }

            val decoderInfo = selectDecoder()
                ?: throw IllegalStateException("No H.264 decoder available")
            val hasQualcommOptions = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                (decoderInfo.name.startsWith("c2.qti", true) ||
                    decoderInfo.name.startsWith("omx.qcom", true))
            if (hasQualcommOptions) {
                format.setInteger("vendor.qti-ext-dec-picture-order.enable", 1)
                format.setInteger("vendor.qti-ext-dec-low-latency.enable", 1)
            }
            Log.i(TAG, "Decoder: ${decoderInfo.name}")

            callbackThread = HandlerThread("MonitorizeDecoder").also {
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }
            val handler = Handler(callbackThread!!.looper)

            codec = MediaCodec.createByCodecName(decoderInfo.name).also {
                it.setCallback(object : MediaCodec.Callback() {
                    override fun onInputBufferAvailable(mc: MediaCodec, inputBufferId: Int) {
                        if (!isCurrent(generation)) return
                        val chunk = chunkQueue.poll()
                        if (chunk == null) {
                            if (!pendingInputBuffers.offer(inputBufferId)) {
                                queueEmptyInputBuffer(mc, inputBufferId)
                            }
                            return
                        }
                        if (!fillInputBuffer(mc, inputBufferId, chunk)) {
                            markFatal("Failed to fill input buffer")
                        }
                        recycleChunk(chunk)
                    }

                    override fun onOutputBufferAvailable(
                        mc: MediaCodec, outputBufferId: Int, info: MediaCodec.BufferInfo
                    ) {
                        if (!isCurrent(generation)) return
                        decodedSinceStats.incrementAndGet()
                        decodeMicrosSinceStats.addAndGet(
                            (System.nanoTime() / 1000 - info.presentationTimeUs).coerceAtLeast(0)
                        )
                        if (!balancedOutput) {
                            try {
                                mc.releaseOutputBuffer(outputBufferId, System.nanoTime())
                                frameCount++
                            } catch (_: Exception) {}
                            return
                        }
                        pendingOutputBuffers.offer(outputBufferId)?.let { old ->
                            droppedSinceStats.incrementAndGet()
                            try { mc.releaseOutputBuffer(old, false) } catch (_: Exception) {}
                        }
                    }

                    override fun onError(mc: MediaCodec, e: MediaCodec.CodecException) {
                        if (!isCurrent(generation)) return
                        Log.e(TAG, "Decoder error: ${e.diagnosticInfo}", e)
                        markFatal("Codec error")
                    }

                    override fun onOutputFormatChanged(mc: MediaCodec, format: MediaFormat) {
                        if (!isCurrent(generation)) return
                        val outputWidth = if (
                            format.containsKey(MediaFormat.KEY_CROP_LEFT) &&
                            format.containsKey(MediaFormat.KEY_CROP_RIGHT)
                        ) {
                            format.getInteger(MediaFormat.KEY_CROP_RIGHT) -
                                format.getInteger(MediaFormat.KEY_CROP_LEFT) + 1
                        } else {
                            format.getInteger(MediaFormat.KEY_WIDTH)
                        }
                        val outputHeight = if (
                            format.containsKey(MediaFormat.KEY_CROP_TOP) &&
                            format.containsKey(MediaFormat.KEY_CROP_BOTTOM)
                        ) {
                            format.getInteger(MediaFormat.KEY_CROP_BOTTOM) -
                                format.getInteger(MediaFormat.KEY_CROP_TOP) + 1
                        } else {
                            format.getInteger(MediaFormat.KEY_HEIGHT)
                        }
                        if (outputWidth > 0 && outputHeight > 0) {
                            Log.i(TAG, "Output: ${outputWidth}×${outputHeight}")
                            onOutputSizeChanged(outputWidth, outputHeight)
                        }
                    }
                }, handler)

                try {
                    it.configure(format, surface, null, 0)
                } catch (first: Exception) {
                    if (!hasQualcommOptions || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                        throw first
                    }
                    Log.w(TAG, "Decoder rejected Qualcomm low-latency options; retrying", first)
                    format.removeKey("vendor.qti-ext-dec-picture-order.enable")
                    format.removeKey("vendor.qti-ext-dec-low-latency.enable")
                    it.configure(format, surface, null, 0)
                }
                it.setOnFrameRenderedListener({ _, presentationTimeUs, _ ->
                    val renderedAtNs = System.nanoTime()
                    renderedSinceStats.incrementAndGet()
                    renderMicrosSinceStats.addAndGet(
                        (renderedAtNs / 1000 - presentationTimeUs).coerceAtLeast(0)
                    )
                    presentationRtpTimestamps.remove(presentationTimeUs)?.let {
                        onFrameRenderedTiming?.invoke(it, renderedAtNs)
                    }
                    if (isCurrent(generation) && firstFrameReported.compareAndSet(false, true)) {
                        onFirstFrameRendered()
                    }
                }, handler)
                initialized = true
                it.start()
                if (balancedOutput) {
                    handler.post {
                        val choreographer = Choreographer.getInstance()
                        val callback = object : Choreographer.FrameCallback {
                            override fun doFrame(frameTimeNanos: Long) {
                                if (!isCurrent(generation)) return
                                val output = pendingOutputBuffers.poll()
                                if (output != null) {
                                    try {
                                        it.releaseOutputBuffer(output, frameTimeNanos)
                                        frameCount++
                                    } catch (_: Exception) {}
                                }
                                choreographer.postFrameCallback(this)
                            }
                        }
                        choreographer.postFrameCallback(callback)
                    }
                }
            }
            frameCount = 0
        } catch (e: Exception) {
            Log.e(TAG, "Init failed", e)
            fatalError.set(true)
            release()
        }
    }

    private val pendingInputBuffers = LinkedBlockingQueue<Int>(16)

    private fun selectDecoder(): MediaCodecInfo? {
        return MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos
            .asSequence()
            .filter { !it.isEncoder && it.supportedTypes.any { type ->
                type.equals(MediaFormat.MIMETYPE_VIDEO_AVC, true)
            } }
            .filter { info ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    info.isHardwareAccelerated && !info.isSoftwareOnly
                } else {
                    !info.name.contains("google", true) &&
                        !info.name.contains("android", true)
                }
            }
            .sortedByDescending { info ->
                val name = info.name.lowercase()
                var score = if (name.contains("low_latency") || name.contains("lowlatency")) 4 else 0
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    try {
                        if (info.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC)
                                .isFeatureSupported(MediaCodecInfo.CodecCapabilities.FEATURE_LowLatency)) {
                            score += 8
                        }
                    } catch (_: Exception) {}
                }
                score
            }
            .firstOrNull()
            ?: MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.firstOrNull {
                !it.isEncoder && it.supportedTypes.any { type ->
                    type.equals(MediaFormat.MIMETYPE_VIDEO_AVC, true)
                }
            }
    }

    private fun isCurrent(generation: Int): Boolean {
        return initialized && !fatalError.get() && decoderGeneration.get() == generation
    }

    private fun markFatal(reason: String) {
        fatalError.set(true)
        initialized = false
        Log.e(TAG, reason)
    }

    private fun recycleChunk(chunk: FrameChunk) {
        chunk.size = 0
        chunk.isKeyFrame = false
        chunk.receivedAtUs = 0
        chunk.rtpTimestamp = -1
        chunkPool.offer(chunk)
    }

    private fun drainQueuedFrames(
        reusable: FrameChunk? = null, countDrops: Boolean = false,
    ): FrameChunk? {
        var candidate = reusable
        while (true) {
            val dropped = chunkQueue.poll() ?: break
            if (countDrops) droppedSinceStats.incrementAndGet()
            if (candidate == null) {
                candidate = dropped
            } else {
                recycleChunk(dropped)
            }
        }
        return candidate
    }

    private fun dropOldestNonKeyFrame(): FrameChunk? {
        val iterator = chunkQueue.iterator()
        while (iterator.hasNext()) {
            val chunk = iterator.next()
            if (!chunk.isKeyFrame) {
                iterator.remove()
                droppedSinceStats.incrementAndGet()
                return chunk
            }
        }
        return null
    }

    private fun fillInputBuffer(mc: MediaCodec, idx: Int, chunk: FrameChunk): Boolean {
        return try {
            val buf = mc.getInputBuffer(idx) ?: return false
            buf.clear()
            val sz = chunk.size.coerceAtMost(buf.remaining())
            buf.put(chunk.data, 0, sz)
            mc.queueInputBuffer(idx, 0, sz, chunk.receivedAtUs, 0)
            if (chunk.rtpTimestamp >= 0) {
                presentationRtpTimestamps[chunk.receivedAtUs] = chunk.rtpTimestamp
                if (presentationRtpTimestamps.size > 240) presentationRtpTimestamps.clear()
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "Input queue failed", e)
            false
        }
    }

    private fun queueEmptyInputBuffer(mc: MediaCodec, idx: Int) {
        try {
            mc.queueInputBuffer(idx, 0, 0, System.nanoTime() / 1000, 0)
        } catch (e: Exception) {
            Log.e(TAG, "Empty input queue failed", e)
            markFatal("Failed to return input buffer")
        }
    }

    fun takeStats(): Stats {
        val decoded = decodedSinceStats.getAndSet(0)
        val totalMicros = decodeMicrosSinceStats.getAndSet(0)
        val rendered = renderedSinceStats.getAndSet(0)
        val renderMicros = renderMicrosSinceStats.getAndSet(0)
        return Stats(
            renderedFrames = rendered,
            decodedFrames = decoded,
            queueDepth = chunkQueue.size + pendingOutputBuffers.size(),
            decodeMicros = if (decoded == 0) 0 else totalMicros / decoded,
            renderMicros = if (rendered == 0) 0 else renderMicros / rendered,
            droppedFrames = droppedSinceStats.getAndSet(0),
        )
    }

    fun setFrameRenderedTimingCallback(callback: ((Long, Long) -> Unit)?) {
        onFrameRenderedTiming = callback
    }

    fun feedChunk(
        data: ByteArray, offset: Int, size: Int, isKeyFrame: Boolean = false,
        rtpTimestamp: Long = -1,
    ): SubmissionResult {
        if (!initialized || fatalError.get()) return SubmissionResult.FAILED

        val chunk = chunkPool.poll() ?: if (isKeyFrame) {
            drainQueuedFrames(countDrops = true)
        } else if (replaceQueuedOnOverflow) {
            dropOldestNonKeyFrame()
        } else null
        if (chunk == null) {
            droppedSinceStats.incrementAndGet()
            return SubmissionResult.DROPPED
        }

        val actualSize = size.coerceAtMost(chunk.data.size)
        System.arraycopy(data, offset, chunk.data, 0, actualSize)
        chunk.size = actualSize
        chunk.isKeyFrame = isKeyFrame
        chunk.receivedAtUs = System.nanoTime() / 1000
        chunk.rtpTimestamp = rtpTimestamp

        val pendingIdx = pendingInputBuffers.poll()
        if (pendingIdx != null) {
            val mc = codec
            if (mc == null) {
                recycleChunk(chunk)
                return SubmissionResult.FAILED
            }
            val queued = fillInputBuffer(mc, pendingIdx, chunk)
            recycleChunk(chunk)
            return if (queued) SubmissionResult.ACCEPTED else SubmissionResult.FAILED
        }

        if (!chunkQueue.offer(chunk)) {
            droppedSinceStats.incrementAndGet()
            recycleChunk(chunk)
            return SubmissionResult.DROPPED
        }
        return if (fatalError.get()) SubmissionResult.FAILED else SubmissionResult.ACCEPTED
    }

    @Synchronized
    fun release() {
        decoderGeneration.incrementAndGet()
        initialized = false
        fatalError.set(false)
        drainQueuedFrames()?.let { recycleChunk(it) }
        pendingInputBuffers.clear()
        renderedSinceStats.set(0)
        decodedSinceStats.set(0)
        decodeMicrosSinceStats.set(0)
        renderMicrosSinceStats.set(0)
        droppedSinceStats.set(0)
        presentationRtpTimestamps.clear()
        pendingOutputBuffers.drain().forEach { output ->
            try { codec?.releaseOutputBuffer(output, false) } catch (_: Exception) {}
        }
        try { codec?.stop(); codec?.release() } catch (_: Exception) {}
        codec = null
        try {
            val thread = callbackThread
            thread?.quitSafely()
            if (thread != null && Thread.currentThread() !== thread) {
                thread.join(2000)
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        callbackThread = null
        while (chunkPool.size < inputCapacity) {
            if (!chunkPool.offer(FrameChunk(ByteArray(MAX_INPUT)))) break
        }
    }
}
