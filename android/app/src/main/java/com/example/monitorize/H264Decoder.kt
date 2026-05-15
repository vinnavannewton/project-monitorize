package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaFormat
import android.util.Log
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * H.264 hardware decoder — minimal latency configuration.
 * Feeds raw Annex B byte chunks directly to MediaCodec.
 */
class H264Decoder(private val surface: Surface) {

    private var codec: MediaCodec? = null
    private var frameCount = 0L
    @Volatile private var initialized = false
    private var decodeThread: Thread? = null

    // Small queue — we want the LATEST data, not a backlog.
    // When full, we drop the OLDEST chunk so the decoder always catches up.
    private val chunkQueue = LinkedBlockingQueue<ByteArray>(10)
    private val nextPts = AtomicLong(0L)
    private var frameDurationUs = 16_667L

    companion object {
        private const val TAG = "H264Decoder"
        private const val TIMEOUT_US = 5_000L   // 5ms — half of before
        private const val MAX_INPUT = 2 * 1024 * 1024
    }

    fun init(width: Int, height: Int, fps: Int = 60) {
        if (initialized) release()
        try {
            Log.i(TAG, "Init: ${width}×${height}@${fps}fps")
            frameDurationUs = if (fps > 0) 1_000_000L / fps else 16_667L
            nextPts.set(0L)

            val format = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height
            ).apply {
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_INPUT)
                setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt()) // max speed
                setInteger(MediaFormat.KEY_PRIORITY, 0) // real-time
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1) // request low-latency mode
            }

            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC).also {
                it.configure(format, surface, null, 0)
                it.start()
            }
            frameCount = 0
            initialized = true
            decodeThread = Thread(::decodeLoop, "MonitorizeDecoder").also {
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Init failed", e)
            initialized = false
        }
    }

    fun feedChunk(data: ByteArray, offset: Int, size: Int) {
        if (!initialized) return
        val copy = ByteArray(size)
        System.arraycopy(data, offset, copy, 0, size)
        // Drop oldest when full — always prefer newest data
        while (!chunkQueue.offer(copy)) {
            chunkQueue.poll()
        }
    }

    fun release() {
        initialized = false
        chunkQueue.clear()
        try { decodeThread?.interrupt(); decodeThread?.join(2000) }
        catch (_: InterruptedException) { Thread.currentThread().interrupt() }
        decodeThread = null
        try { codec?.stop(); codec?.release() } catch (_: Exception) {}
        codec = null
        Log.i(TAG, "Released. Frames: $frameCount")
    }

    private fun decodeLoop() {
        val info = MediaCodec.BufferInfo()
        while (initialized) {
            val chunk = try {
                chunkQueue.poll(50, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) { break } ?: continue

            val c = codec ?: break
            try {
                val idx = c.dequeueInputBuffer(TIMEOUT_US)
                if (idx >= 0) {
                    val buf = c.getInputBuffer(idx)!!
                    buf.clear()
                    val sz = chunk.size.coerceAtMost(buf.remaining())
                    buf.put(chunk, 0, sz)
                    val pts = nextPts.getAndAdd(frameDurationUs)
                    c.queueInputBuffer(idx, 0, sz, pts, 0)
                }

                // Drain with minimal wait — 1ms instead of 5ms
                var outIdx = c.dequeueOutputBuffer(info, 1_000)
                while (outIdx != MediaCodec.INFO_TRY_AGAIN_LATER) {
                    if (outIdx >= 0) {
                        c.releaseOutputBuffer(outIdx, true)
                        frameCount++
                    } else if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        Log.i(TAG, "Format: ${c.outputFormat}")
                    }
                    outIdx = c.dequeueOutputBuffer(info, 0)
                }
            } catch (e: Exception) {
                if (initialized) Log.e(TAG, "Decode error", e)
            }
        }
    }
}
