
"""Connect to an encrypted Monitorize host and expose plaintext on localhost."""

import argparse
import hashlib
import socket
import ssl
import threading


def _read_line(sock: ssl.SSLSocket, limit: int = 256) -> str:
    data = bytearray()
    while len(data) < limit:
        byte = sock.recv(1)
        if not byte:
            break
        if byte == b"\n":
            return data.decode("ascii")
        data.extend(byte)
    raise ValueError("invalid server response")


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--fingerprint", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--code", default="")
    parser.add_argument("--local-port", type=int, default=17110)
    args = parser.parse_args()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    remote = context.wrap_socket(
        socket.create_connection((args.host, args.port), timeout=5),
        server_hostname=args.host,
    )
    fingerprint = hashlib.sha256(remote.getpeercert(binary_form=True)).hexdigest().upper()
    if args.fingerprint and fingerprint != args.fingerprint.upper():
        print(f"[TLS RECEIVER] AUTH_FAILED {fingerprint}", flush=True)
        return 2

    if args.token:
        remote.sendall(f"AUTH {args.token}\n".encode("ascii"))
    elif args.code:
        remote.sendall(f"PAIR {args.code}\n".encode("ascii"))
    else:
        print(f"[TLS RECEIVER] AUTH_FAILED {fingerprint}", flush=True)
        return 2

    response = _read_line(remote)
    if not response.startswith("OK"):
        print(f"[TLS RECEIVER] AUTH_FAILED {fingerprint}", flush=True)
        return 2
    token = args.token or response.removeprefix("OK ").strip()
    if len(token) != 64:
        print("[TLS RECEIVER] ERROR invalid authentication token", flush=True)
        return 1

    print(f"[TLS RECEIVER] CREDENTIALS {fingerprint} {token}", flush=True)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.local_port))
    listener.listen(1)
    print("[TLS RECEIVER] READY", flush=True)
    local, _ = listener.accept()
    local.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    remote.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    threading.Thread(target=_pipe, args=(local, remote), daemon=True).start()
    _pipe(remote, local)


if __name__ == "__main__":
    try:
        raise SystemExit(main() or 0)
    except (OSError, ValueError, ssl.SSLError) as exc:
        print(f"[TLS RECEIVER] ERROR {exc}", flush=True)
        raise SystemExit(1)
