package com.example.monitorize

import java.net.InetSocketAddress
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

data class SecureConnection(val socket: SSLSocket, val fingerprint: String)

fun connectTls(host: String, port: Int, expectedFingerprint: String? = null): SecureConnection {
    val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
    })
    val context = SSLContext.getInstance("TLS").apply { init(null, trustAll, SecureRandom()) }
    val socket = context.socketFactory.createSocket() as SSLSocket
    socket.enabledProtocols = socket.supportedProtocols.filter { it == "TLSv1.3" }.toTypedArray()
    socket.connect(InetSocketAddress(host, port), 3000)
    socket.startHandshake()
    val certificate = socket.session.peerCertificates.first().encoded
    val fingerprint = MessageDigest.getInstance("SHA-256")
        .digest(certificate).joinToString("") { "%02X".format(it) }
    if (expectedFingerprint != null && !fingerprint.equals(expectedFingerprint, ignoreCase = true)) {
        socket.close()
        throw SecurityException("Server certificate changed")
    }
    return SecureConnection(socket, fingerprint)
}

fun readAsciiLine(socket: SSLSocket, limit: Int = 256): String {
    val bytes = ArrayList<Byte>()
    while (bytes.size < limit) {
        val value = socket.inputStream.read()
        if (value < 0) break
        if (value == '\n'.code) return bytes.toByteArray().toString(Charsets.US_ASCII)
        bytes.add(value.toByte())
    }
    throw java.io.IOException("Invalid server response")
}
