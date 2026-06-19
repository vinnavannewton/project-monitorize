import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tls_proxy


class FakeSocket:
    def __init__(self, line):
        self.data = bytearray((line + "\n").encode())
        self.sent = b""

    def recv(self, size):
        return bytes([self.data.pop(0)]) if self.data else b""

    def sendall(self, data):
        self.sent += data


class ProxyAuthTest(unittest.TestCase):
    def test_pairing_code_is_reusable_and_tokens_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            tls_proxy.TOKEN_FILE = Path(directory) / "token"
            proxy = tls_proxy.Proxy("123456")
            pair = FakeSocket("PAIR 123456")
            self.assertTrue(proxy.authenticate(pair))
            token = pair.sent.decode().strip().split()[1]
            self.assertTrue(proxy.authenticate(FakeSocket("PAIR 123456")))
            self.assertTrue(proxy.authenticate(FakeSocket(f"AUTH {token}")))
            second = tls_proxy.Proxy("654321")
            self.assertTrue(second.authenticate(FakeSocket("PAIR 654321")))
            self.assertTrue(second.authenticate(FakeSocket(f"AUTH {token}")))

    def test_wrong_code_does_not_invalidate_session_code(self):
        with tempfile.TemporaryDirectory() as directory:
            tls_proxy.TOKEN_FILE = Path(directory) / "token"
            proxy = tls_proxy.Proxy("123456")
            with patch("tls_proxy.time.sleep"):
                self.assertFalse(proxy.authenticate(FakeSocket("PAIR 000000")))
            self.assertTrue(proxy.authenticate(FakeSocket("PAIR 123456")))

    def test_backend_connection_retries_until_stream_is_ready(self):
        expected = object()
        with (
            patch(
                "tls_proxy.socket.create_connection",
                side_effect=[ConnectionRefusedError(), ConnectionRefusedError(), expected],
            ) as connect,
            patch("tls_proxy.time.sleep"),
        ):
            self.assertIs(tls_proxy._connect_backend(7115), expected)
        self.assertEqual(connect.call_count, 3)


if __name__ == "__main__":
    unittest.main()
