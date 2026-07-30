import unittest
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
        self.assertIn("key-int-max=300", description)

    def test_rtp_kernel_pacing_allows_twice_the_selected_bitrate(self):
        self.assertEqual(2_000_000, Session._pacing_rate(8_000))

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
        self.assertIn("buffer-size=500000", description)

    def test_identical_start_keeps_receiver_without_forcing_idr(self):
        sink = Mock()
        sink.get_property.side_effect = lambda name: {"host": "192.0.2.1", "port": 49152}[name]
        session = Session.__new__(Session)
        session.pipeline = Mock()
        session.pipeline.get_by_name.return_value = sink
        session.force_key_unit = Mock()

        self.assertFalse(session.update_client("192.0.2.1", 49152))

        sink.set_property.assert_not_called()
        session.force_key_unit.assert_not_called()

if __name__ == "__main__":
    unittest.main()
