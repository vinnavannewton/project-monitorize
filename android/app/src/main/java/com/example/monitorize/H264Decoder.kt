package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.SystemClock
import android.util.Log
import android.view.Surface

/**
 * H.264 hardware decoder backed by Android MediaCodec (synchronous mode).
 */
class H264Decoder(private val surface: Surface) {

    private var codec: MediaCodec? = null
    private var frameCount = 0L
    private var initialized = false

    companion object {
        private const val TAG = "H264Decoder"
        private const val TIMEOUT_US = 10_000L
        private const val MAX_INPUT_BYTES = 2 * 1024 * 1024
    }

    fun init(width: Int, height: Int) {
        if (initialized) {
            Log.w(TAG, "Already initialized — releasing before re-init")
            release()
        }
        try {
            Log.i(TAG, "Initialising decoder: ${width}×${height}")
            val format = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height
            ).apply {
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_INPUT_BYTES)
                setInteger(MediaFormat.KEY_OPERATING_RATE, 30)
                setInteger(MediaFormat.KEY_PRIORITY, 0)
            }
            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC).also { c ->
                c.configure(format, surface, null, 0)
                c.start()
            }
            frameCount   = 0
            initialized  = true
            Log.i(TAG, "Decoder started OK")
        } catch (e: Exception) {
            Log.e(TAG, "Init failed", e)
            initialized = false
        }
    }

    /**
     * BUG 1 FIX — CAP TRUNCATION AND LOG OVERSIZED NALS
     */
    fun decode(data: ByteArray, offset: Int, size: Int, presentationTimeUs: Long) {
        val c = codec ?: return
        val inputIndex = c.dequeueInputBuffer(TIMEOUT_US)
        if (inputIndex >= 0) {
            val buf = c.getInputBuffer(inputIndex)!!
            buf.clear()
            val actualSize = minOf(size, buf.remaining())
            if (actualSize < size) {
                android.util.Log.w("H264Decoder", "NAL unit truncated: attempted $size bytes, buffer capacity ${buf.remaining()}, wrote $actualSize bytes")
            }
            buf.put(data, offset, actualSize)
            c.queueInputBuffer(inputIndex, 0, actualSize, presentationTimeUs, 0)
        }

        val bufferInfo = MediaCodec.BufferInfo()
        var outputIndex = c.dequeueOutputBuffer(bufferInfo, TIMEOUT_US)
        while (outputIndex != MediaCodec.INFO_TRY_AGAIN_LATER) {
            when {
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    Log.i(TAG, "Output format changed: ${c.outputFormat}")
                }
                outputIndex == MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> {
                    Log.i(TAG, "Output buffers changed")
                }
                outputIndex >= 0 -> {
                    c.releaseOutputBuffer(outputIndex, true)
                    frameCount++
                }
            }
            outputIndex = c.dequeueOutputBuffer(bufferInfo, 0)
        }
    }

    fun release() {
        initialized = false
        try {
            codec?.stop()
            codec?.release()
        } catch (_: Exception) {}
        codec = null
        Log.i(TAG, "Decoder released. Total frames decoded: $frameCount")
    }
}
