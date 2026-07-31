import select
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class NativeRtpSenderTest(unittest.TestCase):
    def test_preserves_order_and_spaces_a_large_rtp_burst(self):
        if shutil.which("cc") is None:
            self.skipTest("C compiler unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            sender = Path(directory) / "monitorize-rtp-sender"
            subprocess.run(
                [str(root / "linux/native/rtp_sender/build.sh"), str(sender)],
                check=True,
            )
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.bind(("127.0.0.1", 0))
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2_097_152)
            receiver.settimeout(1)
            source = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            source.bind(("127.0.0.1", 0))
            source_port = source.getsockname()[1]
            source.close()

            process = subprocess.Popen(
                [str(sender), str(source_port), "127.0.0.1",
                 str(receiver.getsockname()[1]), "60", "262144"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            producer = None
            try:
                ready, _, _ = select.select([process.stdout], [], [], 2)
                self.assertTrue(ready)
                line = process.stdout.readline().strip()
                self.assertTrue(line.startswith("READY inputPort="), line)
                input_port = int(line.split("inputPort=", 1)[1].split()[0])
                producer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                payload = bytes(1188)
                for sequence in range(384):
                    header = bytes((
                        0x80, 96, sequence >> 8, sequence & 0xff,
                        0, 0, 0, 90, 0, 0, 0, 1,
                    ))
                    producer.sendto(header + payload, ("127.0.0.1", input_port))

                sequences = []
                first_at = last_at = 0.0
                for _ in range(384):
                    packet, address = receiver.recvfrom(2048)
                    now = time.monotonic()
                    if not sequences:
                        first_at = now
                        self.assertEqual(source_port, address[1])
                    last_at = now
                    sequences.append(int.from_bytes(packet[2:4], "big"))
                self.assertEqual(list(range(384)), sequences)
                self.assertGreater(last_at - first_at, 0.010)
                self.assertLess(last_at - first_at, 0.250)
            except PermissionError as exc:
                self.skipTest(f"UDP sockets unavailable: {exc}")
            finally:
                if process.stdin:
                    try:
                        process.stdin.write("QUIT\n")
                        process.stdin.flush()
                    except BrokenPipeError:
                        pass
                process.wait(timeout=2)
                if producer:
                    producer.close()
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()
                receiver.close()
