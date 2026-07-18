package app.monitorize.android.input

import java.net.InetAddress
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertSame
import org.junit.Test

class EncryptedUdpSessionTest {
    @Test
    fun encryptsDistinctPacketsWithOneReusableDatagram() {
        val key = ByteArray(32) { it.toByte() }
        val session = InputEventSender.EncryptedUdpSession(
            key, byteArrayOf(1, 2, 3, 4), byteArrayOf(5, 6, 7, 8),
            InetAddress.getLoopbackAddress(), 7113,
        )
        val frame = byteArrayOf(0, 0, 0, 13, 3, 2, 0, 1, 0, 2, 0, 3, 0, 0, 0, 0, 0, 0)

        val firstPacket = session.encrypt(frame)
        val first = firstPacket.data.copyOf(firstPacket.length)
        val secondPacket = session.encrypt(frame)
        val second = secondPacket.data.copyOf(secondPacket.length)

        assertSame(firstPacket, secondPacket)
        assertArrayEquals(
            byteArrayOf(0x4d, 0x5a, 0x49, 0x55, 1, 1, 2, 3, 4, 5, 6, 7, 8),
            first.copyOfRange(0, 13),
        )
        assertArrayEquals(byteArrayOf(0, 0, 0, 0, 0, 0, 0, 1), first.copyOfRange(13, 21))
        assertArrayEquals(byteArrayOf(0, 0, 0, 0, 0, 0, 0, 2), second.copyOfRange(13, 21))
        assertArrayEquals(frame, decrypt(first, key))
        assertArrayEquals(frame, decrypt(second, key))
    }

    private fun decrypt(packet: ByteArray, key: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(key, "AES"),
            GCMParameterSpec(128, packet.copyOfRange(9, 21)),
        )
        cipher.updateAAD(packet, 0, 21)
        return cipher.doFinal(packet, 21, packet.size - 21)
    }
}
