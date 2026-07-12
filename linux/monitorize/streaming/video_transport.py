"""UDP endpoint negotiation for Monitorize RTP video."""

import json
import secrets
import socket
import time

TRANSPORT = "rtp-udp-v1"
HELLO_PREFIX = b"MZRP1 "
MTU = 1200
RTP_PAYLOAD_TYPE = 96
FEC_PAYLOAD_TYPE = 122
INITIAL_FEC_PERCENT = 0


def parse_hello(data):
    if not data.startswith(HELLO_PREFIX):
        return None
    try:
        message = json.loads(data[len(HELLO_PREFIX):].decode())
        port = int(message["port"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if message.get("transport") != TRANSPORT or not 1 <= port <= 65535:
        return None
    return port, message


def wait_for_client(video_port, timeout=120, *, width=0, height=0, fps=0, bitrate=0):
    control_port = video_port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", control_port))
    sock.listen(4)
    sock.settimeout(1)
    deadline = time.monotonic() + timeout
    print(f"[RTP] Waiting for client START on TCP {control_port}", flush=True)
    try:
        while time.monotonic() < deadline:
            try:
                client, addr = sock.accept()
            except socket.timeout:
                continue
            try:
                client.settimeout(2)
                data = b""
                while b"\n" not in data and len(data) < 4096:
                    chunk = client.recv(4096 - len(data))
                    if not chunk:
                        break
                    data += chunk
                parsed = parse_hello(data.split(b"\n", 1)[0])
            except (OSError, socket.timeout):
                parsed = None
            if parsed is None:
                client.close()
                continue
            port, message = parsed
            session_id = secrets.token_hex(8)
            ssrc = secrets.randbits(32)
            profiles = message.get("decoderProfiles", [])
            profile = "high" if "high" in profiles else "constrained-baseline"
            reply = json.dumps({
                "transport": TRANSPORT, "status": "ready", "mtu": MTU,
                "rtpPt": RTP_PAYLOAD_TYPE, "fecPt": FEC_PAYLOAD_TYPE,
                "fecPercent": INITIAL_FEC_PERCENT,
                "version": 1, "sessionId": session_id, "ssrc": ssrc,
                "codec": "h264", "profile": profile,
                "width": width, "height": height, "fps": fps,
                "bitrateKbps": bitrate,
            }, separators=(",", ":")).encode()
            client.sendall(HELLO_PREFIX + reply + b"\n")
            client.close()
            print(f"[RTP] Client {addr[0]}:{port} connected", flush=True)
            return addr[0], port, ssrc, profile
    finally:
        sock.close()
    raise TimeoutError(f"No RTP client on UDP {control_port}")
