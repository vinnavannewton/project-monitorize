import unittest

from monitorize.streaming.video_transport import parse_hello


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

if __name__ == "__main__":
    unittest.main()
