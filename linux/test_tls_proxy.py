import tempfile
import unittest
from pathlib import Path

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
    def test_pairing_is_one_time_and_token_reconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            tls_proxy.TOKEN_FILE = Path(directory) / "token"
            proxy = tls_proxy.Proxy("123456")
            pair = FakeSocket("PAIR 123456")
            self.assertTrue(proxy.authenticate(pair))
            token = pair.sent.decode().strip().split()[1]
            self.assertFalse(proxy.authenticate(FakeSocket("PAIR 123456")))
            self.assertTrue(proxy.authenticate(FakeSocket(f"AUTH {token}")))
            second = tls_proxy.Proxy("654321")
            self.assertTrue(second.authenticate(FakeSocket("PAIR 654321")))
            self.assertTrue(second.authenticate(FakeSocket(f"AUTH {token}")))

    def test_new_pairing_code_does_not_invalidate_existing_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            tls_proxy.TOKEN_FILE = Path(directory) / "token"
            proxy = tls_proxy.Proxy("123456")
            pair = FakeSocket("PAIR 123456")
            self.assertTrue(proxy.authenticate(pair))
            token = pair.sent.decode().strip().split()[1]
            new_code = proxy.generate_pairing_code()
            self.assertEqual(len(new_code), 6)
            self.assertTrue(proxy.authenticate(FakeSocket(f"AUTH {token}")))
            self.assertTrue(proxy.authenticate(FakeSocket(f"PAIR {new_code}")))


if __name__ == "__main__":
    unittest.main()
