
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
    def __init__(self, pairing_code: str):
        self.pairing_code = pairing_code
        self.tokens = _load_tokens()
        self.lock = threading.Lock()

    def authenticate(self, client: ssl.SSLSocket) -> bool:
        line = _read_line(client)
        command, _, value = line.partition(" ")
        failed_pairing = False
        with self.lock:
            if command == "AUTH" and any(
                secrets.compare_digest(value.lower(), token) for token in self.tokens
            ):
                client.sendall(b"OK\n")
                print("[TLS] Client authenticated.", flush=True)
                return True
            if self.pairing_code and command == "PAIR" and secrets.compare_digest(value, self.pairing_code):
                token = secrets.token_hex(32)
                self.tokens.add(token)
                _save_tokens(self.tokens)
                client.sendall(f"OK {token}\n".encode("ascii"))
                print("[TLS] Pairing accepted.", flush=True)
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

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-port", type=_port_arg, default=7110)
    parser.add_argument("--video-backend", type=_port_arg, default=7112)
    parser.add_argument("--input-port", type=_port_arg, default=7113)
    parser.add_argument("--input-backend", type=_port_arg, default=7111)
    parser.add_argument("--second-video-port", type=_port_arg, default=7114)
    parser.add_argument("--second-video-backend", type=_port_arg, default=7115)
    args = parser.parse_args()

    cert, key = ensure_identity()
    context = create_server_context(cert, key)

    code = f"{secrets.randbelow(1_000_000):06d}"
    proxy = Proxy(code)
    print(f"[TLS] Fingerprint: {certificate_fingerprint()}", flush=True)
    print(f"[TLS CONTROL] PAIRING_CODE {code}", flush=True)
    threading.Thread(
        target=proxy.serve, args=(context, args.video_port, args.video_backend), daemon=True
    ).start()
    threading.Thread(
        target=proxy.serve,
        args=(context, args.second_video_port, args.second_video_backend),
        daemon=True,
    ).start()
    proxy.serve(context, args.input_port, args.input_backend)


if __name__ == "__main__":
    main()
