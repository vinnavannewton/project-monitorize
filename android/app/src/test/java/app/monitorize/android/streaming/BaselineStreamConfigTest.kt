package app.monitorize.android.streaming

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RtpStreamConfigTest {
    @Test fun parsesHostConfirmedReadyMetadata() {
        val padding = "x".repeat(300)
        val config = parseRtpReady(
            "MZRP1 {\"transport\":\"rtp-udp-v1\",\"status\":\"ready\"," +
                "\"width\":2336,\"height\":1080,\"fps\":90,\"padding\":\"$padding\"}"
        )
        assertEquals(RtpStreamConfig(2336, 1080, 90), config)
    }

    @Test fun rejectsInvalidHostMetadata() {
        assertNull(parseRtpReady(
            "MZRP1 {\"transport\":\"rtp-udp-v1\",\"status\":\"ready\"," +
                "\"width\":2337,\"height\":1080,\"fps\":90}"
        ))
    }
}
