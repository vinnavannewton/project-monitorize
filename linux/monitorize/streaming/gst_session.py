"""Runtime-controlled GStreamer RTP session for Monitorize video."""

import argparse
import json
import signal
import socket
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, Gst, GstVideo

from .video_transport import (
    FEC_PAYLOAD_TYPE, HELLO_PREFIX, INITIAL_FEC_PERCENT, MTU,
    RTP_PAYLOAD_TYPE, TRANSPORT, parse_hello,
)


class Session:
    def __init__(self, description, control_port, pacing_bitrate, target_fps):
        Gst.init(None)
        self.pipeline = Gst.parse_launch(description)
        self.control_port = control_port
        self.loop = GLib.MainLoop()
        self.running = True
        self.force_key_count = 0
        self.unhealthy_windows = 0
        self.last_unhealthy = 0.0
        self.healthy_since = time.monotonic()
        self.target_fps = target_fps
        self.original_bitrate = pacing_bitrate
        self.current_bitrate = pacing_bitrate
        self.render_window_started = time.monotonic()
        self.rendered_in_window = 0
        self.overload_windows = 0
        self.configure_udp_socket(pacing_bitrate)

    def configure_udp_socket(self, bitrate_kbps):
        sink = self.pipeline.get_by_name("udpsink0")
        if sink is None:
            return
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262_144)
        raw.bind(("0.0.0.0", int(sink.get_property("bind-port"))))
        try:
            
            rate = max(1, int(bitrate_kbps * 1000 * 1.25 / 8))
            raw.setsockopt(socket.SOL_SOCKET, 47, rate)
            print(f"[RTP] Kernel pacing enabled at {rate} B/s", flush=True)
        except OSError:
            print("[RTP] Kernel pacing unavailable; using bounded UDP sends", flush=True)
        gio_socket = Gio.Socket.new_from_fd(raw.detach())
        sink.set_property("socket", gio_socket)
        sink.set_property("close-socket", True)

    def force_key_unit(self):
        self.force_key_count += 1
        for name in ("nvh264enc0", "vah264enc0", "vah264lpenc0", "vaapih264enc0", "x264enc0"):
            encoder = self.pipeline.get_by_name(name)
            if encoder is None:
                continue
            pad = encoder.get_static_pad("src")
            event = GstVideo.video_event_new_upstream_force_key_unit(
                Gst.CLOCK_TIME_NONE, True, self.force_key_count
            )
            if pad and pad.send_event(event):
                print("[RTP] Forced IDR from receiver feedback", flush=True)
            return

    def update_fec(self, message):
        fec = self.pipeline.get_by_name("rtpulpfecenc0")
        if fec is None:
            return
        now = time.monotonic()
        unhealthy = int(message.get("lost", 0)) > 0 or int(message.get("incomplete", 0)) > 0
        current = int(fec.get_property("percentage"))
        if unhealthy:
            self.unhealthy_windows += 1
            self.last_unhealthy = now
            self.healthy_since = now
            if self.unhealthy_windows >= 2 and current < 20:
                current = min(20, current + 5)
                fec.set_property("percentage", current)
                self.unhealthy_windows = 0
                print(f"[RTP] FEC increased to {current}%", flush=True)
        else:
            self.unhealthy_windows = 0
            if now - self.healthy_since >= 10 and current > 5:
                current = max(5, current - 5)
                fec.set_property("percentage", current)
                self.healthy_since = now
                print(f"[RTP] FEC reduced to {current}%", flush=True)
        self.update_overload(message, now)

    def update_overload(self, message, now):
        self.rendered_in_window += int(message.get("renderedFrames", 0))
        if now - self.render_window_started < 1:
            return
        healthy = (
            self.rendered_in_window >= self.target_fps * 0.95
            and int(message.get("queueDepth", 0)) <= 1
        )
        self.render_window_started = now
        self.rendered_in_window = 0
        self.overload_windows = 0 if healthy else self.overload_windows + 1
        if self.overload_windows >= 2:
            self.set_bitrate(max(1000, int(self.current_bitrate * 0.85)))
            self.overload_windows = 0
        elif healthy and now - self.last_unhealthy >= 10 and self.current_bitrate < self.original_bitrate:
            self.set_bitrate(min(self.original_bitrate, int(self.current_bitrate / 0.85)))

    def set_bitrate(self, bitrate):
        if bitrate == self.current_bitrate:
            return
        for name in ("nvh264enc0", "vah264enc0", "vah264lpenc0", "x264enc0"):
            encoder = self.pipeline.get_by_name(name)
            if encoder is not None and encoder.find_property("bitrate"):
                encoder.set_property("bitrate", bitrate)
                self.current_bitrate = bitrate
                print(f"[RTP] Bitrate adapted to {bitrate} kbps", flush=True)
                return

    def update_client(self, host, port):
        sink = self.pipeline.get_by_name("udpsink0")
        if sink is None:
            return False
        old_host = sink.get_property("host")
        old_port = sink.get_property("port")
        sink.set_property("host", host)
        sink.set_property("port", port)
        print(
            f"[RTP] Switched receiver {old_host}:{old_port} -> {host}:{port}",
            flush=True,
        )
        self.force_key_unit()
        return False

    def control_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.control_port))
        sock.listen(4)
        sock.settimeout(1)
        try:
            while self.running:
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
                    if parsed is None:
                        continue
                    port, message = parsed
                    profiles = message.get("decoderProfiles", [])
                    profile = "high" if "high" in profiles else "constrained-baseline"
                    payloader = self.pipeline.get_by_name("rtph264pay0")
                    ssrc = int(payloader.get_property("ssrc")) if payloader else 0
                    reply = json.dumps({
                        "transport": TRANSPORT, "status": "ready", "version": 1,
                        "mtu": MTU, "rtpPt": RTP_PAYLOAD_TYPE,
                        "fecPt": FEC_PAYLOAD_TYPE,
                        "fecPercent": INITIAL_FEC_PERCENT,
                        "ssrc": ssrc, "codec": "h264", "profile": profile,
                    }, separators=(",", ":")).encode()
                    client.sendall(HELLO_PREFIX + reply + b"\n")
                    GLib.idle_add(self.update_client, addr[0], port)
                except (OSError, ValueError, TypeError):
                    pass
                finally:
                    try:
                        client.close()
                    except OSError:
                        pass
        finally:
            sock.close()

    def bus_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"[GStreamer] ERROR: {error}: {debug or ''}", flush=True)
            self.loop.quit()
        elif message.type == Gst.MessageType.EOS:
            self.loop.quit()

    def run(self):
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.bus_message)
        threading.Thread(target=self.control_loop, daemon=True).start()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            print("[GStreamer] ERROR: pipeline failed to enter PLAYING", flush=True)
            return 1
        sink = self.pipeline.get_by_name("udpsink0")
        if sink:
            dest_host = sink.get_property("host")
            dest_port = sink.get_property("port")
            bind_port = sink.get_property("bind-port")
            print(
                f"[RTP] Pipeline PLAYING — sending to {dest_host}:{dest_port} "
                f"(bind {bind_port})",
                flush=True,
            )
        print("[Pipeline] READY", flush=True)
        try:
            self.loop.run()
        finally:
            self.running = False
            self.pipeline.send_event(Gst.Event.new_eos())
            self.pipeline.set_state(Gst.State.NULL)
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--pacing-bitrate", type=int, required=True)
    parser.add_argument("--target-fps", type=int, required=True)
    parser.add_argument("description")
    args = parser.parse_args()
    session = Session(
        args.description, args.control_port, args.pacing_bitrate, args.target_fps
    )
    signal.signal(signal.SIGTERM, lambda *_: session.loop.quit())
    signal.signal(signal.SIGINT, lambda *_: session.loop.quit())
    raise SystemExit(session.run())


if __name__ == "__main__":
    main()
