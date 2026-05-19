package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicLong

/**
 * H.264 hardware decoder — minimal latency configuration.
 * Uses asynchronous MediaCodec callbacks for lowest possible decode latency.
 * Feeds raw Annex B byte chunks directly to MediaCodec.
 */
class H264Decoder(private val surface: Surface) {

    private var codec: MediaCodec? = null
    private var frameCount = 0L
    @Volatile private var initialized = false
    private var callbackThread: HandlerThread? = null

    // Tight queue — only 2 frames max to minimise backlog latency.
    // When full, we drop the OLDEST chunk so the decoder always catches up.
    private val chunkQueue = LinkedBlockingQueue<ByteArray>(2)
    private val nextPts = AtomicLong(0L)
    private var frameDurationUs = 16_667L

    companion object {
        private const val TAG = "H264Decoder"
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
                // Constrained Baseline — no B-frames, fastest decode path
                try {
                    setInteger(
                        MediaFormat.KEY_PROFILE,
                        MediaCodecInfo.CodecProfileLevel.AVCProfileConstrainedBaseline
                    )
                } catch (_: Exception) { /* not all devices support this key */ }
            }

            // Dedicated handler thread for codec callbacks
            callbackThread = HandlerThread("MonitorizeDecoder").also {
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }
            val handler = Handler(callbackThread!!.looper)

            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC).also {
                it.setCallback(object : MediaCodec.Callback() {
                    override fun onInputBufferAvailable(mc: MediaCodec, inputBufferId: Int) {
                        if (!initialized) return
                        // Try to get a chunk; if none available, re-offer the buffer index
                        val chunk = chunkQueue.poll()
                        if (chunk == null) {
                            // No data ready — park this buffer index for later use by feedChunk
                            pendingInputBuffers.offer(inputBufferId)
                            return
                        }
                        fillInputBuffer(mc, inputBufferId, chunk)
                    }

                    override fun onOutputBufferAvailable(
                        mc: MediaCodec, outputBufferId: Int, info: MediaCodec.BufferInfo
                    ) {
                        if (!initialized) return
                        try {
                            mc.releaseOutputBuffer(outputBufferId, true)
                            frameCount++
                        } catch (e: Exception) {
                            if (initialized) Log.e(TAG, "Release error", e)
                        }
                    }

                    override fun onError(mc: MediaCodec, e: MediaCodec.CodecException) {
                        Log.e(TAG, "Codec error", e)
                    }

                    override fun onOutputFormatChanged(mc: MediaCodec, format: MediaFormat) {
                        Log.i(TAG, "Format: $format")
                    }
                }, handler)

                it.configure(format, surface, null, 0)
                it.start()
            }
            frameCount = 0
            initialized = true
        } catch (e: Exception) {
            Log.e(TAG, "Init failed", e)
            initialized = false
        }
    }

    // Queue of input buffer indices that are ready but had no data
    private val pendingInputBuffers = LinkedBlockingQueue<Int>(16)

    private fun fillInputBuffer(mc: MediaCodec, idx: Int, chunk: ByteArray) {
        try {
            val buf = mc.getInputBuffer(idx) ?: return
            buf.clear()
            val sz = chunk.size.coerceAtMost(buf.remaining())
            buf.put(chunk, 0, sz)
            val pts = nextPts.getAndAdd(frameDurationUs)
            mc.queueInputBuffer(idx, 0, sz, pts, 0)
        } catch (e: Exception) {
            if (initialized) Log.e(TAG, "Input error", e)
        }
    }

    fun feedChunk(data: ByteArray, offset: Int, size: Int) {
        if (!initialized) return
        val copy = ByteArray(size)
        System.arraycopy(data, offset, copy, 0, size)

        // If there's a pending input buffer from the callback, fill it immediately
        val pendingIdx = pendingInputBuffers.poll()
        if (pendingIdx != null) {
            val mc = codec ?: return
            fillInputBuffer(mc, pendingIdx, copy)
            return
        }

        // Otherwise queue the chunk — drop oldest when full to prefer newest data
        while (!chunkQueue.offer(copy)) {
            chunkQueue.poll()
        }
    }

    fun release() {
        initialized = false
        chunkQueue.clear()
        pendingInputBuffers.clear()
        try { codec?.stop(); codec?.release() } catch (_: Exception) {}
        codec = null
        try { callbackThread?.quitSafely(); callbackThread?.join(2000) }
        catch (_: InterruptedException) { Thread.currentThread().interrupt() }
        callbackThread = null
        Log.i(TAG, "Released. Frames: $frameCount")
    }
}
