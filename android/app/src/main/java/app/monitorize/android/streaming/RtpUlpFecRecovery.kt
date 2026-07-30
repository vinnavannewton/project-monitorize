package app.monitorize.android.streaming

internal enum class FecRecoveryStatus {
    NOT_NEEDED,
    RECOVERED,
    UNRECOVERABLE,
    MALFORMED,
}

internal data class FecRecoveryResult(
    val status: FecRecoveryStatus,
    val packet: RtpPacket? = null,
)

internal class RtpUlpFecRecovery(private val fecPayloadType: Int = 122) {
    private val media = LinkedHashMap<Int, RtpPacket>()

    fun remember(packet: RtpPacket) {
        if (packet.payloadType != 96) return
        media[packet.sequence] = packet
        while (media.size > 256) media.remove(media.keys.first())
    }

    fun recover(fec: RtpPacket): FecRecoveryResult {
        val data = fec.payload
        if (fec.payloadType != fecPayloadType || data.size < 14) {
            return FecRecoveryResult(FecRecoveryStatus.MALFORMED)
        }
        val longMask = data[0].toInt() and 0x40 != 0
        val maskBytes = if (longMask) 6 else 2
        if (data.size < 12 + maskBytes) {
            return FecRecoveryResult(FecRecoveryStatus.MALFORMED)
        }
        val base = u16(data, 2)
        val protectionLength = u16(data, 10)
        val recoveryOffset = 12 + maskBytes
        if (protectionLength <= 0 || recoveryOffset + protectionLength > data.size) {
            return FecRecoveryResult(FecRecoveryStatus.MALFORMED)
        }

        val protected = ArrayList<Int>()
        for (bit in 0 until maskBytes * 8) {
            if ((data[12 + bit / 8].toInt() and (0x80 ushr (bit % 8))) != 0) {
                protected += (base + bit) and 0xffff
            }
        }
        if (protected.isEmpty()) {
            return FecRecoveryResult(FecRecoveryStatus.MALFORMED)
        }
        val missing = protected.filterNot(media::containsKey)
        if (missing.isEmpty()) {
            return FecRecoveryResult(FecRecoveryStatus.NOT_NEEDED)
        }
        if (missing.size > 1) {
            return FecRecoveryResult(FecRecoveryStatus.UNRECOVERABLE)
        }

        var pXcc = data[0].toInt() and 0x3f
        var mPt = data[1].toInt() and 0xff
        var timestamp = u32(data, 4)
        var payloadLength = u16(data, 8)
        val payload = data.copyOfRange(recoveryOffset, recoveryOffset + protectionLength)
        for (sequence in protected) {
            val packet = media[sequence] ?: continue
            pXcc = pXcc xor 0 
            mPt = mPt xor ((if (packet.marker) 0x80 else 0) or packet.payloadType)
            timestamp = timestamp xor packet.timestamp
            payloadLength = payloadLength xor packet.payload.size
            for (index in packet.payload.indices.take(protectionLength)) {
                payload[index] = (payload[index].toInt() xor packet.payload[index].toInt()).toByte()
            }
        }
        if (pXcc != 0 || payloadLength !in 1..protectionLength) {
            return FecRecoveryResult(FecRecoveryStatus.MALFORMED)
        }
        val packet = RtpPacket(
            sequence = missing.single(),
            timestamp = timestamp and 0xffff_ffffL,
            marker = mPt and 0x80 != 0,
            payloadType = mPt and 0x7f,
            payload = payload.copyOf(payloadLength),
        ).also(::remember)
        return FecRecoveryResult(FecRecoveryStatus.RECOVERED, packet)
    }

    private fun u16(data: ByteArray, offset: Int): Int =
        ((data[offset].toInt() and 0xff) shl 8) or (data[offset + 1].toInt() and 0xff)

    private fun u32(data: ByteArray, offset: Int): Long =
        (0..3).fold(0L) { value, index ->
            (value shl 8) or (data[offset + index].toLong() and 0xff)
        }
}
