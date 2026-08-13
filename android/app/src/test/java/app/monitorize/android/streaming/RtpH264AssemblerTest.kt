package app.monitorize.android.streaming

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RtpH264AssemblerTest {
    private fun hex(value: String): ByteArray =
        value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

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

    @Test fun reconstructsHevcSingleNalVpsSpsPps() {
        val assembler = RtpH264Assembler(codec = "h265")
        
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x40, 0x01, 0x10))))
        
        assertNull(assembler.offer(packet(2, 90, false, byteArrayOf(0x42, 0x01, 0x20))))
        
        assertNull(assembler.offer(packet(3, 90, false, byteArrayOf(0x44, 0x01, 0x30))))
        
        assertArrayEquals(
            byteArrayOf(
                0, 0, 0, 1, 0x40, 0x01, 0x10,
                0, 0, 0, 1, 0x42, 0x01, 0x20,
                0, 0, 0, 1, 0x44, 0x01, 0x30,
                0, 0, 0, 1, 0x26, 0x01, 0x40
            ),
            assembler.offer(packet(4, 90, true, byteArrayOf(0x26, 0x01, 0x40)))
        )
    }

    @Test fun reconstructsHevcFu() {
        val assembler = RtpH264Assembler(codec = "h265")
        
        val start = packet(10, 90, false, byteArrayOf(0x62, 0x01, 0x93.toByte(), 1, 2))
        
        val end = packet(11, 90, true, byteArrayOf(0x62, 0x01, 0x53, 3, 4))
        assertNull(assembler.offer(start))
        
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x26, 0x01, 1, 2, 3, 4),
            assembler.offer(end)
        )
    }

    @Test fun reconstructsHevcSingleSlicePacket() {
        val assembler = RtpH264Assembler(codec = "h265")
        
        val p = packet(20, 90, true, byteArrayOf(0x02, 0x01, 0x10, 0x20))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x02, 0x01, 0x10, 0x20),
            assembler.offer(p)
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

    @Test fun detectsWholeFrameSequenceGapAfterNormalReorderingCompletes() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(10, 90, false, byteArrayOf(0x09, 0x10))))
        assembler.offer(packet(11, 90, true, byteArrayOf(0x41, 1)))
        assertEquals(0, assembler.completedSequenceGap)

        assertNull(assembler.offer(packet(15, 180, true, byteArrayOf(0x41, 2))))
        assembler.offer(packet(14, 180, false, byteArrayOf(0x09, 0x10)))

        assertEquals(2, assembler.completedSequenceGap)
    }

    @Test fun contiguousFramesAcrossSequenceWrapHaveNoGap() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(65534, 90, false, byteArrayOf(0x09, 0x10))))
        assembler.offer(packet(65535, 90, true, byteArrayOf(0x41, 1)))
        assertNull(assembler.offer(packet(0, 180, false, byteArrayOf(0x09, 0x10))))
        assembler.offer(packet(1, 180, true, byteArrayOf(0x41, 2)))

        assertEquals(0, assembler.completedSequenceGap)
    }

    @Test fun acceptsContiguousAccessUnitsWithTheSameRtpTimestamp() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(10, 90, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x09, 0x10, 0, 0, 0, 1, 0x41, 1),
            assembler.offer(packet(11, 90, true, byteArrayOf(0x41, 1))),
        )

        assertNull(assembler.offer(packet(12, 90, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x09, 0x10, 0, 0, 0, 1, 0x41, 2),
            assembler.offer(packet(13, 90, true, byteArrayOf(0x41, 2))),
        )

        assertEquals(0, assembler.completedSequenceGap)
    }

    @Test fun fecModeCanDisableMediaOnlyCrossFrameGapDetection() {
        val assembler = RtpH264Assembler(detectCrossFrameGaps = false)
        assertNull(assembler.offer(packet(10, 90, false, byteArrayOf(0x09, 0x10))))
        assembler.offer(packet(11, 90, true, byteArrayOf(0x41, 1)))
        assertNull(assembler.offer(packet(20, 180, false, byteArrayOf(0x09, 0x10))))
        assembler.offer(packet(21, 180, true, byteArrayOf(0x41, 2)))

        assertEquals(0, assembler.completedSequenceGap)
    }

    @Test fun handlesSequenceWrap() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(65535, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x65, 1, 2),
            assembler.offer(packet(0, 90, true, byteArrayOf(0x7c, 0x45, 2))),
        )
    }

    @Test fun ignoresLatePacketFromFinalizedTimestamp() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x09, 0x10, 0, 0, 0, 1, 0x65, 1),
            assembler.offer(packet(2, 90, true, byteArrayOf(0x65, 1))),
        )
        assertNull(assembler.offer(packet(3, 90, true, byteArrayOf(0x41, 2))))
        assertNull(assembler.offer(packet(4, 180, false, byteArrayOf(0x09, 0x10))))
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x09, 0x10, 0, 0, 0, 1, 0x41, 3),
            assembler.offer(packet(5, 180, true, byteArrayOf(0x41, 3))),
        )
        assertFalse(assembler.droppedFrame)
    }

    @Test fun keepsCurrentFrameWhilePacketsFromNextTimestampArrive() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertNull(assembler.offer(packet(3, 90, true, byteArrayOf(0x7c, 0x45, 3))))
        assertNull(assembler.offer(packet(4, 180, false, byteArrayOf(0x7c, 0x85.toByte(), 4))))
        assertFalse(assembler.droppedFrame)
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x65, 1, 2, 3),
            assembler.offer(packet(2, 90, false, byteArrayOf(0x7c, 0x05, 2))),
        )
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 1, 0x65, 4, 5),
            assembler.offer(packet(5, 180, true, byteArrayOf(0x7c, 0x45, 5))),
        )
        assertFalse(assembler.droppedFrame)
    }

    @Test fun seventhTimestampDropsOnlyOldestIncompleteFrame() {
        val assembler = RtpH264Assembler()
        assertNull(assembler.offer(packet(1, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))))
        assertNull(assembler.offer(packet(3, 90, true, byteArrayOf(0x7c, 0x45, 3))))
        for (index in 2..6) {
            assertNull(assembler.offer(packet(index + 2, index * 90, false,
                byteArrayOf(0x7c, 0x85.toByte(), index.toByte()))))
            assertFalse(assembler.droppedFrame)
        }
        assertNull(assembler.offer(packet(9, 630, false, byteArrayOf(0x7c, 0x85.toByte(), 7))))
        assertTrue(assembler.droppedFrame)
        assertTrue(assembler.lostPackets == 1)
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
        val result = recovery.recover(RtpPacket(20, 0, false, 122, fecPayload))
        assertTrue(result.status == FecRecoveryStatus.RECOVERED)
        val recovered = requireNotNull(result.packet)
        assertTrue(recovered.marker)
        assertArrayEquals(missingPayload, recovered.payload)
        assertTrue(recovered.sequence == 11)
    }

    @Test fun recoversPacketFromGstreamerRfc5109Fixture() {
        val recovery = RtpUlpFecRecovery()
        recovery.remember(
            RtpPacket(31371, 0xd2b3cf64L, false, 96, hex("0910"))
        )
        recovery.remember(
            RtpPacket(31373, 0xd2b3cf64L, false, 96, hex("68ce32c8"))
        )
        val fecPayload = hex(
            "00607a8bd2b3cf64001c001ae000" +
                "069cf2dddc426c05a8303035280000030008000003001478b17c"
        )
        val result = recovery.recover(
            RtpPacket(31386, 0xd2b3cf64L, false, 122, fecPayload)
        )

        assertTrue(result.status == FecRecoveryStatus.RECOVERED)
        val recovered = requireNotNull(result.packet)
        assertTrue(recovered.sequence == 31372)
        assertArrayEquals(
            hex("6742c015dc426c05a8303035280000030008000003001478b17c"),
            recovered.payload,
        )
    }

    @Test fun classifiesParityThatIsNotNeeded() {
        val recovery = RtpUlpFecRecovery()
        val first = packet(10, 90, false, byteArrayOf(1, 2, 3))
        val second = packet(11, 90, true, byteArrayOf(4, 5, 6))
        recovery.remember(first)
        recovery.remember(second)
        val fecPayload = ByteArray(17)
        fecPayload[1] = 0x80.toByte()
        fecPayload[3] = 10
        fecPayload[11] = 3
        fecPayload[12] = 0xc0.toByte()
        assertTrue(
            recovery.recover(RtpPacket(20, 0, false, 122, fecPayload)).status ==
                FecRecoveryStatus.NOT_NEEDED
        )
    }

    @Test fun recoversAcrossMediaSequenceWrap() {
        val recovery = RtpUlpFecRecovery()
        val first = packet(65535, 90, false, byteArrayOf(0x7c, 0x85.toByte(), 1))
        val missingPayload = byteArrayOf(0x7c, 0x45, 2)
        recovery.remember(first)
        val fecPayload = ByteArray(17)
        fecPayload[1] = 0x80.toByte()
        fecPayload[2] = 0xff.toByte()
        fecPayload[3] = 0xff.toByte()
        fecPayload[9] = (first.payload.size xor missingPayload.size).toByte()
        fecPayload[11] = 3
        fecPayload[12] = 0xc0.toByte()
        for (index in missingPayload.indices) {
            fecPayload[14 + index] =
                (first.payload[index].toInt() xor missingPayload[index].toInt()).toByte()
        }

        val result = recovery.recover(RtpPacket(20, 0, false, 122, fecPayload))

        assertTrue(result.status == FecRecoveryStatus.RECOVERED)
        assertTrue(result.packet?.sequence == 0)
    }

    @Test fun classifiesMultipleMissingPacketsAndMalformedParity() {
        val recovery = RtpUlpFecRecovery()
        val fecPayload = ByteArray(17)
        fecPayload[3] = 10
        fecPayload[11] = 3
        fecPayload[12] = 0xc0.toByte()
        assertTrue(
            recovery.recover(RtpPacket(20, 0, false, 122, fecPayload)).status ==
                FecRecoveryStatus.UNRECOVERABLE
        )
        assertTrue(
            recovery.recover(RtpPacket(21, 0, false, 122, byteArrayOf(1))).status ==
                FecRecoveryStatus.MALFORMED
        )
    }
}
