package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import android.util.Log

class H264Decoder(private val surface: Surface) {
    private var codec: MediaCodec? = null
    private val TAG = "H264Decoder"

    fun init(width: Int, height: Int) {
        if (codec != null) return
        try {
            Log.d(TAG, "Init: $width x $height")
            val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height)
            format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            
            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            codec?.configure(format, surface, null, 0)
            codec?.start()
            Log.d(TAG, "Codec Started")
        } catch (e: Exception) {
            Log.e(TAG, "Init Fail", e)
        }
    }

    fun decode(data: ByteArray, offset: Int, size: Int) {
        val c = codec ?: return
        try {
            // Block until we get an input buffer (don't drop data!)
            var inputIndex = -1
            while (inputIndex < 0) {
                inputIndex = c.dequeueInputBuffer(10000)
            }
            
            c.getInputBuffer(inputIndex)?.let { buf ->
                buf.clear()
                buf.put(data, offset, size)
                c.queueInputBuffer(inputIndex, 0, size, System.nanoTime() / 1000, 0)
            }

            val info = MediaCodec.BufferInfo()
            var outputIndex = c.dequeueOutputBuffer(info, 0)
            while (outputIndex >= 0) {
                c.releaseOutputBuffer(outputIndex, true)
                outputIndex = c.dequeueOutputBuffer(info, 0)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Decode Error", e)
        }
    }

    fun release() {
        try {
            codec?.stop()
            codec?.release()
        } catch (e: Exception) {}
        codec = null
    }
}
