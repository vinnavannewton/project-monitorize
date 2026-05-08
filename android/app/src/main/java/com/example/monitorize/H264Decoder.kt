package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface

class H264Decoder(private val surface: Surface) {

    private var codec: MediaCodec? = null
    private val TIMEOUT_US = 10_000L

    fun init(width: Int, height: Int) {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height)
        format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)

        codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        codec?.configure(format, surface, null, 0)
        codec?.start()
    }

    fun decode(data: ByteArray, offset: Int, size: Int, presentationTimeUs: Long) {
        val c = codec ?: return
        val inputIndex = c.dequeueInputBuffer(TIMEOUT_US)
        if (inputIndex >= 0) {
            val buf = c.getInputBuffer(inputIndex)!!
            buf.clear()
            buf.put(data, offset, size)
            c.queueInputBuffer(inputIndex, 0, size, presentationTimeUs, 0)
        }

        val bufferInfo = MediaCodec.BufferInfo()
        val outputIndex = c.dequeueOutputBuffer(bufferInfo, TIMEOUT_US)
        if (outputIndex >= 0) {
            c.releaseOutputBuffer(outputIndex, true)
        }
    }

    fun release() {
        codec?.stop()
        codec?.release()
        codec = null
    }
}
