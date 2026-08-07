package app.monitorize.android.streaming

import java.io.ByteArrayInputStream
import java.io.InputStream
import java.util.concurrent.ArrayBlockingQueue
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test


class AudioReceiverTest {
    @Test fun wifiClarityBufferIsDeeperThanUsbBuffer() {
        assertTrue(WIFI_AUDIO_MIN_PRIME_BLOCKS > AUDIO_MAX_PRIME_BLOCKS)
        assertTrue(WIFI_AUDIO_MAX_PRIME_BLOCKS < WIFI_AUDIO_QUEUE_BLOCKS)
        assertTrue(WIFI_AUDIO_PLAYBACK_WAIT_MS > AUDIO_PLAYBACK_WAIT_MS)
    }

    @Test fun hardwarePrimeLeavesOneBlockOfTrackHeadroom() {
        assertEquals(7, audioHardwarePrimeBlocks(20, 8 * 480))
        assertEquals(20, audioHardwarePrimeBlocks(20, 40 * 480))
        assertEquals(1, audioHardwarePrimeBlocks(8, 480))
    }

    private fun rtp(
        sequence: Int,
        payload: ByteArray,
        type: Int = AUDIO_RTP_PAYLOAD_TYPE,
        timestamp: Long = 0x12345678,
    ): ByteArray = byteArrayOf(
        0x80.toByte(), type.toByte(),
        (sequence ushr 8).toByte(), sequence.toByte(),
        (timestamp ushr 24).toByte(), (timestamp ushr 16).toByte(),
        (timestamp ushr 8).toByte(), timestamp.toByte(),
        0, 0, 0, 1,
    ) + payload

    @Test fun parsesRtpOpusWithoutChangingPayload() {
        val payload = byteArrayOf(0xf8.toByte(), 0x34, 0x56)
        val packet = requireNotNull(parseRtpOpus(rtp(0xffff, payload), 15))
        assertEquals(0xffff, packet.sequence)
        assertEquals(0x12345678, packet.timestamp)
        assertArrayEquals(payload, packet.opus)
    }

    @Test fun rejectsWrongPayloadTypeAndEmptyOpus() {
        assertNull(parseRtpOpus(rtp(1, byteArrayOf(1), 96), 13))
        val empty = rtp(1, byteArrayOf())
        assertNull(parseRtpOpus(empty, empty.size))
    }

    @Test fun sequenceDistanceWrapsAtSixteenBits() {
        assertEquals(1, audioSequenceDistance(0, 0xffff))
        assertEquals(0xffff, audioSequenceDistance(0xffff, 0))
    }

    @Test fun jitterBufferPrimesEightPacketsAndReorders() {
        val jitter = AudioJitterBuffer()
        listOf(10, 12, 11, 13, 17, 16, 14).forEach {
            assertTrue(jitter.offer(RtpAudioPacket(it, 0, byteArrayOf(it.toByte()))).isEmpty())
        }
        val output = jitter.offer(RtpAudioPacket(15, 0, byteArrayOf(15)))
        assertEquals((10..17).map(Int::toByte),
            output.map { requireNotNull(it)[0] })
    }

    @Test fun isolatedLossProducesOneMissingFrame() {
        val jitter = AudioJitterBuffer()
        listOf(10, 12, 13, 14, 15, 16, 17, 18).forEach {
            jitter.offer(RtpAudioPacket(it, 0, byteArrayOf(it.toByte())))
        }
        val output = jitter.offer(RtpAudioPacket(19, 0, byteArrayOf(19)))
        assertNull(output[0])
        assertEquals((12..19).map(Int::toByte),
            output.drop(1).map { requireNotNull(it)[0] })
        assertEquals(1, jitter.lostPackets)
    }

    @Test fun duplicateAndLatePacketsAreRejected() {
        val jitter = AudioJitterBuffer(primePackets = 1)
        jitter.offer(RtpAudioPacket(10, 0, byteArrayOf(10)))
        assertTrue(jitter.offer(RtpAudioPacket(10, 0, byteArrayOf(10))).isEmpty())
        assertEquals(1, jitter.latePackets)
    }

    @Test fun largeGapResetsInsteadOfGeneratingLongLoss() {
        val jitter = AudioJitterBuffer()
        jitter.offer(RtpAudioPacket(1, 0, byteArrayOf(1)))
        assertTrue(jitter.offer(RtpAudioPacket(100, 0, byteArrayOf(100))).isEmpty())
        (101..106).forEach { jitter.offer(RtpAudioPacket(it, 0, byteArrayOf(it.toByte()))) }
        val output = jitter.offer(RtpAudioPacket(107, 0, byteArrayOf(107)))
        assertEquals((100..107).map(Int::toByte),
            output.map { requireNotNull(it)[0] })
        assertEquals(99, jitter.lostPackets)
    }

    @Test fun adaptivePrimeGrowsOnUnderrunAndShrinksAfterStableWindows() {
        val tuner = AudioBufferTuner()
        assertEquals(8, tuner.targetBlocks)
        assertEquals(9, tuner.onUnderrun())
        repeat(3) { tuner.onUnderrun() }
        assertEquals(12, tuner.targetBlocks)
        repeat(9) { assertEquals(12, tuner.onStableWindow()) }
        assertEquals(11, tuner.onStableWindow())
        assertEquals(150L, AUDIO_PLAYBACK_WAIT_MS)
    }

    @Test fun fragmentedUsbReadsReconstructOneBlock() {
        val source = ByteArray(AUDIO_BLOCK_BYTES) { (it and 0x7f).toByte() }
        val input = object : InputStream() {
            private val delegate = ByteArrayInputStream(source)
            override fun read(): Int = delegate.read()
            override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
                delegate.read(buffer, offset, minOf(length, 37))
        }
        val target = ByteArray(AUDIO_BLOCK_BYTES)
        assertTrue(readPcmBlock(input, target))
        assertArrayEquals(source, target)
    }

    @Test fun playbackQueueDropsOldestBlock() {
        val queue = ArrayBlockingQueue<ByteArray>(3)
        (1..3).forEach { assertFalse(enqueueLatest(queue, byteArrayOf(it.toByte()))) }
        assertTrue(enqueueLatest(queue, byteArrayOf(4)))
        assertEquals(listOf(2.toByte(), 3.toByte(), 4.toByte()), queue.map { it[0] })
    }

    @Test fun usbReconnectsQuicklyAfterFirstConnection() {
        assertEquals(500, audioRetryDelayMs(isUsb = true, everConnected = false))
        assertEquals(50, audioRetryDelayMs(isUsb = true, everConnected = true))
        assertEquals(750, audioRetryDelayMs(isUsb = false, everConnected = true))
    }

    @Test fun initialProbeStopsAfterFiveFailuresButConnectedAudioKeepsRetrying() {
        assertTrue(shouldRetryAudio(false, 4))
        assertFalse(shouldRetryAudio(false, 5))
        assertTrue(shouldRetryAudio(true, 100))
    }
}
