import unittest
from unittest.mock import Mock, patch

from monitorize.streaming.audio_receiver import (
    INITIAL_ATTEMPTS,
    LinuxAudioReceiver,
    attach_udp_socket,
    audio_pipeline,
    build_start,
    parse_ready,
)
from monitorize.streaming.audio_sender import (
    parse_start,
    usb_pipeline,
    wifi_pipeline,
)


class AudioTransportTest(unittest.TestCase):
    @staticmethod
    def ready(**changes):
        values = {
            "status": "ready", "version": 1,
            "transport": "rtp-opus-udp-v1", "codec": "OPUS",
            "sampleRate": 48000, "channels": 1, "packetMs": 10,
            "rtpPt": 97, "bitrate": 96000,
        }
        values.update(changes)
        import json
        return b"MZA1 " + json.dumps(values).encode("utf-8")

    def test_parses_only_valid_wifi_start(self):
        self.assertEqual(
            parse_start(
                b'MZA1 {"transport":"rtp-opus-udp-v1","type":"start","port":49152}'
            ),
            49152,
        )
        self.assertIsNone(parse_start(b'MZA1 {"transport":"tcp","type":"start","port":49152}'))
        self.assertIsNone(parse_start(b'MZA1 {"transport":"rtp-opus-udp-v1","type":"start","port":0}'))
        self.assertIsNone(parse_start(b"not-monitorize"))

    def test_wifi_pipeline_is_bounded_mono_rtp_opus(self):
        text = " ".join(wifi_pipeline("10.0.0.2", 49152, 7120))
        self.assertIn("device=@DEFAULT_MONITOR@", text)
        self.assertIn("buffer-time=10000 latency-time=5000", text)
        self.assertIn("max-size-buffers=2", text)
        self.assertIn("mix-matrix=<<(float)0.5,(float)0.5>>", text)
        self.assertIn("volume volume=0.70710678", text)
        self.assertIn("format=S16LE", text)
        self.assertIn("rate=48000,channels=1", text)
        self.assertIn("output-buffer-duration=1/100", text)
        self.assertIn(
            "opusenc bitrate=96000 bitrate-type=constrained-vbr frame-size=10 "
            "audio-type=generic perfect-timestamp=true",
            text,
        )
        self.assertIn("rtpopuspay pt=97", text)
        self.assertIn("host=10.0.0.2 port=49152 bind-port=7120", text)
        self.assertIn("qos-dscp=48", text)

    def test_usb_pipeline_is_bounded_little_endian_tcp(self):
        text = " ".join(usb_pipeline(7120))
        self.assertIn("format=S16LE", text)
        self.assertIn("tcpserversink host=127.0.0.1 port=7120", text)
        self.assertIn("sync=false", text)
        self.assertIn("buffers-soft-max=6 buffers-max=20 recover-policy=latest", text)

    def test_linux_receiver_builds_and_validates_audio_negotiation(self):
        start = build_start(49152)
        self.assertIn(b'"transport":"rtp-opus-udp-v1"', start)
        self.assertIn(b'"type":"start"', start)
        self.assertIn(b'"port":49152', start)
        self.assertEqual(parse_ready(self.ready())["bitrate"], 96000)
        self.assertIsNone(parse_ready(b"not-monitorize"))
        for field, value in (
            ("status", "error"), ("version", 2), ("transport", "tcp"),
            ("codec", "PCM"), ("sampleRate", 44100), ("channels", 2),
            ("packetMs", 20), ("rtpPt", 96),
        ):
            self.assertIsNone(parse_ready(self.ready(**{field: value})))

    def test_linux_receiver_pipeline_is_bounded_mono_opus(self):
        text = " ".join(audio_pipeline("pulsesink"))
        self.assertIn("encoding-name=OPUS,payload=97,clock-rate=48000", text)
        self.assertIn(
            "rtpjitterbuffer name=audio_jitter latency=80 "
            "drop-on-latency=true do-lost=true",
            text,
        )
        self.assertIn("opusdec name=audio_decoder plc=true", text)
        self.assertIn("rate=48000,channels=1", text)
        self.assertIn(
            "pulsesink name=audio_sink sync=true async=false "
            "client-name=Monitorize buffer-time=40000 latency-time=10000",
            text,
        )
        fallback = " ".join(audio_pipeline("autoaudiosink"))
        self.assertIn("autoaudiosink name=audio_sink sync=true", fallback)
        self.assertNotIn("buffer-time=", fallback)

    def test_linux_receiver_hands_the_still_bound_socket_to_udpsrc(self):
        source = Mock()
        pipeline = Mock()
        pipeline.get_by_name.return_value = source
        raw_socket = Mock()
        raw_socket.fileno.return_value = 42
        gio = Mock()
        gst_socket = gio.Socket.new_from_fd.return_value
        with patch("monitorize.streaming.audio_receiver.os.dup", return_value=84) as duplicate:
            self.assertIs(attach_udp_socket(pipeline, raw_socket, gio), gst_socket)
        duplicate.assert_called_once_with(42)
        gio.Socket.new_from_fd.assert_called_once_with(84)
        source.set_property.assert_any_call("socket", gst_socket)
        source.set_property.assert_any_call("close-socket", True)

    def test_linux_receiver_stops_after_five_initial_failures(self):
        logs = []
        receiver = LinuxAudioReceiver(logs.append)
        receiver._receive_once = Mock(side_effect=RuntimeError("disabled"))
        stop_event = Mock()
        stop_event.is_set.return_value = False
        stop_event.wait.return_value = False
        receiver._run("10.0.0.2", stop_event)
        self.assertEqual(receiver._receive_once.call_count, INITIAL_ATTEMPTS)
        self.assertEqual(len(logs), 1)
        self.assertIn("Audio unavailable", logs[0])

    def test_linux_receiver_retries_interruptions_after_success(self):
        logs = []
        receiver = LinuxAudioReceiver(logs.append)
        calls = 0

        def receive(_host, _stop_event, mark_connected):
            nonlocal calls
            calls += 1
            if calls == 1:
                mark_connected()
                return
            raise RuntimeError("lost")

        receiver._receive_once = Mock(side_effect=receive)
        stop_event = Mock()
        stop_event.is_set.return_value = False
        stop_event.wait.side_effect = [False, False, True]
        receiver._run("10.0.0.2", stop_event)
        self.assertEqual(receiver._receive_once.call_count, 3)
        self.assertEqual(sum("retrying" in line for line in logs), 2)

    def test_linux_receiver_stop_signals_and_joins_worker(self):
        receiver = LinuxAudioReceiver()
        worker = Mock()
        stop_event = Mock()
        receiver._thread = worker
        receiver._stop_event = stop_event
        receiver._host = "10.0.0.2"
        receiver.stop()
        stop_event.set.assert_called_once_with()
        worker.join.assert_called_once_with(timeout=1.5)
        self.assertIsNone(receiver._thread)

    def test_linux_receiver_falls_back_when_pulse_runtime_rejects_playback(self):
        logs = []
        receiver = LinuxAudioReceiver(logs.append)
        raw_socket = Mock()
        raw_socket.getsockname.return_value = ("0.0.0.0", 49152)
        pulse_pipeline = Mock()
        system_pipeline = Mock()
        gst = Mock()
        gst.StateChangeReturn.FAILURE = -1
        gst.State.PLAYING = 3
        gst.MessageType.ERROR = 1
        gst.MessageType.EOS = 2
        gst.MessageType.STATE_CHANGED = 4
        gst.MSECOND = 1
        pulse_pipeline.set_state.return_value = -1
        system_pipeline.set_state.return_value = 0
        playing = Mock(type=4, src=system_pipeline)
        playing.parse_state_changed.return_value = (2, 3, 0)
        system_pipeline.get_bus.return_value.timed_pop_filtered.return_value = playing
        gst.parse_launch.side_effect = [pulse_pipeline, system_pipeline]
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, False, True]
        mark_connected = Mock()

        with (
            patch("monitorize.streaming.audio_receiver._load_gst", return_value=(gst, Mock())),
            patch("monitorize.streaming.audio_receiver.gst_has_element", return_value=True),
            patch("monitorize.streaming.audio_receiver.socket.socket", return_value=raw_socket),
            patch("monitorize.streaming.audio_receiver._negotiate"),
            patch("monitorize.streaming.audio_receiver.attach_udp_socket"),
        ):
            receiver._receive_once("10.0.0.2", stop_event, mark_connected)

        self.assertEqual(gst.parse_launch.call_count, 2)
        self.assertIn("pulsesink", gst.parse_launch.call_args_list[0].args[0])
        self.assertIn("autoaudiosink", gst.parse_launch.call_args_list[1].args[0])
        mark_connected.assert_called_once_with()
        self.assertTrue(any("trying system audio" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
