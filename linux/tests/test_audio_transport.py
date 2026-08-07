import unittest

from monitorize.streaming.audio_sender import (
    parse_start,
    usb_pipeline,
    wifi_pipeline,
)


class AudioTransportTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
