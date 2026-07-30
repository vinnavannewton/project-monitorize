"""Runtime-controlled GStreamer RTP session for Monitorize video."""

import argparse
from collections import deque
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
    FEC_PAYLOAD_TYPE, HELLO_PREFIX, MTU,
    RTP_PAYLOAD_TYPE, TRANSPORT, is_start_message, parse_hello,
    udp_send_buffer_bytes,
)


class Session:
    def __init__(self, description, control_port, bitrate, target_fps, width, height):
        Gst.init(None)
        self.pipeline = Gst.parse_launch(description)
        self.control_port = control_port
        self.loop = GLib.MainLoop()
        self.running = True
        self.force_key_count = 0
        self.target_fps = target_fps
        self.width = width
        self.height = height
        self.current_bitrate = max(1, int(bitrate))
        self.last_client_stats_log = 0.0
        self.pacing_bytes_per_second = 0
        self.pacing_fd = None
        self.last_metric_report = time.monotonic()
        self.metrics = {
            "source": 0, "paced": 0, "encoded": 0, "rtp_packets": 0,
            "rtp_bytes": 0, "fec_packets": 0, "encode_latency_total_ms": 0.0,
            "encode_latency_samples": 0,
        }
        self.capture_pts = {}
        self.capture_buffer_times = {}
        self.encoder_capture_times = deque(maxlen=240)
        self.capture_rtp_times = {}
        self.encoded_capture_times = deque(maxlen=240)
        self.last_media_rtp_timestamp = None
        self.configure_udp_socket(self.current_bitrate)
        print(f"[RTP] Fixed bitrate {self.current_bitrate} kbps", flush=True)

    def configure_udp_socket(self, bitrate_kbps):
        sink = self.pipeline.get_by_name("udpsink0")
        if sink is None:
            return
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF,
            udp_send_buffer_bytes(bitrate_kbps),
        )
        raw.bind(("0.0.0.0", int(sink.get_property("bind-port"))))
        self._set_kernel_pacing(raw, bitrate_kbps)
        self.pacing_fd = raw.detach()
        gio_socket = Gio.Socket.new_from_fd(self.pacing_fd)
        sink.set_property("socket", gio_socket)
        sink.set_property("close-socket", True)

    @staticmethod
    def _pacing_rate(bitrate_kbps):
        return max(1, int(bitrate_kbps * 1000 * 2 / 8))

    def _set_kernel_pacing(self, udp_socket, bitrate_kbps):
        rate = self._pacing_rate(bitrate_kbps)
        try:
            udp_socket.setsockopt(socket.SOL_SOCKET, 47, rate)
        except OSError:
            return False
        self.pacing_bytes_per_second = rate
        print(f"[RTP] Kernel pacing enabled at {rate} B/s", flush=True)
        return True

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

    def report_client_stats(self, message):
        now = time.monotonic()
        if now - self.last_client_stats_log < 1:
            return
        self.last_client_stats_log = now
        e2e = message.get("endToEndMs")
        clock_error = message.get("clockErrorMs")
        try:
            latency = (
                f" e2e={float(e2e):.1f}ms clockError={float(clock_error):.1f}ms"
                if float(e2e) >= 0 and float(clock_error) >= 0 else ""
            )
        except (TypeError, ValueError):
            latency = ""
        print(
            "[RTP][Client] "
            f"rx={message.get('receivedKbps', 0)}kbps "
            f"pps={message.get('packetsPerSecond', 0)} "
            f"loss={float(message.get('lossPercent', 0)):.1f}% "
            f"incomplete={message.get('incomplete', 0)} "
            f"render={int(message.get('renderedFrames', 0)) * 1000 / max(1, int(message.get('intervalMs', 1))):.1f}fps "
            f"queue={message.get('queueDepth', 0)} "
            f"decode={float(message.get('decodeMs', 0)):.1f}ms "
            f"renderLatency={float(message.get('renderMs', 0)):.1f}ms "
            f"dropped={message.get('decoderDropped', 0)} "
            f"media={message.get('mediaPackets', 0)} "
            f"fec={message.get('fecPackets', 0)} "
            f"recovered={message.get('fecRecovered', 0)} "
            f"unrecoverable={message.get('fecUnrecoverable', 0)} "
            f"residual={message.get('residualLost', 0)}{latency}",
            flush=True,
        )

    def handle_stats_message(self, message):
        if message.get("type") != "stats":
            return False
        self.report_client_stats(message)
        return True

    def stats_reply(self, message, received_ns):
        try:
            timestamp = int(message.get("renderedRtpTimestamp", -1))
        except (TypeError, ValueError):
            timestamp = -1
        capture_ns = self.capture_rtp_times.get(timestamp)
        return {
            "transport": TRANSPORT,
            "status": "stats",
            "hostRecvNs": received_ns,
            "hostSendNs": time.monotonic_ns(),
            "rtpTimestamp": timestamp,
            "captureNs": capture_ns,
        }

    def record_capture_pts(self, pts, captured_at=None):
        self.capture_pts[pts] = time.monotonic_ns() if captured_at is None else captured_at
        if len(self.capture_pts) > 240:
            self.capture_pts.pop(next(iter(self.capture_pts)))

    def record_capture_buffer(self, buffer, captured_at=None):
        """Keep the source timestamp with this raw buffer until encoder input.

        PipeWire/VA-API may discard buffer PTS.  Queues keep the same GstBuffer,
        so its miniobject hash gives us a reliable primary association.  A frame
        synthesized by videorate has no source entry and is stamped there instead.
        """
        key = hash(buffer)
        if key not in self.capture_buffer_times:
            self.capture_buffer_times[key] = (
                time.monotonic_ns() if captured_at is None else captured_at
            )
        if len(self.capture_buffer_times) > 480:
            self.capture_buffer_times.pop(next(iter(self.capture_buffer_times)))

    def record_encoder_input_capture(self, buffer):
        captured_at = self.capture_buffer_times.pop(hash(buffer), None)
        self.encoder_capture_times.append(
            time.monotonic_ns() if captured_at is None else captured_at
        )

    def record_encoded_capture(self, pts):
        captured_at = (
            self.encoder_capture_times.popleft()
            if self.encoder_capture_times else self.capture_pts.get(pts)
        )
        if captured_at is not None:
            self.encoded_capture_times.append(captured_at)
        return captured_at

    def record_rtp_capture(self, timestamp, pts):
        if timestamp == self.last_media_rtp_timestamp:
            return
        self.last_media_rtp_timestamp = timestamp
        ordered_capture = (
            self.encoded_capture_times.popleft()
            if self.encoded_capture_times else None
        )
        captured_at = self.capture_pts.get(pts, ordered_capture)
        if captured_at is not None:
            self.capture_rtp_times[timestamp] = captured_at
            if len(self.capture_rtp_times) > 240:
                self.capture_rtp_times.pop(next(iter(self.capture_rtp_times)))

    def handle_idr_message(self, message):
        if message.get("type") != "idr":
            return False
        GLib.idle_add(self.force_key_unit)
        return True

    def update_client(self, host, port):
        sink = self.pipeline.get_by_name("udpsink0")
        if sink is None:
            return False
        old_host = sink.get_property("host")
        old_port = sink.get_property("port")
        if old_host == host and int(old_port) == int(port):
            print(f"[RTP] Receiver unchanged at {host}:{port}", flush=True)
            return False
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
                    received_ns = time.monotonic_ns()
                    parsed = parse_hello(data.split(b"\n", 1)[0])
                    if parsed is None:
                        continue
                    port, message = parsed
                    if self.handle_stats_message(message):
                        reply = json.dumps(
                            self.stats_reply(message, received_ns), separators=(",", ":")
                        ).encode()
                        client.sendall(HELLO_PREFIX + reply + b"\n")
                        continue
                    if self.handle_idr_message(message):
                        reply = json.dumps({
                            "transport": TRANSPORT, "status": "idr-requested",
                            "version": 1,
                        }, separators=(",", ":")).encode()
                        client.sendall(HELLO_PREFIX + reply + b"\n")
                        continue
                    if not is_start_message(message):
                        continue
                    profiles = message.get("decoderProfiles", [])
                    profile = "high" if "high" in profiles else "constrained-baseline"
                    payloader = self.pipeline.get_by_name("rtph264pay0")
                    fec = self.pipeline.get_by_name("rtpulpfecenc0")
                    ssrc = int(payloader.get_property("ssrc")) if payloader else 0
                    reply = json.dumps({
                        "transport": TRANSPORT, "status": "ready", "version": 1,
                        "mtu": MTU, "rtpPt": RTP_PAYLOAD_TYPE,
                        "fecPt": FEC_PAYLOAD_TYPE,
                        "fecPercent": int(fec.get_property("percentage")) if fec else 0,
                        "ssrc": ssrc, "codec": "h264", "profile": profile,
                        "width": self.width, "height": self.height,
                        "fps": self.target_fps,
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

    def _element(self, *names):
        for name in names:
            element = self.pipeline.get_by_name(name)
            if element is not None:
                return element
        return None

    def install_diagnostics(self):
        def buffer_probe(kind):
            def probe(_pad, info):
                buffer = info.get_buffer()
                if buffer is None:
                    return Gst.PadProbeReturn.OK
                if kind in self.metrics:
                    self.metrics[kind] += 1
                if kind in ("source", "paced"):
                    self.record_capture_buffer(buffer)
                    if buffer.pts != Gst.CLOCK_TIME_NONE:
                        self.record_capture_pts(buffer.pts)
                elif kind == "encoder_input":
                    self.record_encoder_input_capture(buffer)
                elif kind == "encoded":
                    captured_at = self.record_encoded_capture(buffer.pts)
                    if captured_at is not None:
                        self.metrics["encode_latency_total_ms"] += (
                            time.monotonic_ns() - captured_at
                        ) / 1_000_000
                        self.metrics["encode_latency_samples"] += 1
                elif kind == "rtp_packets":
                    self.metrics["rtp_bytes"] += buffer.get_size()
                    data = buffer.extract_dup(0, min(12, buffer.get_size()))
                    if len(data) >= 2 and data[1] & 0x7f == FEC_PAYLOAD_TYPE:
                        self.metrics["fec_packets"] += 1
                    elif (
                        len(data) >= 8
                        and data[1] & 0x7f == RTP_PAYLOAD_TYPE
                        and buffer.pts != Gst.CLOCK_TIME_NONE
                    ):
                        self.record_rtp_capture(
                            int.from_bytes(data[4:8], "big"), buffer.pts,
                        )
                return Gst.PadProbeReturn.OK
            return probe

        for names, kind in (
            (("pipewiresrc0", "monitorize_source"), "source"),
            (("videorate0", "monitorize_rate"), "paced"),
            (("nvh264enc0", "vah264enc0", "vah264lpenc0", "vaapih264enc0", "x264enc0", "monitorize_encoder"), "encoded"),
            (("rtpulpfecenc0", "rtph264pay0", "monitorize_payloader"), "rtp_packets"),
        ):
            element = self._element(*names)
            pad = element.get_static_pad("src") if element else None
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.BUFFER, buffer_probe(kind))
        encoder = self._element(
            "nvh264enc0", "vah264enc0", "vah264lpenc0", "vaapih264enc0",
            "x264enc0", "monitorize_encoder",
        )
        encoder_sink = encoder.get_static_pad("sink") if encoder else None
        if encoder_sink is not None:
            encoder_sink.add_probe(
                Gst.PadProbeType.BUFFER, buffer_probe("encoder_input"),
            )
        GLib.timeout_add_seconds(1, self.log_runtime_diagnostics)

    def log_runtime_diagnostics(self):
        now = time.monotonic()
        elapsed = max(0.001, now - self.last_metric_report)
        self.last_metric_report = now
        metrics, self.metrics = self.metrics, {
            "source": 0, "paced": 0, "encoded": 0, "rtp_packets": 0,
            "rtp_bytes": 0, "fec_packets": 0, "encode_latency_total_ms": 0.0,
            "encode_latency_samples": 0,
        }
        samples = metrics["encode_latency_samples"]
        encode_path = (
            f"{metrics['encode_latency_total_ms'] / samples:.1f}ms"
            if samples else "unavailable"
        )
        actual_kbps = metrics["rtp_bytes"] * 8 / elapsed / 1000
        pacing_kbps = self.pacing_bytes_per_second * 8 / 1000
        capture_fps = metrics["source"] / elapsed
        encoded_fps = metrics["encoded"] / elapsed
        fec = self.pipeline.get_by_name("rtpulpfecenc0")
        fec_percent = int(fec.get_property("percentage")) if fec else 0
        video_bitrate = round(self.current_bitrate * (100 - fec_percent) / 100)
        print(
            "[RTP][Host] "
            f"capture={capture_fps:.1f}fps paced={metrics['paced'] / elapsed:.1f}fps "
            f"encoded={encoded_fps:.1f}fps rtp={metrics['rtp_packets'] / elapsed:.1f}pps "
            f"tx={actual_kbps:.0f}kbps bitrate={self.current_bitrate}kbps "
            f"videoBitrate={video_bitrate}kbps fec={fec_percent}% "
            f"fecPps={metrics['fec_packets'] / elapsed:.1f} "
            f"pacing={pacing_kbps:.0f}kbps encodePath={encode_path} "
            f"recoveryIdr={self.force_key_count}",
            flush=True,
        )
        return self.running

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
        self.install_diagnostics()
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
    parser.add_argument("--bitrate", type=int, required=True)
    parser.add_argument("--target-fps", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("description")
    args = parser.parse_args()
    session = Session(
        args.description, args.control_port, args.bitrate, args.target_fps,
        args.width, args.height,
    )
    signal.signal(signal.SIGTERM, lambda *_: session.loop.quit())
    signal.signal(signal.SIGINT, lambda *_: session.loop.quit())
    raise SystemExit(session.run())


if __name__ == "__main__":
    main()
