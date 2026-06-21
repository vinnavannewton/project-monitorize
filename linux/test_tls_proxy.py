import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import tls_proxy


class FakeSocket:
    def __init__(self, line):
        self.data = bytearray((line + "\n").encode())
        self.sent = b""
        self.closed = False

    def recv(self, size):
        return bytes([self.data.pop(0)]) if self.data else b""

    def sendall(self, data):
        self.sent += data

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


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

    def test_tokens_are_filtered_and_lowercased(self):
        good = "A" * 64
        old_token = tls_proxy.TOKEN_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                tls_proxy.TOKEN_FILE = Path(directory) / "token"
                tls_proxy.TOKEN_FILE.write_text(f"bad\n {good} \n{'g' * 64}\n")
                self.assertEqual(tls_proxy._load_tokens(), {good.lower()})
            finally:
                tls_proxy.TOKEN_FILE = old_token

    def test_tokens_are_saved_atomically_with_private_permissions(self):
        good = "B" * 64
        old_config, old_token = tls_proxy.CONFIG_DIR, tls_proxy.TOKEN_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                tls_proxy.CONFIG_DIR = Path(directory)
                tls_proxy.TOKEN_FILE = Path(directory) / "token"
                tls_proxy._save_tokens({good, "bad"})
                self.assertEqual(tls_proxy.TOKEN_FILE.read_text(), good.lower())
                self.assertEqual(os.stat(tls_proxy.TOKEN_FILE).st_mode & 0o777, 0o600)
                self.assertFalse((tls_proxy.TOKEN_FILE.parent / "token.tmp").exists())
            finally:
                tls_proxy.CONFIG_DIR, tls_proxy.TOKEN_FILE = old_config, old_token

    def test_server_context_allows_tls12_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "cert.pem"
            key = Path(directory) / "key.pem"
            with patch("ssl.SSLContext.load_cert_chain"):
                context = tls_proxy.create_server_context(cert, key)
        self.assertEqual(context.minimum_version, tls_proxy.ssl.TLSVersion.TLSv1_2)

    def test_invalid_port_arg_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            tls_proxy._port_arg("70000")

    def test_listener_bind_failure_is_reported_and_closed(self):
        class FakeListener:
            def __init__(self):
                self.closed = False

            def setsockopt(self, *_args):
                pass

            def bind(self, _addr):
                raise OSError("busy")

            def close(self):
                self.closed = True

        listener = FakeListener()
        with (
            patch("tls_proxy.socket.socket", return_value=listener),
            patch("builtins.print") as print_mock,
        ):
            tls_proxy.Proxy("123456").serve(Mock(), 7110, 7112)
        self.assertTrue(listener.closed)
        self.assertIn("[TLS] ERROR listen 7110: busy", print_mock.call_args.args[0])

    def test_auth_failure_closes_client_socket(self):
        client = FakeSocket("PAIR 000000")
        with patch("tls_proxy.time.sleep"):
            tls_proxy.Proxy("123456").handle(client, 7111)
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
