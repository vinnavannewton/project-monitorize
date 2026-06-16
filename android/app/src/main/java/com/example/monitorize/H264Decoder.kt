package com.example.monitorize

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicLong


class H264Decoder(private val surface: Surface) {

    private var codec: MediaCodec? = null
    private var frameCount = 0L
    @Volatile private var initialized = false
    private var callbackThread: HandlerThread? = null

    class FrameChunk(val data: ByteArray) {
        var size: Int = 0
        var isKeyFrame: Boolean = false
    }

    
    
    private val POOL_SIZE = 3
    private val chunkPool = ArrayBlockingQueue<FrameChunk>(POOL_SIZE)
    private val chunkQueue = LinkedBlockingQueue<FrameChunk>(POOL_SIZE)
    
    private val nextPts = AtomicLong(0L)
    private var frameDurationUs = 16_667L

    init {
        for (i in 0 until POOL_SIZE) {
            chunkPool.offer(FrameChunk(ByteArray(MAX_INPUT)))
        }
    }

    companion object {
        private const val TAG = "H264Decoder"
        private const val MAX_INPUT = 2 * 1024 * 1024
    }

    fun init(width: Int, height: Int) {
        if (initialized) release()
        try {
            Log.i(TAG, "Init: ${width}×${height}")
            frameDurationUs = 16_667L
            nextPts.set(0L)

            val format = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height
            ).apply {
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_INPUT)
                setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt())
                setInteger(MediaFormat.KEY_PRIORITY, 0)
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                try {
                    setInteger(
                        MediaFormat.KEY_PROFILE,
                        MediaCodecInfo.CodecProfileLevel.AVCProfileConstrainedBaseline
                    )
                } catch (_: Exception) {}
            }

            callbackThread = HandlerThread("MonitorizeDecoder").also {
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }
            val handler = Handler(callbackThread!!.looper)

            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC).also {
                it.setCallback(object : MediaCodec.Callback() {
                    override fun onInputBufferAvailable(mc: MediaCodec, inputBufferId: Int) {
                        if (!initialized) return
                        val chunk = chunkQueue.poll()
                        if (chunk == null) {
                            pendingInputBuffers.offer(inputBufferId)
                            return
                        }
                        fillInputBuffer(mc, inputBufferId, chunk)
                        recycleChunk(chunk)
                    }

                    override fun onOutputBufferAvailable(
                        mc: MediaCodec, outputBufferId: Int, info: MediaCodec.BufferInfo
                    ) {
                        if (!initialized) return
                        try {
                            mc.releaseOutputBuffer(outputBufferId, true)
                            frameCount++
                        } catch (e: Exception) {}
                    }

                    override fun onError(mc: MediaCodec, e: MediaCodec.CodecException) {}

                    override fun onOutputFormatChanged(mc: MediaCodec, format: MediaFormat) {}
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

    private val pendingInputBuffers = LinkedBlockingQueue<Int>(16)

    private fun recycleChunk(chunk: FrameChunk) {
        chunk.size = 0
        chunk.isKeyFrame = false
        chunkPool.offer(chunk)
    }

    private fun dropOldestNonKeyFrame(): FrameChunk? {
        val iterator = chunkQueue.iterator()
        while (iterator.hasNext()) {
            val chunk = iterator.next()
            if (!chunk.isKeyFrame) {
                iterator.remove()
                return chunk
            }
        }
        return null
    }

    private fun drainQueuedFrames(reusable: FrameChunk? = null): FrameChunk? {
        var candidate = reusable
        while (true) {
            val dropped = chunkQueue.poll() ?: break
            if (candidate == null) {
                candidate = dropped
            } else {
                recycleChunk(dropped)
            }
        }
        return candidate
    }

    private fun obtainChunk(isKeyFrame: Boolean): FrameChunk? {
        chunkPool.poll()?.let { return it }

        if (isKeyFrame) {
            return drainQueuedFrames()
        }

        return dropOldestNonKeyFrame()
    }

    private fun fillInputBuffer(mc: MediaCodec, idx: Int, chunk: FrameChunk) {
        try {
            val buf = mc.getInputBuffer(idx) ?: return
            buf.clear()
            val sz = chunk.size.coerceAtMost(buf.remaining())
            buf.put(chunk.data, 0, sz)
            val pts = System.nanoTime() / 1000
            mc.queueInputBuffer(idx, 0, sz, pts, 0)
        } catch (e: Exception) {}
    }

    fun feedChunk(data: ByteArray, offset: Int, size: Int, isKeyFrame: Boolean = false) {
        if (!initialized) return

        val chunk = obtainChunk(isKeyFrame) ?: return

        val actualSize = size.coerceAtMost(chunk.data.size)
        System.arraycopy(data, offset, chunk.data, 0, actualSize)
        chunk.size = actualSize
        chunk.isKeyFrame = isKeyFrame

        val pendingIdx = pendingInputBuffers.poll()
        if (pendingIdx != null) {
            val mc = codec
            if (mc == null) {
                recycleChunk(chunk)
                return
            }
            fillInputBuffer(mc, pendingIdx, chunk)
            recycleChunk(chunk)
            return
        }

        if (!chunkQueue.offer(chunk)) {
            if (isKeyFrame) {
                drainQueuedFrames()?.let { recycleChunk(it) }
            } else {
                dropOldestNonKeyFrame()?.let { recycleChunk(it) }
            }

            if (!chunkQueue.offer(chunk)) {
                recycleChunk(chunk)
            }
        }
    }

    fun release() {
        initialized = false
        drainQueuedFrames()?.let { recycleChunk(it) }
        pendingInputBuffers.clear()
        try { codec?.stop(); codec?.release() } catch (_: Exception) {}
        codec = null
        try { callbackThread?.quitSafely(); callbackThread?.join(2000) }
        catch (_: InterruptedException) { Thread.currentThread().interrupt() }
        callbackThread = null
    }
}
