package app.monitorize.android.streaming

internal data class RtpPacket(
    val sequence: Int,
    val timestamp: Long,
    val marker: Boolean,
    val payloadType: Int,
    val payload: ByteArray
)

internal class RtpH264Assembler(private val detectCrossFrameGaps: Boolean = true, codec: String = "h264") {
    private val isHevc = codec == "h265"
    var droppedFrame = false
        private set
    var lostPackets = 0
        private set
    var completedTimestamp: Long? = null
        private set
    var completedAssemblyNanos: Long? = null
        private set
    var completedSequenceGap: Int = 0
        private set
    var completedFirstPacketNanos: Long? = null
        private set
    private val frames = java.util.ArrayDeque<Frame>()
    private var lastFinalizedTimestamp: Long? = null
    private var lastCompletedEndSequence: Int? = null

    fun reset() {
        frames.clear()
        lastFinalizedTimestamp = null
        completedTimestamp = null
        completedAssemblyNanos = null
        completedSequenceGap = 0
        completedFirstPacketNanos = null
        lastCompletedEndSequence = null
        droppedFrame = false
        lostPackets = 0
    }

    fun expire(nowNanos: Long, deadlineNanos: Long): Boolean {
        beginOperation()
        val oldest = frames.peekFirst() ?: return false
        if (nowNanos - oldest.firstPacketNanos <= deadlineNanos) return false
        dropOldest()
        return droppedFrame
    }

    fun offer(packet: RtpPacket): ByteArray? {
        beginOperation()
        if (packet.payloadType != 96 || packet.payload.isEmpty()) return null

        var frame = frames.firstOrNull { it.timestamp == packet.timestamp }
        if (frame == null) {
            val finalized = lastFinalizedTimestamp
            if (finalized != null && !isNewerTimestamp(packet.timestamp, finalized)) {
                val nextSequence = lastCompletedEndSequence?.let { (it + 1) and 0xffff }
                val sameTimestampNextAccessUnit =
                    packet.timestamp == finalized &&
                    packet.isAccessUnitDelimiter(isHevc) &&
                    packet.sequence == nextSequence
                if (!sameTimestampNextAccessUnit) return null
            }
            val newest = frames.peekLast()
            if (newest != null && !isNewerTimestamp(packet.timestamp, newest.timestamp)) {
                return null
            }
            if (frames.size == MAX_FRAME_WINDOW) dropOldest()
            frame = Frame(packet.timestamp, System.nanoTime(), isHevc)
            frames.addLast(frame)
        }
        frame.offer(packet)
        return pollCompleted()
    }

    fun pollCompleted(): ByteArray? {
        val oldest = frames.peekFirst() ?: return null
        val result = oldest.assemble() ?: return null
        frames.removeFirst()
        lastFinalizedTimestamp = oldest.timestamp
        completedTimestamp = oldest.timestamp
        completedAssemblyNanos = (System.nanoTime() - oldest.firstPacketNanos).coerceAtLeast(0)
        completedFirstPacketNanos = oldest.firstPacketNanos
        val start = requireNotNull(oldest.startSequence)
        val end = requireNotNull(oldest.endSequence)
        val expected = lastCompletedEndSequence?.let { (it + 1) and 0xffff }
        completedSequenceGap = if (detectCrossFrameGaps && expected != null) {
            val distance = (start - expected) and 0xffff
            if (distance in 1..0x7fff) distance.coerceAtMost(MAX_PACKETS_PER_FRAME) else 0
        } else 0
        lastCompletedEndSequence = end
        return result
    }

    private fun beginOperation() {
        droppedFrame = false
        lostPackets = 0
        completedSequenceGap = 0
    }

    private fun dropOldest() {
        val oldest = frames.pollFirst() ?: return
        lastFinalizedTimestamp = oldest.timestamp
        lastCompletedEndSequence = null
        droppedFrame = true
        lostPackets = oldest.missingPacketCount()
    }

    private class Frame(val timestamp: Long, val firstPacketNanos: Long, val isHevc: Boolean) {
        private val packets = HashMap<Int, RtpPacket>()
        var startSequence: Int? = null
            private set
        var endSequence: Int? = null
            private set

        fun offer(packet: RtpPacket) {
            packets[packet.sequence] = packet
            val nalType = if (isHevc) {
                (packet.payload[0].toInt() ushr 1) and 0x3f
            } else {
                packet.payload[0].toInt() and 0x1f
            }
            if ((!isHevc && nalType == 9) || (isHevc && nalType in 32..35)) {
                if (!isHevc || nalType == 35 || startSequence == null) {
                    startSequence = packet.sequence
                }
            } else if (
                (!isHevc && nalType == 28 && packet.payload.size >= 2 && packet.payload[1].toInt() and 0x80 != 0) ||
                (isHevc && nalType == 49 && packet.payload.size >= 3 && packet.payload[2].toInt() and 0x80 != 0)
            ) {
                if (startSequence == null) startSequence = packet.sequence
            } else if (isHevc && nalType in 0..31 && packet.marker && startSequence == null) {
                startSequence = packet.sequence
            }
            if (packet.marker) endSequence = packet.sequence
        }

        fun assemble(): ByteArray? {
            val ordered = orderedPackets() ?: return null
            val output = java.io.ByteArrayOutputStream()
            for (packet in ordered) {
                appendPayload(output, packet.payload, isHevc) ?: return null
            }
            return output.toByteArray()
        }

        fun missingPacketCount(): Int {
            val start = startSequence ?: return 0
            val end = endSequence ?: return 0
            var missing = 0
            var sequence = start
            repeat(MAX_PACKETS_PER_FRAME) {
                if (!packets.containsKey(sequence)) missing++
                if (sequence == end) return missing
                sequence = (sequence + 1) and 0xffff
            }
            return 0
        }

        private fun orderedPackets(): List<RtpPacket>? {
            val start = startSequence ?: return null
            val end = endSequence ?: return null
            val ordered = ArrayList<RtpPacket>()
            var sequence = start
            repeat(MAX_PACKETS_PER_FRAME) {
                ordered += packets[sequence] ?: return null
                if (sequence == end) return ordered
                sequence = (sequence + 1) and 0xffff
            }
            return null
        }
    }

    companion object {
        private const val MAX_FRAME_WINDOW = 6
        private const val MAX_PACKETS_PER_FRAME = 4096
        private val START_CODE = byteArrayOf(0, 0, 0, 1)

        private fun isNewerTimestamp(candidate: Long, reference: Long): Boolean {
            val distance = (candidate - reference) and 0xffffffffL
            return distance in 1..0x7fffffffL
        }

        private fun RtpPacket.isAccessUnitDelimiter(isHevc: Boolean): Boolean {
            if (payload.isEmpty()) return false
            val type = if (isHevc) (payload[0].toInt() ushr 1) and 0x3f else payload[0].toInt() and 0x1f
            return if (isHevc) type == 35 else type == 9
        }

        private fun appendPayload(
            output: java.io.ByteArrayOutputStream,
            payload: ByteArray,
            isHevc: Boolean
        ): Unit? {
            val type = if (isHevc) (payload[0].toInt() ushr 1) and 0x3f else payload[0].toInt() and 0x1f
            if (!isHevc) {
                when {
                    type in 1..23 -> {
                        output.write(START_CODE)
                        output.write(payload)
                    }
                    type == 24 -> {
                        var offset = 1
                        while (offset + 2 <= payload.size) {
                            val size = ((payload[offset].toInt() and 0xff) shl 8) or
                                (payload[offset + 1].toInt() and 0xff)
                            offset += 2
                            if (size <= 0 || offset + size > payload.size) return null
                            output.write(START_CODE)
                            output.write(payload, offset, size)
                            offset += size
                        }
                    }
                    type == 28 && payload.size >= 3 -> {
                        val header = payload[1].toInt() and 0xff
                        if (header and 0x80 != 0) {
                            output.write(START_CODE)
                            output.write((payload[0].toInt() and 0xe0) or (header and 0x1f))
                        }
                        output.write(payload, 2, payload.size - 2)
                    }
                    else -> return null
                }
            } else {
                when {
                    type in 0..47 -> {
                        output.write(START_CODE)
                        output.write(payload)
                    }
                    type == 48 -> {
                        var offset = 2
                        while (offset + 2 <= payload.size) {
                            val size = ((payload[offset].toInt() and 0xff) shl 8) or
                                (payload[offset + 1].toInt() and 0xff)
                            offset += 2
                            if (size <= 0 || offset + size > payload.size) return null
                            output.write(START_CODE)
                            output.write(payload, offset, size)
                            offset += size
                        }
                    }
                    type == 49 && payload.size >= 4 -> {
                        val innerType = payload[2].toInt() and 0x3f
                        val startBit = payload[2].toInt() and 0x80 != 0
                        if (startBit) {
                            output.write(START_CODE)
                            output.write(((payload[0].toInt() and 0x81)) or ((innerType and 0x3f) shl 1))
                            output.write(payload[1].toInt() and 0xff)
                        }
                        output.write(payload, 3, payload.size - 3)
                    }
                    else -> return null
                }
            }
            return Unit
        }

        fun parse(datagram: ByteArray, size: Int): RtpPacket? {
            if (size < 12 || (datagram[0].toInt() and 0xff) ushr 6 != 2) return null
            val csrcCount = datagram[0].toInt() and 0x0f
            var offset = 12 + csrcCount * 4
            if (offset > size) return null
            if (datagram[0].toInt() and 0x10 != 0) {
                if (offset + 4 > size) return null
                val words = ((datagram[offset + 2].toInt() and 0xff) shl 8) or
                    (datagram[offset + 3].toInt() and 0xff)
                offset += 4 + words * 4
            }
            if (offset >= size) return null
            val sequence = ((datagram[2].toInt() and 0xff) shl 8) or
                (datagram[3].toInt() and 0xff)
            val timestamp = (4..7).fold(0L) { value, index ->
                (value shl 8) or (datagram[index].toLong() and 0xff)
            }
            return RtpPacket(
                sequence, timestamp, datagram[1].toInt() and 0x80 != 0,
                datagram[1].toInt() and 0x7f,
                datagram.copyOfRange(offset, size)
            )
        }
    }
}
