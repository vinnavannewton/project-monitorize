import hashlib
import sys
import unittest
from unittest.mock import Mock, patch

from monitorize.security import tls_receiver


class FakeRemote:
    def __init__(self, response=b""):
        self.data = bytearray(response)
        self.sent = b""
        self.closed = False

    def recv(self, _size):
        return bytes([self.data.pop(0)]) if self.data else b""

    def sendall(self, data):
        self.sent += data

    def getpeercert(self, binary_form=False):
        return b"certificate"

    def setsockopt(self, *_args):
        pass

    def close(self):
        self.closed = True


class TlsReceiverTest(unittest.TestCase):
    def test_client_context_allows_tls12_minimum(self):
        context = tls_receiver.create_client_context()
        self.assertEqual(context.minimum_version, tls_receiver.ssl.TLSVersion.TLSv1_2)

    def test_invalid_local_port_is_rejected(self):
        with (
            patch.object(sys, "argv", ["monitorize.security.tls_receiver.py", "host", "7110", "--local-port", "70000"]),
            self.assertRaises(SystemExit),
        ):
            tls_receiver.main()

    def test_fingerprint_mismatch_closes_remote(self):
        remote = FakeRemote()
        context = Mock()
        context.wrap_socket.return_value = remote
        wrong = "0" * 64
        self.assertNotEqual(
            wrong,
            hashlib.sha256(remote.getpeercert(binary_form=True)).hexdigest().upper(),
        )
        with (
            patch.object(sys, "argv", ["monitorize.security.tls_receiver.py", "host", "7110", "--fingerprint", wrong]),
            patch("monitorize.security.tls_receiver.create_client_context", return_value=context),
            patch("monitorize.security.tls_receiver.socket.create_connection", return_value=object()),
        ):
            self.assertEqual(tls_receiver.main(), 2)
        self.assertTrue(remote.closed)

    def test_auth_failure_closes_remote(self):
        remote = FakeRemote(b"ERR\n")
        context = Mock()
        context.wrap_socket.return_value = remote
        token = "a" * 64
        with (
            patch.object(sys, "argv", ["monitorize.security.tls_receiver.py", "host", "7110", "--token", token]),
            patch("monitorize.security.tls_receiver.create_client_context", return_value=context),
            patch("monitorize.security.tls_receiver.socket.create_connection", return_value=object()),
        ):
            self.assertEqual(tls_receiver.main(), 2)
        self.assertIn(f"AUTH {token}\n".encode("ascii"), remote.sent)
        self.assertTrue(remote.closed)

    def test_listener_bind_failure_closes_remote_and_listener(self):
        token = "b" * 64
        remote = FakeRemote(f"OK {token}\n".encode("ascii"))
        context = Mock()
        context.wrap_socket.return_value = remote

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
            patch.object(sys, "argv", ["monitorize.security.tls_receiver.py", "host", "7110", "--token", token]),
            patch("monitorize.security.tls_receiver.create_client_context", return_value=context),
            patch("monitorize.security.tls_receiver.socket.create_connection", return_value=object()),
            patch("monitorize.security.tls_receiver.socket.socket", return_value=listener),
            self.assertRaises(OSError),
        ):
            tls_receiver.main()
        self.assertTrue(remote.closed)
        self.assertTrue(listener.closed)


if __name__ == "__main__":
    unittest.main()
