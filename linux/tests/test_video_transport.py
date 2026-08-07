import unittest
from collections import deque
from unittest.mock import Mock, patch

from monitorize.streaming.video_transport import (
    is_start_message, negotiate_fec_percent, parse_hello, udp_send_buffer_bytes,
)
from monitorize.streaming.gst_session import Session
from monitorize.streaming.pipeline_builder import build_pipeline


class VideoTransportTest(unittest.TestCase):
    def test_parses_valid_client_hello(self):
        parsed = parse_hello(
            b'MZRP1 {"transport":"rtp-udp-v1","port":49152,"fps":60}'
        )
        self.assertEqual(parsed[0], 49152)
        self.assertEqual(parsed[1]["fps"], 60)

    def test_rejects_wrong_transport_and_port(self):
        self.assertIsNone(parse_hello(b'MZRP1 {"transport":"tcp","port":49152}'))
        self.assertIsNone(parse_hello(b'MZRP1 {"transport":"rtp-udp-v1","port":0}'))
        self.assertIsNone(parse_hello(b"not-monitorize"))

    def test_rejects_removed_baseline_transport(self):
        hello = b'MZRP1 {"transport":"rtp-udp-baseline-v1","port":49152}'
        self.assertIsNone(parse_hello(hello))

    def test_only_explicit_start_can_begin_negotiation(self):
        self.assertTrue(is_start_message({"type": "start"}))
        self.assertFalse(is_start_message({"type": "stats"}))
        self.assertFalse(is_start_message({"type": "idr"}))
        self.assertFalse(is_start_message({}))

    def test_stats_control_message_does_not_switch_endpoint_or_force_idr(self):
        session = Session.__new__(Session)
        session.report_client_stats = Mock()
        session.update_client = Mock()
        session.force_key_unit = Mock()

        handled = session.handle_stats_message({"type": "stats", "intervalMs": 1000})

        self.assertTrue(handled)
        session.report_client_stats.assert_called_once()
        session.update_client.assert_not_called()
        session.force_key_unit.assert_not_called()

    def test_stats_reply_echoes_clock_exchange_and_matching_capture_time(self):
        session = Session.__new__(Session)
        session.capture_rtp_times = {1234: 1_000_000}
        reply = session.stats_reply({"renderedRtpTimestamp": 1234}, 1_234_567)

        self.assertEqual(1234, reply["rtpTimestamp"])
        self.assertEqual(1_000_000, reply["captureNs"])
        self.assertEqual(1_234_567, reply["hostRecvNs"])
        self.assertIsInstance(reply["hostSendNs"], int)

    def test_stats_reply_rejects_stale_capture_time(self):
        session = Session.__new__(Session)
        session.capture_rtp_times = {1234: 1}

        reply = session.stats_reply({"renderedRtpTimestamp": 1234}, 2_000_000_000)

        self.assertIsNone(reply["captureNs"])

    def test_stats_reply_derives_capture_from_nearest_rtp_timestamp(self):
        session = Session.__new__(Session)
        session.capture_rtp_times = {90_000: 1_000_000_000}

        reply = session.stats_reply(
            {"renderedRtpTimestamp": 94_500}, 1_100_000_000,
        )

        self.assertEqual(1_050_000_000, reply["captureNs"])

    def test_rtp_timestamp_delta_handles_wrap(self):
        self.assertEqual(32, Session.rtp_timestamp_delta(16, 0xfffffff0))
        self.assertEqual(-32, Session.rtp_timestamp_delta(0xfffffff0, 16))

    def test_stats_reply_falls_back_to_recent_encoder_capture(self):
        session = Session.__new__(Session)
        session.capture_rtp_times = {}
        session.latest_encoder_capture_time = 100_000_000
        session.target_fps = 50

        reply = session.stats_reply(
            {"renderedRtpTimestamp": 1234}, 150_000_000,
        )

        self.assertEqual(80_000_000, reply["captureNs"])

    def test_paced_frame_timestamp_replaces_pre_rate_timestamp(self):
        session = Session.__new__(Session)
        session.capture_pts = {42: 1}
        session.record_capture_pts(42, 2)

        self.assertEqual(2, session.capture_pts[42])

    def test_rtp_timestamp_uses_ordered_encoder_capture_when_payloader_has_no_pts(self):
        session = Session.__new__(Session)
        session.capture_pts = {}
        session.latest_encoder_capture_time = 100
        session.capture_rtp_times = {}
        session.encoded_capture_times = deque()

        session.record_encoded_capture(None)
        session.record_rtp_capture(99, None)

        self.assertEqual(100, session.capture_rtp_times[99])

    def test_equal_rtp_timestamps_consume_each_access_unit_capture(self):
        session = Session.__new__(Session)
        session.capture_pts = {42: 1}
        session.capture_rtp_times = {}
        session.encoded_capture_times = deque([100, 200])

        session.record_rtp_capture(99, 42)
        session.record_rtp_capture(99, 42)

        self.assertEqual(200, session.capture_rtp_times[99])
        self.assertEqual([], list(session.encoded_capture_times))

    def test_encoder_input_keeps_source_capture_time_when_pts_are_unavailable(self):
        session = Session.__new__(Session)
        frame = object()
        session.capture_buffer_times = {}
        session.latest_encoder_capture_time = None

        session.record_capture_buffer(frame, 100)
        session.record_encoder_input_capture(frame)

        self.assertEqual(100, session.latest_encoder_capture_time)

    def test_encoder_input_recovers_source_time_after_buffer_conversion(self):
        session = Session.__new__(Session)
        frame = Mock(pts=42)
        session.capture_buffer_times = {}
        session.capture_pts = {42: 100}
        session.latest_encoder_capture_time = None

        session.record_encoder_input_capture(frame)

        self.assertEqual(100, session.capture_pts[42])
        self.assertEqual(100, session.latest_encoder_capture_time)

    def test_encoded_pts_skips_an_encoder_input_that_was_not_output(self):
        session = Session.__new__(Session)
        session.capture_pts = {41: 100, 42: 200}
        session.latest_encoder_capture_time = 300
        session.encoded_capture_times = deque()

        captured_at = session.record_encoded_capture(42)

        self.assertEqual(200, captured_at)
        self.assertEqual([200], list(session.encoded_capture_times))

    def test_encoded_frame_without_matching_pts_uses_latest_encoder_input(self):
        session = Session.__new__(Session)
        session.capture_pts = {41: 100}
        session.latest_encoder_capture_time = 200
        session.encoded_capture_times = deque()

        captured_at = session.record_encoded_capture(42)

        self.assertEqual(200, captured_at)

    def test_idr_control_message_forces_key_without_switching_endpoint(self):
        session = Session.__new__(Session)
        session.force_key_unit = Mock()
        session.update_client = Mock()

        with patch(
            "monitorize.streaming.gst_session.GLib.idle_add",
            side_effect=lambda function: function(),
        ):
            handled = session.handle_idr_message({"type": "idr"})

        self.assertTrue(handled)
        session.force_key_unit.assert_called_once()
        session.update_client.assert_not_called()

    def test_pending_recovery_idr_coalesces_duplicate_request(self):
        session = Session.__new__(Session)
        session.pending_idr_since = 1.0
        session.coalesced_idr_count = 0
        session.force_key_count = 1
        session.pipeline = Mock()

        self.assertFalse(session.force_key_unit())

        self.assertEqual(1, session.coalesced_idr_count)
        self.assertEqual(1, session.force_key_count)
        session.pipeline.get_by_name.assert_not_called()

    def test_recent_completed_recovery_idr_rate_limits_new_request(self):
        session = Session.__new__(Session)
        session.pending_idr_since = None
        session.last_forced_idr_at = 1.0
        session.coalesced_idr_count = 0
        session.force_key_count = 1
        session.pipeline = Mock()

        with patch("monitorize.streaming.gst_session.time.monotonic", return_value=1.5):
            self.assertFalse(session.force_key_unit())

        self.assertEqual(1, session.coalesced_idr_count)
        self.assertEqual(1, session.force_key_count)
        session.pipeline.get_by_name.assert_not_called()

    def test_host_retry_can_replace_pending_recovery_idr(self):
        session = Session.__new__(Session)
        session.pending_idr_since = 1.0
        session.coalesced_idr_count = 0
        session.force_key_count = 1
        session.last_forced_idr_at = 1.0
        pad = Mock()
        pad.send_event.return_value = True
        encoder = Mock()
        encoder.get_static_pad.return_value = pad
        session.pipeline = Mock()
        session.pipeline.get_by_name.return_value = encoder

        with (
            patch("monitorize.streaming.gst_session.time.monotonic", return_value=2.0),
            patch(
                "monitorize.streaming.gst_session.GstVideo."
                "video_event_new_upstream_force_key_unit",
                return_value=object(),
            ),
        ):
            self.assertFalse(session.force_key_unit(replace_pending=True))

        self.assertEqual(2, session.force_key_count)
        self.assertEqual(0, session.coalesced_idr_count)
        self.assertEqual(2.0, session.pending_idr_since)
        self.assertEqual(2.0, session.last_forced_idr_at)
        pad.send_event.assert_called_once()

    def test_encoded_idr_is_classified_as_scheduled_or_recovery(self):
        session = Session.__new__(Session)
        session.pending_idr_since = None
        session.scheduled_idr_count = 0
        session.confirmed_idr_count = 0
        session.last_idr_ms = None

        session.record_encoded_idr(1024, now=10.0)
        self.assertEqual(1, session.scheduled_idr_count)
        self.assertEqual(1.0, session.last_idr_kib)

        session.pending_idr_since = 10.0
        session.record_encoded_idr(2048, now=10.025)
        self.assertEqual(1, session.scheduled_idr_count)
        self.assertEqual(1, session.confirmed_idr_count)
        self.assertAlmostEqual(25.0, session.last_idr_ms)
        self.assertEqual(2.0, session.last_idr_kib)

    def test_fec_requires_both_request_and_receiver_capability(self):
        capable = {"fecModes": ["ulp-rfc5109"]}
        self.assertEqual(negotiate_fec_percent(capable, 10), 10)
        self.assertEqual(negotiate_fec_percent(capable, 0), 0)
        self.assertEqual(negotiate_fec_percent({}, 10), 0)

    def test_rtp_pipeline_uses_the_selected_fixed_bitrate_without_activity_branch(self):
        pipeline = build_pipeline(
            pw_fd=1, node_id=1, width=1920, height=1200, fps=60,
            bitrate=8_000, port=7110, hw_encoder=None, wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152),
        )
        description = " ".join(pipeline)

        self.assertIn("bitrate=8000", description)
        self.assertNotIn("monitorize_activity", description)
        self.assertNotIn("appsink", description)
        self.assertIn("key-int-max=1800", description)
        self.assertIn("h264parse name=monitorize_parser", description)

    def test_rtp_sender_pacing_tracks_selected_bitrate_with_headroom(self):
        from monitorize.streaming.gst_session import (
            congestion_bitrate_kbps,
            sender_pacing_kbps,
        )
        self.assertEqual(10_000, sender_pacing_kbps(8_000))
        self.assertEqual(28_750, sender_pacing_kbps(23_000))
        self.assertEqual(23_000, congestion_bitrate_kbps(23_000, 4.9))
        self.assertEqual(17_250, congestion_bitrate_kbps(23_000, 5.0))
        self.assertEqual(4_000, congestion_bitrate_kbps(4_000, 50.0))

    def test_congestion_reduces_encoder_and_sender_together(self):
        session = Session.__new__(Session)
        session.current_bitrate = 23_000
        session.pacing_bytes_per_second = 0
        encoder = Mock()
        session._element = Mock(return_value=encoder)
        session.sender_command = Mock()
        session.force_key_unit = Mock()

        self.assertFalse(session.reduce_bitrate_for_congestion(12.0))

        encoder.set_property.assert_called_once_with("bitrate", 17_250)
        session.sender_command.assert_called_once_with("RATE 21562")
        session.force_key_unit.assert_called_once_with(replace_pending=True)
        self.assertEqual(17_250, session.current_bitrate)

    def test_udp_send_buffer_scales_to_two_tenths_of_a_second(self):
        self.assertEqual(262_144, udp_send_buffer_bytes(8_000))
        self.assertEqual(275_000, udp_send_buffer_bytes(11_000))
        self.assertEqual(525_000, udp_send_buffer_bytes(21_000))
        self.assertEqual(2_097_152, udp_send_buffer_bytes(100_000))

    def test_fixed_ten_percent_fec_reserves_video_bitrate(self):
        pipeline = build_pipeline(
            pw_fd=1, node_id=1, width=1920, height=1200, fps=60,
            bitrate=20_000, port=7110, hw_encoder=None, wifi_mode=True,
            rtp_endpoint=(
                "192.0.2.1", 49152, 1234, "constrained-baseline", 10
            ),
        )
        description = " ".join(pipeline)
        self.assertIn("bitrate=18000", description)
        self.assertIn(
            "rtpulpfecenc pt=122 percentage=10 multipacket=true", description
        )
        self.assertIn("udpsink host=192.0.2.1", description)
        self.assertIn("qos-dscp=40", description)
        self.assertIn("buffer-size=500000", description)

    def test_identical_start_keeps_receiver_without_forcing_idr(self):
        session = Session.__new__(Session)
        session.client_host = "192.0.2.1"
        session.client_port = 49152
        session.sender_command = Mock()
        session.force_key_unit = Mock()

        self.assertFalse(session.update_client("192.0.2.1", 49152))

        session.sender_command.assert_not_called()
        session.force_key_unit.assert_not_called()

    def test_endpoint_change_updates_native_sender_and_forces_idr(self):
        session = Session.__new__(Session)
        session.client_host = "192.0.2.1"
        session.client_port = 49152
        session.sender_command = Mock()
        session.force_key_unit = Mock()

        self.assertFalse(session.update_client("192.0.2.2", 49153))

        session.sender_command.assert_called_once_with("DEST 192.0.2.2 49153")
        session.force_key_unit.assert_called_once()
        self.assertEqual(("192.0.2.2", 49153), (session.client_host, session.client_port))

    def test_encoded_access_unit_detects_annex_b_idr(self):
        self.assertTrue(Session.encoded_access_unit_has_idr(b"\x00\x00\x00\x01\x65x"))
        self.assertFalse(Session.encoded_access_unit_has_idr(b"\x00\x00\x01\x41x"))

if __name__ == "__main__":
    unittest.main()
