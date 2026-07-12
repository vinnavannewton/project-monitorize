package app.monitorize.android.security

import android.annotation.SuppressLint
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

data class SecureConnection(val socket: SSLSocket, val fingerprint: String)

@SuppressLint("CustomX509TrustManager", "TrustAllX509TrustManager")
fun connectTls(host: String, port: Int, expectedFingerprint: String? = null): SecureConnection {
    
    val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
    })
    val context = SSLContext.getInstance("TLS").apply { init(null, trustAll, SecureRandom()) }
    val socket = context.socketFactory.createSocket() as SSLSocket
    try {
        val protocols = listOf("TLSv1.3", "TLSv1.2")
            .filter { it in socket.supportedProtocols }
            .toTypedArray()
        if (protocols.isNotEmpty()) {
            socket.enabledProtocols = protocols
        }
        socket.connect(InetSocketAddress(host, port), 3000)
        socket.keepAlive = true
        socket.soTimeout = 6000
        socket.startHandshake()
        val certificate = socket.session.peerCertificates.first().encoded
        val fingerprint = MessageDigest.getInstance("SHA-256")
            .digest(certificate).joinToString("") { "%02X".format(it) }
        if (expectedFingerprint != null && !fingerprint.equals(expectedFingerprint, ignoreCase = true)) {
            try { socket.close() } catch (_: Exception) {}
            throw SecurityException("Server certificate changed")
        }
        return SecureConnection(socket, fingerprint)
    } catch (e: Exception) {
        try { socket.close() } catch (_: Exception) {}
        throw e
    }
}

fun readAsciiLine(socket: Socket, limit: Int = 256): String {
    val bytes = ArrayList<Byte>()
    while (bytes.size < limit) {
        val value = socket.inputStream.read()
        if (value < 0) break
        if (value == '\n'.code) return bytes.toByteArray().toString(Charsets.US_ASCII)
        bytes.add(value.toByte())
    }
    throw java.io.IOException("Invalid server response")
}
