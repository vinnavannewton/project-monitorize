package app.monitorize.android.streaming

import app.monitorize.android.preferredSurfaceFrameRate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RtpStreamConfigTest {
    @Test fun balancedOutputRendersSixtyFpsOnEveryOther120HzCallback() {
        assertTrue(balancedFrameDue(-1L, 0L, 60))
        assertTrue(!balancedFrameDue(0L, 8_333_333L, 60))
        assertTrue(balancedFrameDue(0L, 16_666_666L, 60))
    }

    @Test fun balancedOutputStillRendersEvery120HzCallbackFor120Fps() {
        assertTrue(balancedFrameDue(0L, 8_333_333L, 120))
    }

    @Test fun surfaceFrameRateUsesHighestExactMultipleForLowLatency() {
        assertEquals(
            120.00001f,
            preferredSurfaceFrameRate(60, listOf(60f, 120.00001f)),
        )
        assertEquals(60f, preferredSurfaceFrameRate(60, listOf(60f, 90f)))
    }

    @Test fun surfaceFrameRateFallsBackToHighestDisplayRate() {
        assertEquals(120f, preferredSurfaceFrameRate(90, listOf(60f, 120f)))
        assertEquals(60f, preferredSurfaceFrameRate(60, emptyList()))
    }

    @Test fun assemblyPercentileUsesNearestRank() {
        assertEquals(0f, percentile95(emptyList()), 0f)
        assertEquals(19f, percentile95((1..20).map(Int::toFloat)), 0f)
    }

    @Test fun rtpFrameDeadlineAllowsPacedRecoveryFramesWithinBounds() {
        assertEquals(250_000_000L, rtpFrameDeadlineNanos(60))
        assertEquals(250_000_000L, rtpFrameDeadlineNanos(30))
        assertEquals(250_000_000L, rtpFrameDeadlineNanos(24))
        assertEquals(150_000_000L, rtpFrameDeadlineNanos(240))
    }

    @Test fun recoveryIdrRequestsHaveOneSecondCooldown() {
        assertTrue(recoveryIdrAllowed(1_000, 2_000))
        assertTrue(!recoveryIdrAllowed(1_000, 1_999))
    }

    @Test fun rtpSessionBecomesInactiveAfterFiveSecondsWithoutPackets() {
        assertTrue(!rtpSessionInactive(1_000, 5_999))
        assertTrue(rtpSessionInactive(1_000, 6_000))
        assertTrue(!rtpSessionInactive(5_500, 6_000))
    }

    @Test fun recoveryIdrUsesDedicatedControlMessage() {
        val message = buildRtpControlMessage(49_152, 60, 1920, 1200, requestIdr = true)
        assertTrue(message.contains("\"type\":\"idr\""))
        assertTrue(message.contains("\"port\":49152"))
    }

    @Test fun negotiationUsesExplicitStartControlMessage() {
        val message = buildRtpControlMessage(49_152, 60, 1920, 1200)
        assertTrue(message.contains("\"type\":\"start\""))
    }

    @Test fun balancedOutputQueueKeepsNewestTwoBuffers() {
        val queue = PendingOutputQueue(2)
        val first = PendingOutput(1, 10L)
        val second = PendingOutput(2, 20L)
        val third = PendingOutput(3, 30L)
        assertNull(queue.offer(first))
        assertNull(queue.offer(second))
        assertEquals(first, queue.offer(third))
        assertEquals(second, queue.poll())
        assertEquals(third, queue.poll())
        assertEquals(0, queue.size())
    }

    @Test fun lowLatencyOutputQueueKeepsOnlyNewestBuffer() {
        val queue = PendingOutputQueue(1)
        val first = PendingOutput(1, 10L)
        val second = PendingOutput(2, 20L)
        assertNull(queue.offer(first))
        assertEquals(first, queue.offer(second))
        assertEquals(second, queue.poll())
        assertEquals(0, queue.size())
    }

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

    @Test fun parsesEffectiveFecMetadataAndRejectsUnknownPercentage() {
        assertEquals(
            RtpStreamConfig(1920, 1200, 60, 122, 10),
            parseRtpReady(
                "MZRP1 {\"transport\":\"rtp-udp-v1\",\"status\":\"ready\"," +
                    "\"width\":1920,\"height\":1200,\"fps\":60," +
                    "\"fecPt\":122,\"fecPercent\":10}"
            ),
        )
        assertNull(
            parseRtpReady(
                "MZRP1 {\"transport\":\"rtp-udp-v1\",\"status\":\"ready\"," +
                    "\"width\":1920,\"height\":1200,\"fps\":60,\"fecPercent\":5}"
            ),
        )
    }

    @Test fun aggregatesFourQuarterSecondWindowsBeforeFeedback() {
        val accumulator = RtpFeedbackAccumulator()
        val quarter = StreamStats(
            decodedFrames = 15,
            renderedFrames = 15,
            inputFrames = 15,
            decodeMs = 10f,
            renderMs = 20f,
            queueDepth = 1,
            mediaPackets = 100,
            residualLost = 1,
            measurementMs = 250,
        )
        repeat(3) {
            assertNull(accumulator.add(quarter, 125_000, 100, 1))
        }
        val result = requireNotNull(accumulator.add(quarter, 125_000, 100, 1))
        assertEquals(1_000, result.measurementMs)
        assertEquals(4_000, result.receivedKbps)
        assertEquals(400, result.packetsPerSecond)
        assertEquals(4, result.lostPackets)
        assertEquals(60, result.renderedFrames)
        assertEquals(60f, result.renderedFps, 0.01f)
        assertEquals(10f, result.decodeMs, 0.01f)
        assertTrue(result.lossPercent > 0f)
    }

    @Test fun feedbackLossExcludesFecPacketsAndUsesResidualMediaLoss() {
        val accumulator = RtpFeedbackAccumulator()
        val result = requireNotNull(
            accumulator.add(
                StreamStats(
                    mediaPackets = 90,
                    fecPackets = 10,
                    fecRecovered = 1,
                    residualLost = 1,
                    lostPackets = 1,
                    measurementMs = 1_000,
                ),
                windowReceivedBytes = 120_000,
                windowReceivedPackets = 100,
                windowLostPackets = 1,
            ),
        )
        assertEquals(90, result.mediaPackets)
        assertEquals(10, result.fecPackets)
        assertEquals(1, result.fecRecovered)
        assertEquals(1, result.residualLost)
        assertEquals(100f / 92f, result.lossPercent, 0.01f)
    }

    @Test fun feedbackBitrateUsesTotalBytesAcrossBurstyWindows() {
        val accumulator = RtpFeedbackAccumulator()
        val byteBursts = listOf(25_000L, 175_000L, 50_000L, 150_000L)
        byteBursts.forEachIndexed { index, bytes ->
            val result = accumulator.add(
                StreamStats(renderedFrames = 15, measurementMs = 250),
                bytes, 100, 0,
            )
            if (index < byteBursts.lastIndex) assertNull(result)
            else {
                requireNotNull(result)
                assertEquals(3_200, result.receivedKbps)
                assertEquals(60, result.renderedFrames)
            }
        }
    }

    @Test fun clockSyncCalculatesCaptureToRenderLatencyAndNtpError() {
        val sync = RtpClockSync()
        val frame = RenderedRtpFrame(timestamp = 99, renderedAtNs = 1_160_000_000)
        sync.recordRendered(frame.timestamp, frame.renderedAtNs)
        sync.applyResponse(
            clientSentNs = 1_100_000_000,
            clientReceivedNs = 1_120_000_000,
            response = "{\"hostRecvNs\":2110000000,\"hostSendNs\":2112000000," +
                "\"rtpTimestamp\":99,\"captureNs\":2101000000}",
            frame = frame,
        )
        val estimate = requireNotNull(sync.latest())
        assertEquals(60f, estimate.first, 0.01f)
        assertEquals(9f, estimate.second, 0.01f)
    }

    @Test fun clockSyncRejectsStaleCaptureAndKeepsPreviousValidEstimate() {
        val sync = RtpClockSync()
        val frame = RenderedRtpFrame(timestamp = 99, renderedAtNs = 1_160_000_000)
        sync.applyResponse(
            clientSentNs = 1_100_000_000,
            clientReceivedNs = 1_120_000_000,
            response = "{\"hostRecvNs\":2110000000,\"hostSendNs\":2112000000," +
                "\"rtpTimestamp\":99,\"captureNs\":2101000000}",
            frame = frame,
        )
        val validEstimate = requireNotNull(sync.latest())

        sync.applyResponse(
            clientSentNs = 2_100_000_000,
            clientReceivedNs = 2_120_000_000,
            response = "{\"hostRecvNs\":3110000000,\"hostSendNs\":3112000000," +
                "\"rtpTimestamp\":99,\"captureNs\":1000000}",
            frame = RenderedRtpFrame(timestamp = 99, renderedAtNs = 2_160_000_000),
        )

        assertEquals(validEstimate, sync.latest())
    }
}
