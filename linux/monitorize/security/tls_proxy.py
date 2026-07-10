
"""Small TLS front-end for Monitorize Wi-Fi video and input."""

import argparse
import hashlib
import os
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

from monitorize.input_bridge.protocol import parse_udp_packets
from monitorize.input_bridge.transport import (
    UDP_DRAIN_CAP, UDP_RCVBUF, append_udp_batch, coalesce_motion_packets,
)
from monitorize.security.secure_udp import SecureUdpError, decrypt_packet

CONFIG_DIR = Path.home() / ".config" / "monitorize"
CERT_FILE = CONFIG_DIR / "tls-cert.pem"
KEY_FILE = CONFIG_DIR / "tls-key.pem"
TOKEN_FILE = CONFIG_DIR / "tls-client-token"
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_port(value: int) -> bool:
    return 1 <= int(value) <= 65535


def _port_arg(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not _valid_port(port):
        raise argparse.ArgumentTypeError("port must be in 1..65535")
    return port


def _secure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def ensure_identity() -> tuple[Path, Path]:
    _secure_config_dir()
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:P-256",
            "-sha256", "-days", "3650", "-nodes",
            "-subj", "/CN=Monitorize",
            "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.chmod(KEY_FILE, 0o600)
    return CERT_FILE, KEY_FILE


def certificate_fingerprint() -> str:
    cert, _ = ensure_identity()
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return hashlib.sha256(der).hexdigest().upper()


def _load_tokens() -> set[str]:
    if TOKEN_FILE.exists():
        return {
            stripped.lower() for token in TOKEN_FILE.read_text().splitlines()
            if TOKEN_RE.fullmatch(stripped := token.strip())
        }
    return set()


def _save_tokens(tokens: set[str]) -> None:
    _secure_config_dir()
    clean = sorted({
        stripped.lower() for token in tokens
        if TOKEN_RE.fullmatch(stripped := str(token).strip())
    })
    tmp = TOKEN_FILE.with_name(f"{TOKEN_FILE.name}.tmp")
    tmp.write_text("\n".join(clean))
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)


def create_server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


def _read_line(sock: ssl.SSLSocket, limit: int = 128) -> str:
    data = bytearray()
    while len(data) < limit:
        byte = sock.recv(1)
        if not byte:
            break
        if byte == b"\n":
            return data.decode("ascii", errors="strict")
        data.extend(byte)
    raise ValueError("invalid authentication line")


def _pipe(source: socket.socket, destination: socket.socket) -> None:
    try:
        while data := source.recv(128 * 1024):
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _connect_backend(port: int, timeout: float = 10) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=1)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


class Proxy:
    def __init__(self, pairing_code: str, debug: bool = False):
        self.pairing_code = pairing_code
        self.tokens = _load_tokens()
        self.lock = threading.Lock()
        self.udp_replay_state = {}
        self.debug = debug
        self.udp_received = 0
        self.udp_rejected = 0
        self.udp_replayed = 0
        self.udp_forwarded = 0
        self.udp_coalesced = 0

    def authenticate(self, client: ssl.SSLSocket) -> bool:
        line = _read_line(client)
        command, _, value = line.partition(" ")
        parts = value.strip().split(" ", 1)
        token_or_code = parts[0]
        model_name = parts[1] if len(parts) > 1 else "Android Device"
        failed_pairing = False
        client_ip = "unknown"
        try:
            client_ip = client.getpeername()[0]
        except Exception:
            pass
        with self.lock:
            if command == "AUTH" and any(
                secrets.compare_digest(token_or_code.lower(), token) for token in self.tokens
            ):
                client.sendall(b"OK\n")
                print(f"[TLS] Client authenticated. IP: {client_ip} Name: {model_name}", flush=True)
                return True
            if self.pairing_code and command == "PAIR" and secrets.compare_digest(token_or_code, self.pairing_code):
                token = secrets.token_hex(32)
                self.tokens.add(token)
                _save_tokens(self.tokens)
                client.sendall(f"OK {token}\n".encode("ascii"))
                print(f"[TLS] Pairing accepted. IP: {client_ip} Name: {model_name}", flush=True)
                return True
            if command == "PAIR":
                failed_pairing = True
        if failed_pairing:
            time.sleep(1)
        client.sendall(b"ERR\n")
        return False

    def handle(self, client: ssl.SSLSocket, backend_port: int) -> None:
        backend = None
        try:
            client.settimeout(35)
            if not self.authenticate(client):
                return
            client.settimeout(None)
            backend = _connect_backend(backend_port)
            backend.settimeout(None)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            backend.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if backend_port == 7111:
                client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                backend.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            threading.Thread(target=_pipe, args=(client, backend), daemon=True).start()
            _pipe(backend, client)
        except (OSError, ValueError, ssl.SSLError) as exc:
            print(f"[TLS] Connection rejected: {exc}", flush=True)
        finally:
            for sock in (backend, client):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def serve(self, context: ssl.SSLContext, public_port: int, backend_port: int) -> None:
        listener = None
        try:
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", public_port))
            listener.listen(4)
            print(f"[TLS] 0.0.0.0:{public_port} -> 127.0.0.1:{backend_port}", flush=True)
            while True:
                raw, _ = listener.accept()
                try:
                    client = context.wrap_socket(raw, server_side=True)
                except (OSError, ssl.SSLError):
                    raw.close()
                    continue
                threading.Thread(
                    target=self.handle, args=(client, backend_port), daemon=True
                ).start()
        except OSError as exc:
            print(f"[TLS] ERROR listen {public_port}: {exc}", flush=True)
        finally:
            if listener:
                try:
                    listener.close()
                except OSError:
                    pass

    @staticmethod
    def _frame_packet(pkt_type: int, payload: bytes) -> bytes:
        return len(payload).to_bytes(4, "big") + bytes([pkt_type]) + payload

    def _debug_udp_stats(self) -> None:
        if self.debug:
            print(
                "[TLS UDP] stats "
                f"received={self.udp_received} rejected={self.udp_rejected} "
                f"replayed={self.udp_replayed} coalesced={self.udp_coalesced} "
                f"forwarded={self.udp_forwarded}",
                flush=True,
            )

    def _decrypt_udp_packet(self, data: bytes, addr, fingerprint: str) -> bytes | None:
        with self.lock:
            tokens = set(self.tokens)
        try:
            self.udp_received += 1
            return decrypt_packet(
                data, tokens, fingerprint, self.udp_replay_state, addr
            )
        except SecureUdpError as exc:
            self.udp_rejected += 1
            if "replay" in str(exc).lower():
                self.udp_replayed += 1
            return None

    def _forward_udp_packets(self, packets, backend: socket.socket, backend_addr) -> int:
        before = len(packets)
        coalesced = coalesce_motion_packets(packets)
        self.udp_coalesced += before - len(coalesced)
        sent = 0
        for pkt_type, payload in coalesced:
            backend.sendto(self._frame_packet(pkt_type, payload), backend_addr)
            sent += 1
        self.udp_forwarded += sent
        return sent

    def handle_udp_datagrams(
        self, datagrams, backend: socket.socket, backend_addr, fingerprint: str
    ) -> int:
        batches = []
        for packet, packet_addr in datagrams:
            payload = self._decrypt_udp_packet(packet, packet_addr, fingerprint)
            if payload:
                append_udp_batch(batches, packet_addr, parse_udp_packets(payload))
        sent = 0
        for _addr, packets in batches:
            sent += self._forward_udp_packets(packets, backend, backend_addr)
        return sent

    def handle_udp_packet(
        self, data: bytes, addr, backend: socket.socket, backend_addr, fingerprint: str
    ) -> bool:
        try:
            return self.handle_udp_datagrams(
                [(data, addr)], backend, backend_addr, fingerprint
            ) > 0
        except OSError:
            return False

    def serve_udp(self, public_port: int, backend_port: int, fingerprint: str) -> None:
        listener = None
        backend = None
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RCVBUF)
            listener.bind(("0.0.0.0", public_port))
            backend = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            backend_addr = ("127.0.0.1", backend_port)
            print(
                f"[TLS UDP] 0.0.0.0:{public_port} -> 127.0.0.1:{backend_port}",
                flush=True,
            )
            while True:
                data, addr = listener.recvfrom(2048)
                datagrams = [(data, addr)]
                listener.setblocking(False)
                try:
                    for _ in range(UDP_DRAIN_CAP):
                        datagrams.append(listener.recvfrom(2048))
                except BlockingIOError:
                    pass
                finally:
                    listener.setblocking(True)
                try:
                    self.handle_udp_datagrams(
                        datagrams, backend, backend_addr, fingerprint
                    )
                except OSError:
                    pass
                self._debug_udp_stats()
        except OSError as exc:
            print(f"[TLS UDP] ERROR listen {public_port}: {exc}", flush=True)
        finally:
            for sock in (backend, listener):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-port", type=_port_arg, default=7110)
    parser.add_argument("--video-backend", type=_port_arg, default=7112)
    parser.add_argument("--input-port", type=_port_arg, default=7113)
    parser.add_argument("--input-backend", type=_port_arg, default=7116)
    parser.add_argument("--second-video-port", type=_port_arg, default=7114)
    parser.add_argument("--second-video-backend", type=_port_arg, default=7115)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    cert, key = ensure_identity()
    context = create_server_context(cert, key)

    code = f"{secrets.randbelow(1_000_000):06d}"
    proxy = Proxy(code, debug=args.debug)
    fingerprint = certificate_fingerprint()
    print(f"[TLS] Fingerprint: {fingerprint}", flush=True)
    print(f"[TLS CONTROL] PAIRING_CODE {code}", flush=True)
    threading.Thread(
        target=proxy.serve,
        args=(context, args.second_video_port, args.second_video_backend),
        daemon=True,
    ).start()
    threading.Thread(
        target=proxy.serve_udp,
        args=(args.input_port, args.input_backend, fingerprint),
        daemon=True,
    ).start()
    proxy.serve(context, args.video_port, args.video_backend)


if __name__ == "__main__":
    main()
