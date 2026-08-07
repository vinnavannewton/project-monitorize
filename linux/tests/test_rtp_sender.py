import select
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class NativeRtpSenderTest(unittest.TestCase):
    def test_preserves_order_and_byte_paces_a_large_rtp_burst(self):
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
            receiver.setsockopt(socket.IPPROTO_IP, socket.IP_RECVTOS, 1)
            receiver.settimeout(1)
            source = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            source.bind(("127.0.0.1", 0))
            source_port = source.getsockname()[1]
            source.close()

            process = subprocess.Popen(
                [str(sender), str(source_port), "127.0.0.1",
                 str(receiver.getsockname()[1]), "60", "262144", "46000"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            producer = None
            try:
                ready, _, _ = select.select([process.stdout], [], [], 2)
                self.assertTrue(ready)
                line = process.stdout.readline().strip()
                self.assertTrue(line.startswith("READY inputPort="), line)
                self.assertIn("pacingKbps=46000", line)
                input_port = int(line.split("inputPort=", 1)[1].split()[0])
                process.stdin.write("FLUSH\n")
                process.stdin.flush()
                ready, _, _ = select.select([process.stdout], [], [], 1)
                self.assertTrue(ready)
                self.assertEqual("FLUSH", process.stdout.readline().strip())
                process.stdin.write("RATE 46000\n")
                process.stdin.flush()
                ready, _, _ = select.select([process.stdout], [], [], 1)
                self.assertTrue(ready)
                self.assertEqual(
                    "RATE pacingKbps=46000", process.stdout.readline().strip()
                )
                producer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                frames = [(90_000, 384), (91_500, 20), (91_500, 20)]
                packets = [
                    (timestamp, offset == 0, offset == count - 1)
                    for timestamp, count in frames
                    for offset in range(count)
                ]
                for sequence, (timestamp, first, last) in enumerate(packets):
                    nal = 0x09 if first else (0x65 if timestamp == 90_000 else 0x41)
                    payload = bytes((nal,)) + bytes(1187)
                    header = bytes((
                        0x80, 96 | (0x80 if last else 0),
                        sequence >> 8, sequence & 0xff,
                        timestamp >> 24, (timestamp >> 16) & 0xff,
                        (timestamp >> 8) & 0xff, timestamp & 0xff,
                        0, 0, 0, 1,
                    ))
                    producer.sendto(header + payload, ("127.0.0.1", input_port))

                sequences = []
                received_tos = None
                first_at = last_at = 0.0
                for _ in packets:
                    packet, ancillary, _, address = receiver.recvmsg(
                        2048, socket.CMSG_SPACE(1)
                    )
                    now = time.monotonic()
                    if not sequences:
                        first_at = now
                        self.assertEqual(source_port, address[1])
                    for level, kind, data in ancillary:
                        if level == socket.IPPROTO_IP and kind == socket.IP_TOS:
                            received_tos = data[0]
                    last_at = now
                    sequences.append(int.from_bytes(packet[2:4], "big"))
                self.assertEqual(list(range(len(packets))), sequences)
                self.assertEqual(40, received_tos >> 2)
                self.assertGreater(last_at - first_at, 0.060)
                self.assertLess(last_at - first_at, 0.160)
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
