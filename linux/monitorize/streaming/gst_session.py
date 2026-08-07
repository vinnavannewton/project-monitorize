"""Runtime-controlled GStreamer RTP session for Monitorize video."""

import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import select
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstVideo

from .video_transport import (
    FEC_PAYLOAD_TYPE, HELLO_PREFIX, MTU,
    RTP_PAYLOAD_TYPE, TRANSPORT, is_start_message, parse_hello,
    udp_send_buffer_bytes,
)

SENDER_NAME = "monitorize-rtp-sender"
SENDER_PACING_HEADROOM_PERCENT = 25
MAX_CAPTURE_TO_STATS_NS = 1_000_000_000
RTP_CLOCK_RATE = 90_000
MIN_RECOVERY_IDR_INTERVAL_SECONDS = 1.0
BITRATE_REDUCTION_COOLDOWN_SECONDS = 1.0
SEVERE_LOSS_PERCENT = 5.0
MIN_ADAPTIVE_BITRATE_KBPS = 4_000


def sender_pacing_kbps(bitrate):
    return max(1_000, int(bitrate) * (100 + SENDER_PACING_HEADROOM_PERCENT) // 100)


def congestion_bitrate_kbps(current_bitrate, loss_percent):
    if float(loss_percent) < SEVERE_LOSS_PERCENT:
        return int(current_bitrate)
    return max(MIN_ADAPTIVE_BITRATE_KBPS, int(current_bitrate) * 3 // 4)


class Session:
    def __init__(self, description, control_port, bitrate, target_fps, width, height):
        Gst.init(None)
        self.pipeline = Gst.parse_launch(description)
        self.exit_code = 0
        self.control_port = control_port
        self.loop = GLib.MainLoop()
        self.running = True
        self.force_key_count = 0
        self.confirmed_idr_count = 0
        self.scheduled_idr_count = 0
        self.coalesced_idr_count = 0
        self.pending_idr_since = None
        self.last_forced_idr_at = None
        self.last_idr_ms = None
        self.last_idr_kib = None
        self.target_fps = target_fps
        self.width = width
        self.height = height
        self.current_bitrate = max(1, int(bitrate))
        self.last_congestion_check_at = time.monotonic()
        self.peak_client_loss_percent = 0.0
        self.last_client_stats_log = 0.0
        self.pacing_bytes_per_second = sender_pacing_kbps(self.current_bitrate) * 1000 // 8
        self.sender = None
        self.sender_lock = threading.Lock()
        self.sender_metrics = {
            "txKbps": 0.0, "txPps": 0.0, "queuePackets": 0.0,
            "queueDelayMs": 0.0, "droppedFrames": 0.0, "sendErrors": 0.0,
        }
        self.last_metric_report = time.monotonic()
        self.metrics = {
            "source": 0, "paced": 0, "encoded": 0, "rtp_packets": 0,
            "rtp_bytes": 0, "fec_packets": 0, "encode_latency_total_ms": 0.0,
            "encode_latency_samples": 0,
        }
        self.capture_pts = {}
        self.capture_buffer_times = {}
        self.latest_encoder_capture_time = None
        self.capture_rtp_times = {}
        self.encoded_capture_times = deque(maxlen=240)
        self.has_rate_filter = self._element("videorate0", "monitorize_rate") is not None
        self.start_sender()
        print(f"[RTP] Fixed bitrate {self.current_bitrate} kbps", flush=True)

    @staticmethod
    def find_sender():
        override = os.environ.get("MONITORIZE_RTP_SENDER", "").strip()
        candidates = [override, str(Path(sys.executable).with_name(SENDER_NAME))]
        from_path = shutil.which(SENDER_NAME)
        if from_path:
            candidates.append(from_path)
        candidates.append(str(
            Path(__file__).resolve().parents[2] / "native" / "rtp_sender" / SENDER_NAME
        ))
        return next((path for path in candidates if path and os.path.isfile(path)
                     and os.access(path, os.X_OK)), "")

    @staticmethod
    def _sender_values(line):
        return dict(re.findall(r"([A-Za-z]+)=([^\s]+)", line))

    def start_sender(self):
        sink = self.pipeline.get_by_name("udpsink0")
        if sink is None:
            raise RuntimeError("RTP pipeline has no UDP sink")
        sender_path = self.find_sender()
        if not sender_path:
            raise RuntimeError(
                "deterministic RTP sender is missing; re-run the Monitorize installer"
            )
        self.client_host = sink.get_property("host")
        self.client_port = int(sink.get_property("port"))
        bind_port = int(sink.get_property("bind-port"))
        self.sender = subprocess.Popen(
            [sender_path, str(bind_port), self.client_host, str(self.client_port),
             str(self.target_fps), str(udp_send_buffer_bytes(self.current_bitrate)),
             str(sender_pacing_kbps(self.current_bitrate))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        deadline = time.monotonic() + 3
        ready_line = ""
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [self.sender.stdout], [], [], max(0, deadline - time.monotonic())
            )
            if not ready:
                break
            ready_line = self.sender.stdout.readline().strip()
            if ready_line.startswith(("READY ", "ERROR ")):
                break
        values = self._sender_values(ready_line)
        try:
            input_port = int(values["inputPort"])
        except (KeyError, ValueError):
            self.stop_sender()
            raise RuntimeError(
                f"deterministic RTP sender failed to start: {ready_line or 'no READY'}"
            )
        sink.set_property("host", "127.0.0.1")
        sink.set_property("port", input_port)
        sink.set_property("bind-port", 0)
        print(
            f"[RTP] Deterministic sender ready on loopback UDP {input_port}; "
            f"network source port {bind_port}, pacing "
            f"{sender_pacing_kbps(self.current_bitrate)} kbps",
            flush=True,
        )
        threading.Thread(target=self.sender_output_loop, daemon=True).start()

    def sender_output_loop(self):
        process = self.sender
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line.startswith("STAT "):
                values = self._sender_values(line)
                for key in self.sender_metrics:
                    try:
                        self.sender_metrics[key] = float(values[key])
                    except (KeyError, ValueError):
                        pass
            elif line.startswith(("DROP ", "DEST ")):
                print(f"[RTP][Sender] {line}", flush=True)
            elif line.startswith("ERROR "):
                print(f"[RTP] ERROR: deterministic sender: {line[6:]}", flush=True)
                GLib.idle_add(self.loop.quit)
        if self.running:
            print("[RTP] ERROR: deterministic sender exited unexpectedly", flush=True)
            GLib.idle_add(self.loop.quit)

    def sender_command(self, command):
        with self.sender_lock:
            if self.sender is None or self.sender.poll() is not None or self.sender.stdin is None:
                raise RuntimeError("deterministic RTP sender is not running")
            self.sender.stdin.write(command + "\n")
            self.sender.stdin.flush()

    def reduce_bitrate_for_congestion(self, loss_percent):
        bitrate = congestion_bitrate_kbps(self.current_bitrate, loss_percent)
        if bitrate >= self.current_bitrate:
            return False
        encoder = self._element(
            "nvh264enc0", "vah264enc0", "vah264lpenc0", "vaapih264enc0", "x264enc0",
        )
        if encoder is None:
            return False
        encoder.set_property("bitrate", bitrate)
        self.current_bitrate = bitrate
        self.pacing_bytes_per_second = sender_pacing_kbps(bitrate) * 1000 // 8
        self.sender_command(f"RATE {sender_pacing_kbps(bitrate)}")
        print(
            f"[RTP] Congestion: reduced bitrate to {bitrate} kbps "
            f"(receiver loss {float(loss_percent):.1f}%)",
            flush=True,
        )
        self.force_key_unit(replace_pending=True)
        return False

    def observe_client_congestion(self, loss_percent, now):
        self.peak_client_loss_percent = max(
            getattr(self, "peak_client_loss_percent", 0.0), float(loss_percent),
        )
        last_check = getattr(self, "last_congestion_check_at", now)
        self.last_congestion_check_at = last_check
        if now - last_check < BITRATE_REDUCTION_COOLDOWN_SECONDS:
            return
        peak_loss = self.peak_client_loss_percent
        self.peak_client_loss_percent = 0.0
        self.last_congestion_check_at = now
        self.reduce_bitrate_for_congestion(peak_loss)

    def stop_sender(self):
        process, self.sender = self.sender, None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
            process.wait(timeout=2)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def force_key_unit(self, replace_pending=False):
        if self.pending_idr_since is not None and not replace_pending:
            self.coalesced_idr_count += 1
            print("[RTP] Recovery IDR request coalesced; one is pending", flush=True)
            return False
        now = time.monotonic()
        last_forced = self.last_forced_idr_at
        if (not replace_pending and last_forced is not None and
                now - last_forced < MIN_RECOVERY_IDR_INTERVAL_SECONDS):
            self.coalesced_idr_count += 1
            print("[RTP] Recovery IDR request rate-limited", flush=True)
            return False
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
                self.pending_idr_since = now
                self.last_forced_idr_at = now
                print("[RTP] Forced IDR from receiver feedback", flush=True)
            return False
        return False

    @staticmethod
    def encoded_access_unit_has_idr(data):
        for match in re.finditer(b"\x00\x00(?:\x00)?\x01", data):
            header = match.end()
            if header < len(data) and data[header] & 0x1f == 5:
                return True
        return False

    def record_encoded_idr(self, size, now=None):
        now = time.monotonic() if now is None else now
        self.last_idr_kib = size / 1024
        if self.pending_idr_since is None:
            self.scheduled_idr_count += 1
            return
        self.confirmed_idr_count += 1
        self.last_idr_ms = (now - self.pending_idr_since) * 1000
        self.pending_idr_since = None
        print(
            f"[RTP] Recovery IDR confirmed in {self.last_idr_ms:.1f} ms "
            f"({self.last_idr_kib:.1f} KiB)",
            flush=True,
        )

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
            f"residual={message.get('residualLost', 0)} "
            f"assemblyP95={float(message.get('assemblyP95Ms', 0)):.1f}ms "
            f"late={message.get('lateFrames', 0)}{latency}",
            flush=True,
        )

    def handle_stats_message(self, message):
        if message.get("type") != "stats":
            return False
        self.observe_client_congestion(
            message.get("lossPercent", 0), time.monotonic(),
        )
        self.report_client_stats(message)
        return True

    def stats_reply(self, message, received_ns):
        try:
            timestamp = int(message.get("renderedRtpTimestamp", -1))
        except (TypeError, ValueError):
            timestamp = -1
        capture_ns = self.capture_rtp_times.get(timestamp)
        if capture_ns is None and timestamp >= 0 and self.capture_rtp_times:
            nearest_timestamp, nearest_capture = min(
                self.capture_rtp_times.items(),
                key=lambda item: abs(self.rtp_timestamp_delta(timestamp, item[0])),
            )
            timestamp_delta = self.rtp_timestamp_delta(timestamp, nearest_timestamp)
            if abs(timestamp_delta) <= RTP_CLOCK_RATE:
                capture_ns = nearest_capture + (
                    timestamp_delta * 1_000_000_000 // RTP_CLOCK_RATE
                )
        if capture_ns is None:
            latest_capture = getattr(self, "latest_encoder_capture_time", None)
            if latest_capture is not None:
                capture_ns = latest_capture - (
                    1_000_000_000 // max(1, self.target_fps)
                )
        if capture_ns is not None and not (
            0 <= received_ns - capture_ns <= MAX_CAPTURE_TO_STATS_NS
        ):
            capture_ns = None
        return {
            "transport": TRANSPORT,
            "status": "stats",
            "hostRecvNs": received_ns,
            "hostSendNs": time.monotonic_ns(),
            "rtpTimestamp": timestamp,
            "captureNs": capture_ns,
        }

    @staticmethod
    def rtp_timestamp_delta(timestamp, reference):
        return ((timestamp - reference + 0x80000000) & 0xffffffff) - 0x80000000

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
        pts = getattr(buffer, "pts", Gst.CLOCK_TIME_NONE)
        if pts != Gst.CLOCK_TIME_NONE:
            if captured_at is None:
                captured_at = self.capture_pts.get(pts)
            self.record_capture_pts(pts, captured_at)
        self.latest_encoder_capture_time = (
            time.monotonic_ns() if captured_at is None else captured_at
        )

    def record_encoded_capture(self, pts):
        captured_at = (
            self.capture_pts.get(pts)
            if pts != Gst.CLOCK_TIME_NONE else None
        )
        if captured_at is None:
            captured_at = self.latest_encoder_capture_time
        if captured_at is not None:
            self.encoded_capture_times.append(captured_at)
        return captured_at

    def record_rtp_capture(self, timestamp, pts):
        ordered_capture = (
            self.encoded_capture_times.popleft()
            if self.encoded_capture_times else None
        )
        captured_at = (
            ordered_capture if ordered_capture is not None else self.capture_pts.get(pts)
        )
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
        old_host, old_port = self.client_host, self.client_port
        if old_host == host and old_port == int(port):
            print(f"[RTP] Receiver unchanged at {host}:{port}", flush=True)
            return False
        try:
            self.sender_command(f"DEST {host} {int(port)}")
        except RuntimeError as exc:
            print(f"[RTP] ERROR: {exc}", flush=True)
            self.loop.quit()
            return False
        self.client_host, self.client_port = host, int(port)
        print(
            f"[RTP] Switched receiver {old_host}:{old_port} -> {host}:{port}",
            flush=True,
        )
        self.force_key_unit(replace_pending=True)
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
                    if (
                        self.pending_idr_since is not None
                        or not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
                    ):
                        encoded = buffer.extract_dup(0, buffer.get_size())
                        if self.encoded_access_unit_has_idr(encoded):
                            self.record_encoded_idr(buffer.get_size())
                elif kind == "rtp_packets":
                    self.metrics["rtp_bytes"] += buffer.get_size()
                    data = buffer.extract_dup(0, min(12, buffer.get_size()))
                    if len(data) >= 2 and data[1] & 0x7f == FEC_PAYLOAD_TYPE:
                        self.metrics["fec_packets"] += 1
                    elif (
                        len(data) >= 8
                        and data[1] & 0x7f == RTP_PAYLOAD_TYPE
                        and data[1] & 0x80
                    ):
                        self.record_rtp_capture(
                            int.from_bytes(data[4:8], "big"), buffer.pts,
                        )
                return Gst.PadProbeReturn.OK
            return probe

        for names, kind in (
            (("monitorize_kwin_source", "pipewiresrc0", "monitorize_source"), "source"),
            (("videorate0", "monitorize_rate"), "paced"),
            (("h264parse0", "monitorize_parser"), "encoded"),
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
        if (self.pending_idr_since is not None and
                now - self.pending_idr_since >= 0.5):
            print(
                "[RTP] WARNING: recovery IDR not observed within 500 ms; retrying",
                flush=True,
            )
            self.force_key_unit(replace_pending=True)
        sender = dict(self.sender_metrics)
        paced_frames = metrics["paced"] if self.has_rate_filter else metrics["source"]
        print(
            "[RTP][Host] "
            f"capture={capture_fps:.1f}fps paced={paced_frames / elapsed:.1f}fps "
            f"encoded={encoded_fps:.1f}fps "
            f"rtp={sender['txPps'] or metrics['rtp_packets'] / elapsed:.1f}pps "
            f"tx={sender['txKbps'] or actual_kbps:.0f}kbps "
            f"bitrate={self.current_bitrate}kbps "
            f"videoBitrate={video_bitrate}kbps fec={fec_percent}% "
            f"fecPps={metrics['fec_packets'] / elapsed:.1f} "
            f"pacing={pacing_kbps:.0f}kbps encodePath={encode_path} "
            f"senderQueue={sender['queuePackets']:.0f} "
            f"senderDelay={sender['queueDelayMs']:.2f}ms "
            f"senderDrops={sender['droppedFrames']:.0f} "
            f"sendErrors={sender['sendErrors']:.0f} "
            f"scheduledIdr={self.scheduled_idr_count} "
            f"recoveryIdr={self.force_key_count} "
            f"confirmedIdr={self.confirmed_idr_count} "
            f"coalescedIdr={self.coalesced_idr_count} "
            f"idrKiB={self.last_idr_kib if self.last_idr_kib is not None else 'unavailable'} "
            f"idrMs={self.last_idr_ms if self.last_idr_ms is not None else 'unavailable'}",
            flush=True,
        )
        return self.running

    def bus_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.exit_code = 1
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
            self.running = False
            self.stop_sender()
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
        try:
            self.loop.run()
        finally:
            self.running = False
            self.pipeline.send_event(Gst.Event.new_eos())
            self.pipeline.set_state(Gst.State.NULL)
            self.stop_sender()
        return self.exit_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--bitrate", type=int, required=True)
    parser.add_argument("--target-fps", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("description")
    args = parser.parse_args()
    try:
        session = Session(
            args.description, args.control_port, args.bitrate, args.target_fps,
            args.width, args.height,
        )
    except RuntimeError as exc:
        print(f"[RTP] ERROR: {exc}", flush=True)
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, lambda *_: session.loop.quit())
    signal.signal(signal.SIGINT, lambda *_: session.loop.quit())
    raise SystemExit(session.run())


if __name__ == "__main__":
    main()
