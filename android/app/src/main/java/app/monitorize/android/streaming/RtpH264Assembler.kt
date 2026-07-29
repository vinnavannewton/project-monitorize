package app.monitorize.android.streaming

internal data class RtpPacket(
    val sequence: Int,
    val timestamp: Long,
    val marker: Boolean,
    val payloadType: Int,
    val payload: ByteArray
)

internal class RtpH264Assembler {
    var droppedFrame = false
        private set
    private var timestamp = -1L
    private val packets = HashMap<Int, RtpPacket>()
    private var startSequence: Int? = null
    private var endSequence: Int? = null
    private var firstPacketNanos = 0L

    fun reset() {
        timestamp = -1
        packets.clear()
        startSequence = null
        endSequence = null
        firstPacketNanos = 0L
    }

    fun expire(nowNanos: Long, deadlineNanos: Long): Boolean {
        if (packets.isEmpty() || firstPacketNanos == 0L ||
            nowNanos - firstPacketNanos <= deadlineNanos) return false
        reset()
        droppedFrame = true
        return true
    }

    fun offer(packet: RtpPacket): ByteArray? {
        droppedFrame = false
        if (packet.payloadType != 96 || packet.payload.isEmpty()) return null
        if (timestamp != packet.timestamp) {
            droppedFrame = packets.isNotEmpty()
            reset()
            timestamp = packet.timestamp
        }
        if (firstPacketNanos == 0L) firstPacketNanos = System.nanoTime()
        packets[packet.sequence] = packet
        val nalType = packet.payload[0].toInt() and 0x1f
        if (nalType == 9) {
            startSequence = packet.sequence
        } else if (nalType == 28 && packet.payload.size >= 2) {
            if (packet.payload[1].toInt() and 0x80 != 0) {
                if (startSequence == null) startSequence = packet.sequence
            }
        }
        if (packet.marker) endSequence = packet.sequence
        return assembleIfComplete()
    }

    private fun assembleIfComplete(): ByteArray? {
        val start = startSequence ?: return null
        val end = endSequence ?: return null
        val ordered = ArrayList<RtpPacket>()
        var sequence = start
        while (true) {
            ordered += packets[sequence] ?: return null
            if (sequence == end) break
            sequence = (sequence + 1) and 0xffff
            if (ordered.size > 4096) return null
        }
        val output = java.io.ByteArrayOutputStream()
        for (packet in ordered) {
            appendPayload(output, packet.payload) ?: return null
        }
        val frame = output.toByteArray()
        reset()
        return frame
    }

    private fun appendPayload(output: java.io.ByteArrayOutputStream, payload: ByteArray): Unit? {
        val type = payload[0].toInt() and 0x1f
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
        return Unit
    }

    companion object {
        private val START_CODE = byteArrayOf(0, 0, 0, 1)

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
