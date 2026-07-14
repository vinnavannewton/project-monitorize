package app.monitorize.android.streaming

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RtpH264AssemblerTest {
    private fun datagram(sequence: Int, timestamp: Int, marker: Boolean, payload: ByteArray): ByteArray {
        return byteArrayOf(
            0x80.toByte(), (96 or if (marker) 0x80 else 0).toByte(),
            (sequence ushr 8).toByte(), sequence.toByte(),
            (timestamp ushr 24).toByte(), (timestamp ushr 16).toByte(),
            (timestamp ushr 8).toByte(), timestamp.toByte(),
            0, 0, 0, 1,
        ) + payload
    }

    private fun packet(sequence: Int, timestamp: Int, marker: Boolean, payload: ByteArray): RtpPacket {
        val bytes = datagram(sequence, timestamp, marker, payload)
        return requireNotNull(RtpH264Assembler.parse(bytes, bytes.size))
    }

    @Test fun reconstructsSingleNal() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(6, 90, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x09, 0x10, 0, 0, 0, 1, 0x65, 1, 2),
            assembler.offer(packet(7, 90, true, byteArrayOf(0x65, 1, 2))),
        )
    }

    @Test fun markerArrivingBeforeAudDoesNotCreateFakeFrame() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(12, 90, true, byteArrayOf(0x65, 3))))
        assertNull(assembler.offer(packet(10, 90, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(
                0, 0, 0, 1, 0x09, 0x10,
                0, 0, 0, 1, 0x41, 2,
                0, 0, 0, 1, 0x65, 3,
            ),
            assembler.offer(packet(11, 90, false, byteArrayOf(0x41, 2))),
        )
    }

    @Test fun reconstructsReorderedFuA() {
        val assembler = RtpH264Assembler()
        val end = packet(12, 90, true, byteArrayOf(0x7c, 0x45, 3, 4))
        val start = packet(10, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))
        val middle = packet(11, 90, false, byteArrayOf(0x7c, 0x05, 2))
        assertNull(assembler.offer(end))
        assertNull(assembler.offer(start))
        assertArrayEquals(byteArrayOf(0, 0, 0, 1, 0x65, 1, 2, 3, 4), assembler.offer(middle))
    }

    @Test fun handlesSequenceWrap() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(65535, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x65, 1, 2),
            assembler.offer(packet(0, 90, true, byteArrayOf(0x7c, 0x45, 2))),
        )
    }

    @Test fun reportsIncompleteFrameWhenTimestampAdvances() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertNull(assembler.offer(packet(3, 90, true, byteArrayOf(0x7c, 0x45, 3))))
        assertNull(assembler.offer(packet(4, 180, false, byteArrayOf(0x7c, 0x85.toByte(), 4))))
        assertTrue(assembler.droppedFrame)
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x65, 4, 5),
            assembler.offer(packet(5, 180, true, byteArrayOf(0x7c, 0x45, 5))),
        )
        assertFalse(assembler.droppedFrame)
    }

    @Test fun expiresIncompleteFrameAtDeadline() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertTrue(assembler.expire(Long.MAX_VALUE, 1))
        assertTrue(assembler.droppedFrame)
    }

    @Test fun recoversOneMissingPacketWithUlpFec() {
        val recovery = RtpUlpFecRecovery()
        val first = packet(10, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))
        val missingPayload = byteArrayOf(0x7c, 0x45, 2)
        recovery.remember(first)
        val fecPayload = ByteArray(17)
        fecPayload[1] = 0x80.toByte() 
        fecPayload[3] = 10 
        fecPayload[9] = (first.payload.size xor missingPayload.size).toByte()
        fecPayload[11] = 3 
        fecPayload[12] = 0xc0.toByte() 
        for (index in 0..2) {
            fecPayload[14 + index] =
                (first.payload[index].toInt() xor missingPayload[index].toInt()).toByte()
        }
        val recovered = recovery.recover(RtpPacket(20, 0, false, 122, fecPayload))
        requireNotNull(recovered)
        assertTrue(recovered.marker)
        assertArrayEquals(missingPayload, recovered.payload)
        assertTrue(recovered.sequence == 11)
    }
}
