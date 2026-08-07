"""Mono system-audio sender for Monitorize receivers."""

import argparse
import json
import signal
import socket
import subprocess


CONTROL_PREFIX = b"MZA1 "
TRANSPORT = "rtp-opus-udp-v1"
SAMPLE_RATE = 48_000
CHANNELS = 1
PACKET_MS = 10
RTP_PAYLOAD_TYPE = 97
OPUS_BITRATE = 96_000


def parse_start(data):
    if not data.startswith(CONTROL_PREFIX):
        return None
    try:
        message = json.loads(data[len(CONTROL_PREFIX):].decode("utf-8"))
        port = int(message["port"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        message.get("transport") != TRANSPORT
        or message.get("type") != "start"
        or not 1 <= port <= 65535
    ):
        return None
    return port


def _common_pipeline():
    return [
        "gst-launch-1.0", "-e",
        "pulsesrc", "device=@DEFAULT_MONITOR@", "do-timestamp=true",
        "buffer-time=10000", "latency-time=5000", "!",
        "queue", "max-size-buffers=2", "max-size-bytes=0", "max-size-time=0",
        "leaky=downstream", "!",
        "audioconvert", "!", "audioresample", "!",
        f"audio/x-raw,format=S16LE,layout=interleaved,rate={SAMPLE_RATE},channels=2",
        "!", "audioconvert", "mix-matrix=<<(float)0.5,(float)0.5>>",
        "!", "volume", "volume=0.70710678", "!",
        f"audio/x-raw,format=S16LE,layout=interleaved,rate={SAMPLE_RATE},channels={CHANNELS}",
        "!", "audiobuffersplit", "output-buffer-duration=1/100", "!",
    ]


def wifi_pipeline(host, port, bind_port):
    return _common_pipeline() + [
        "opusenc", f"bitrate={OPUS_BITRATE}", "bitrate-type=constrained-vbr",
        f"frame-size={PACKET_MS}", "audio-type=generic",
        "perfect-timestamp=true", "!",
        "rtpopuspay", f"pt={RTP_PAYLOAD_TYPE}", "mtu=1200",
        "min-ptime=10000000", "max-ptime=10000000", "!",
        "udpsink", f"host={host}", f"port={port}", f"bind-port={bind_port}",
        "sync=false", "async=false", "qos-dscp=48",
    ]


def usb_pipeline(port):
    return _common_pipeline() + [
        "tcpserversink", "host=127.0.0.1", f"port={port}",
        "sync=false", "sync-method=latest", "buffers-soft-max=6",
        "buffers-max=20", "recover-policy=latest",
    ]


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_usb(port):
    print(f"[Audio] USB PCM 768 kbps payload server on TCP {port}", flush=True)
    pipeline = subprocess.Popen(usb_pipeline(port))
    signal.signal(signal.SIGTERM, lambda *_args: stop_process(pipeline))
    signal.signal(signal.SIGINT, lambda *_args: stop_process(pipeline))
    return pipeline.wait()


def run_wifi(port):
    running = True
    pipeline = None

    def stop(*_args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(4)
    server.settimeout(1)
    print(
        f"[Audio] Waiting for Android on TCP {port}; "
        "Opus 96 kbps payload (~128 kbps with RTP/UDP/IP)",
        flush=True,
    )
    try:
        while running:
            try:
                client, address = server.accept()
            except socket.timeout:
                if pipeline is not None and pipeline.poll() is not None:
                    print(f"[Audio] GStreamer exited with code {pipeline.returncode}", flush=True)
                    pipeline = None
                continue
            with client:
                client.settimeout(2)
                data = b""
                try:
                    while b"\n" not in data and len(data) < 4096:
                        chunk = client.recv(4096 - len(data))
                        if not chunk:
                            break
                        data += chunk
                except (OSError, socket.timeout):
                    continue
                if b"\n" not in data:
                    continue
                receiver_port = parse_start(data.split(b"\n", 1)[0])
                if receiver_port is None:
                    continue
                stop_process(pipeline)
                pipeline = subprocess.Popen(
                    wifi_pipeline(address[0], receiver_port, port)
                )
                reply = json.dumps({
                    "status": "ready", "version": 1, "transport": TRANSPORT,
                    "codec": "OPUS", "sampleRate": SAMPLE_RATE,
                    "channels": CHANNELS, "packetMs": PACKET_MS,
                    "rtpPt": RTP_PAYLOAD_TYPE, "bitrate": OPUS_BITRATE,
                }, separators=(",", ":")).encode("utf-8")
                try:
                    client.sendall(CONTROL_PREFIX + reply + b"\n")
                except OSError:
                    pass
                print(
                    f"[Audio] RTP/Opus receiver {address[0]}:{receiver_port}",
                    flush=True,
                )
    finally:
        server.close()
        stop_process(pipeline)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("wifi", "usb"))
    parser.add_argument("--port", type=int, default=7120)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    raise SystemExit(run_wifi(args.port) if args.mode == "wifi" else run_usb(args.port))


if __name__ == "__main__":
    main()
