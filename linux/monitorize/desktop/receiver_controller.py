"""Desktop stream receiver lifecycle."""

import os
import json
import socket
import subprocess
import threading
import time
from functools import lru_cache

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from monitorize.platform.process_utils import gst_has_element, kill_patterns, stop_processes
from monitorize.platform.utils import LINUX_DIR
from monitorize.config.validation import normalize_host, sanitize_decoder, sanitize_port, valid_host, valid_port
from monitorize.streaming.audio_receiver import LinuxAudioReceiver


COMPRESSED_QUEUE = [
    "queue", "max-size-buffers=3", "max-size-time=0", "max-size-bytes=4194304",
]
RAW_DROP_QUEUE = [
    "queue", "max-size-buffers=1", "max-size-time=0", "max-size-bytes=0",
    "leaky=downstream",
]
PARSED_H264_CAPS = "video/x-h264,stream-format=byte-stream,alignment=au"
SINK_PROPS = {
    "sync": "false",
    "async": "false",
    "qos": "true",
    "enable-last-sample": "false",
    "force-aspect-ratio": "false",
    "max-lateness": "20000000",
}
SINK_EXTRA_PROPS = {
    "waylandsink": {"fullscreen": "true"},
    "xvimagesink": {"double-buffer": "true", "draw-borders": "true"},
}
SOFTWARE_DECODER_PROPS = {
    "max-threads": "2",
    "thread-type": "slice",
    "output-corrupt": "false",
    "discard-corrupted-frames": "true",
    "automatic-request-sync-points": "true",
}
HARDWARE_DECODERS = ("vah264dec", "vaapih264dec")
PRIMARY_STREAM_PORT = 7110
HARDWARE_DECODER_PROPS = {
    "discard-corrupted-frames": "true",
    "automatic-request-sync-points": "true",
    "automatic-request-sync-point-flags": "corrupt-output+discard-input",
    "min-force-key-unit-interval": "250000000",
    "qos": "true",
    "max-errors": "-1",
}

_GST = None
_GIO = None
_GST_VIDEO = None
_GST_IMPORT_ERROR = None


class RtpLossTracker:
    """Count finalized RTP sequence gaps without delaying the stream."""

    def __init__(self, reorder_window=64):
        self.reorder_window = reorder_window
        self.highest = None
        self.finalized = None
        self.seen = set()

    def add(self, sequence):
        if self.highest is None:
            extended = int(sequence)
            self.highest = extended
            self.finalized = extended - 1
        else:
            base = self.highest & ~0xFFFF
            extended = base | int(sequence)
            if extended - self.highest > 0x8000:
                extended -= 0x10000
            elif self.highest - extended > 0x8000:
                extended += 0x10000
            if extended - self.highest > 4096:
                self.highest = extended
                self.finalized = extended - 1
                self.seen.clear()
                self.seen.add(extended)
                return 0
            self.highest = max(self.highest, extended)
        if extended <= self.finalized:
            return 0
        self.seen.add(extended)
        lost = 0
        cutoff = self.highest - self.reorder_window
        while self.finalized < cutoff:
            self.finalized += 1
            if self.finalized in self.seen:
                self.seen.remove(self.finalized)
            else:
                lost += 1
        return lost


class ReceiverNegotiationError(RuntimeError):
    pass


def _load_gst():
    global _GST, _GIO, _GST_VIDEO, _GST_IMPORT_ERROR
    if _GST is not None:
        return _GST
    if _GST_IMPORT_ERROR is not None:
        raise _GST_IMPORT_ERROR
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("Gio", "2.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst
        from gi.repository import Gio
        from gi.repository import GstVideo

        Gst.init(None)
        _GST = Gst
        _GIO = Gio
        _GST_VIDEO = GstVideo
        return Gst
    except Exception as exc:
        _GST_IMPORT_ERROR = exc
        raise


@lru_cache(maxsize=64)
def _gst_element_properties(element):
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", element],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    properties = set()
    in_properties = False
    for line in result.stdout.splitlines():
        if line.strip() == "Element Properties:":
            in_properties = True
            continue
        if not in_properties:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith("  "):
            break
        name = stripped.split(":", 1)[0].strip()
        if name:
            properties.add(name)
    return properties


def _gst_has_property(element, prop):
    return prop in _gst_element_properties(element)


def _negotiate_udp(host, control_port, udp_port):
    hello = json.dumps({
        "transport": "rtp-udp-v1", "port": udp_port,
        "type": "start",
        "decoderProfiles": ["high", "constrained-baseline"],
    }, separators=(",", ":")).encode()
    with socket.create_connection((host, control_port), timeout=1.5) as control:
        control.settimeout(1.5)
        control.sendall(b"MZRP1 " + hello + b"\n")
        response = b""
        while b"\n" not in response and len(response) < 4096:
            chunk = control.recv(4096 - len(response))
            if not chunk:
                break
            response += chunk
    if not response.startswith(b"MZRP1 "):
        raise RuntimeError("invalid UDP control response")
    try:
        ready = json.loads(response.split(b"\n", 1)[0][6:].decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid UDP control response") from exc
    if (
        ready.get("transport") != "rtp-udp-v1"
        or ready.get("status") != "ready"
        or ready.get("codec") != "h264"
        or int(ready.get("rtpPt", 96)) != 96
    ):
        raise RuntimeError("UDP control response rejected")
    return ready


class ReceiverController(QObject):
    receivingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    hostChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str)

    def __init__(self, de, discovery, parent=None):
        super().__init__(parent)
        self.de = de
        self.discovery = discovery
        self.receiving = False
        self.status = ""
        self.host_label = ""
        self.process = None
        self.stopping = False
        self.retry_count = 0
        self.retry_pending = False
        self.attempt_started = 0.0
        self.inhibit_cookie = None
        self.generation = 0
        self.stable_generation = None
        self.stable_process = None
        self.retry_generation = None
        self.receiver_host = ""
        self.receiver_port = 0
        self.pipeline_fallback_used = False
        self.stable = False
        self.gst_pipeline = None
        self.gst_bus = None
        self.gst_generation = None
        self.gst_socket = None
        self.udp_transport = False
        self.show_stats = False
        self.overlay_supported = False
        self.overlay_width = 1920
        self.overlay_height = 1080
        self.overlay_surface = None
        self.stats_elements = {}
        self.stats_lock = threading.Lock()
        self.stats_counts = {}
        self.decode_started = {}
        self.loss_tracker = RtpLossTracker()
        self.last_stats_time = time.monotonic()
        self.last_sink_rendered = 0
        self.last_sink_dropped = 0
        self.last_snapshot = {}
        self.decoder = "Software"
        self.decoder_args = self._software_decoder_args()
        self.decoder_label = "Software avdec_h264"
        self.audio_receiver = LinuxAudioReceiver(self.logAppended.emit)
        self.hardware_decoder_candidates = []
        self.hardware_decoder_index = 0
        self.sink_candidates = []
        self.sink_index = 0
        self.stable_timer = QTimer(self)
        self.stable_timer.setSingleShot(True)
        self.stable_timer.timeout.connect(
            lambda: self._mark_stable(self.stable_generation, self.stable_process)
        )
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(lambda: self._start_attempt(self.retry_generation))
        self.gst_bus_timer = QTimer(self)
        self.gst_bus_timer.setInterval(50)
        self.gst_bus_timer.timeout.connect(self._poll_gst_bus)
        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(250)
        self.stats_timer.timeout.connect(self._update_stats)

    def _set_receiving(self, value):
        self.receiving = value
        self.receivingChanged.emit(value)

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def connect(self, host, port, decoder, show_stats=False):
        self.discovery.stop_browsing()
        self.stop()
        host = normalize_host(host)
        if not valid_host(host) or not valid_port(port):
            self._set_status("Invalid host or port")
            self.logAppended.emit("ERROR: Invalid receiver host or port.")
            return
        port = sanitize_port(port)
        decoder = sanitize_decoder(decoder)
        self.generation += 1
        generation = self.generation
        self.stopping = False
        self.stable = False
        self.host = host
        self.port = port
        self.udp_transport = host != "127.0.0.1"
        self.show_stats = bool(show_stats)
        self.decoder = decoder
        self.sink_candidates = self._sink_candidates()
        self.sink_index = 0
        self.sink = self.sink_candidates[0]
        self.pipeline_fallback_used = False
        if decoder == "Hardware":
            self.hardware_decoder_candidates = [
                name for name in HARDWARE_DECODERS if gst_has_element(name)
            ]
            self.hardware_decoder_index = 0
            if not self.hardware_decoder_candidates:
                self._set_status(
                    "Hardware decoder unavailable — install the GStreamer VA-API decoder"
                )
                self.logAppended.emit(
                    "ERROR: Hardware mode requires vah264dec or vaapih264dec."
                )
                return
            self._select_hardware_decoder(0)
        else:
            self.hardware_decoder_candidates = []
            self.hardware_decoder_index = 0
            self.decoder_args = self._software_decoder_args()
            self.decoder_label = "Software avdec_h264"
        self.retry_count = 0
        self.retry_pending = False
        self.host_label = f"{host}:{port}"
        self.hostChanged.emit(self.host_label)
        self._set_receiving(True)
        self._set_status(f"Connecting to {host}:{port}…")
        self.logAppended.emit(f"Connecting to {host} on port {port}…")
        self._start_attempt(generation)

    def set_stats_visible(self, enabled):
        self.show_stats = bool(enabled)
        if not self.show_stats:
            self.overlay_surface = None
        elif self.gst_pipeline is not None:
            self._render_stats_surface(self.last_snapshot)
            if not self.overlay_supported:
                self.logAppended.emit(
                    "Stats overlay unavailable — install GStreamer cairooverlay and Python Cairo."
                )

    def _start_attempt(self, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        self.retry_pending = False
        try:
            self._launch_pipeline(self.host, self.port, generation)
        except Exception as exc:
            self.logAppended.emit(f"Receiver pipeline error: {exc}")
            self._handle_finished(
                0 if isinstance(exc, ReceiverNegotiationError) else 1,
                generation,
            )

    def _launch_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        self._launch_external_pipeline(host, port, generation)

    def _udp_pipeline_args(self, sink_name, udp_port):
        args = [
            "udpsrc", "name=receiver_source", f"port={udp_port}", "buffer-size=524288",
            'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000', "!",
            "rtph264depay", "name=receiver_depay", "!",
            "h264parse", "name=receiver_parser", "disable-passthrough=true", "config-interval=-1", "!",
            PARSED_H264_CAPS, "!",
            "queue", "name=receiver_compressed_queue", *COMPRESSED_QUEUE[1:], "!",
            *self._named_decoder_args(), "!",
            *self._display_chain_args(sink_name),
        ]
        return args

    def _udp_pipeline_description(self, sink_name, udp_port=0):
        return " ".join(self._udp_pipeline_args(sink_name, udp_port))

    def _launch_udp_pipeline(self, host, port, generation, sink_name):
        self.receiver_host = host
        self.receiver_port = port
        self.attempt_started = time.monotonic()
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(("", 0))
        udp_port = raw.getsockname()[1]
        try:
            ready = _negotiate_udp(host, port, udp_port)
        except Exception as exc:
            raw.close()
            raise ReceiverNegotiationError(str(exc)) from exc
        self.logAppended.emit(
            f"UDP ready: {ready.get('width', '?')}x{ready.get('height', '?')}@{ready.get('fps', '?')}; "
            f"decoder: {self.decoder_label}; sink: {sink_name}"
        )
        self._launch_gst_pipeline(
            self._udp_pipeline_description(sink_name, udp_port), generation, raw
        )

    def _launch_external_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        if self.udp_transport:
            return self._launch_udp_pipeline(host, port, generation, self.sink)
        self.receiver_host = host
        self.receiver_port = port
        self.attempt_started = time.monotonic()
        args = [
            "tcpclientsrc", "name=receiver_source", f"host={json.dumps(host)}", f"port={port}", "!",
            "h264parse", "name=receiver_parser", "disable-passthrough=true", "config-interval=-1", "!",
            PARSED_H264_CAPS, "!",
            "queue", "name=receiver_compressed_queue", *COMPRESSED_QUEUE[1:], "!",
            *self._named_decoder_args(), "!",
            *self._display_chain_args(self.sink),
        ]
        self.logAppended.emit(
            f"Decoder: {self.decoder_label}; standalone sink: {self.sink}"
        )
        self._launch_gst_pipeline(" ".join(args), generation)

    def _launch_gst_pipeline(self, description, generation, raw_socket=None):
        Gst = _load_gst()
        self._stop_gst_pipeline()
        pipeline = None
        try:
            pipeline = Gst.parse_launch(description)
            if raw_socket is not None:
                source = pipeline.get_by_name("receiver_source")
                if source is None:
                    raise RuntimeError("receiver UDP source is missing")
                self.gst_socket = _GIO.Socket.new_from_fd(os.dup(raw_socket.fileno()))
                source.set_property("socket", self.gst_socket)
                source.set_property("close-socket", True)
            self.gst_pipeline = pipeline
            self.gst_generation = generation
            self.gst_bus = pipeline.get_bus()
            self._prepare_standalone_sink(pipeline)
            self._setup_stats()
            self.gst_bus_timer.start()
            result = pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer rejected the receiver pipeline")
            self.process = None
            self._started(pipeline, generation)
        except Exception:
            if pipeline is not None:
                pipeline.set_state(Gst.State.NULL)
            self.gst_pipeline = None
            self.gst_bus = None
            self.gst_generation = None
            self.gst_socket = None
            self.gst_bus_timer.stop()
            self.stats_timer.stop()
            raise
        finally:
            if raw_socket is not None:
                raw_socket.close()

    def _prepare_standalone_sink(self, pipeline):
        sink = pipeline.get_by_name("receiver_sink")
        if sink is None or _GST_VIDEO is None:
            return
        if isinstance(sink, _GST_VIDEO.VideoOverlay):
            _GST_VIDEO.VideoOverlay.set_window_handle(sink, 0)

    def _start_audio(self, generation):
        if (
            generation == self.generation
            and self.udp_transport
            and self.port == PRIMARY_STREAM_PORT
        ):
            try:
                self.audio_receiver.start(self.host)
            except Exception as exc:
                self.logAppended.emit(f"Audio receiver unavailable: {exc}")

    def _reset_stats(self):
        with self.stats_lock:
            self.stats_counts = {
                "bytes": 0,
                "packets": 0,
                "lost": 0,
                "input": 0,
                "decoded": 0,
                "sink_input": 0,
                "raw_drops": 0,
                "decode_ns": 0,
                "decode_samples": 0,
            }
            self.decode_started = {}
            self.loss_tracker = RtpLossTracker()
        self.last_stats_time = time.monotonic()
        self.last_sink_rendered = 0
        self.last_sink_dropped = 0
        self.last_snapshot = {}
        self.overlay_surface = None

    def _setup_stats(self):
        Gst = _load_gst()
        pipeline = self.gst_pipeline
        self._reset_stats()
        names = (
            "receiver_source", "receiver_parser", "receiver_decoder",
            "receiver_compressed_queue", "receiver_raw_queue",
            "receiver_stats_overlay", "receiver_sink",
        )
        self.stats_elements = {name: pipeline.get_by_name(name) for name in names}
        probes = (
            ("receiver_source", "src", self._source_probe),
            ("receiver_parser", "src", self._input_probe),
            ("receiver_decoder", "sink", self._decode_input_probe),
            ("receiver_decoder", "src", self._decode_output_probe),
            ("receiver_sink", "sink", self._sink_input_probe),
        )
        for name, pad_name, callback in probes:
            element = self.stats_elements.get(name)
            pad = element.get_static_pad(pad_name) if element is not None else None
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.BUFFER, callback)
        raw_queue = self.stats_elements.get("receiver_raw_queue")
        if raw_queue is not None:
            raw_queue.connect("overrun", self._raw_queue_overrun)
        overlay = self.stats_elements.get("receiver_stats_overlay")
        self.overlay_supported = overlay is not None
        if overlay is not None:
            try:
                import cairo
                overlay.connect("caps-changed", self._overlay_caps_changed)
                overlay.connect("draw", self._draw_overlay)
            except Exception as exc:
                self.overlay_supported = False
                self.logAppended.emit(f"Stats overlay disabled: {exc}")
        elif self.show_stats:
            self.logAppended.emit(
                "Stats overlay unavailable — install GStreamer cairooverlay."
            )
        sink = self.stats_elements.get("receiver_sink")
        rendered, dropped = self._sink_totals(sink)
        self.last_sink_rendered = rendered
        self.last_sink_dropped = dropped
        self.stats_timer.start()

    def _source_probe(self, _pad, info):
        Gst = _load_gst()
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        size = buffer.get_size()
        lost = 0
        packet = 0
        if self.udp_transport:
            success, mapped = buffer.map(Gst.MapFlags.READ)
            try:
                data = mapped.data if success else b""
                if len(data) >= 12 and data[0] >> 6 == 2:
                    packet = 1
                    sequence = int.from_bytes(data[2:4], "big")
            finally:
                if success:
                    buffer.unmap(mapped)
        with self.stats_lock:
            if packet:
                lost = self.loss_tracker.add(sequence)
            self.stats_counts["bytes"] += size
            self.stats_counts["packets"] += packet
            self.stats_counts["lost"] += lost
        return Gst.PadProbeReturn.OK

    def _input_probe(self, _pad, info):
        with self.stats_lock:
            self.stats_counts["input"] += int(info.get_buffer() is not None)
        return _GST.PadProbeReturn.OK

    def _decode_input_probe(self, _pad, info):
        buffer = info.get_buffer()
        if buffer is not None and buffer.pts != _GST.CLOCK_TIME_NONE:
            with self.stats_lock:
                self.decode_started[int(buffer.pts)] = time.monotonic_ns()
                if len(self.decode_started) > 120:
                    self.decode_started.pop(next(iter(self.decode_started)))
        return _GST.PadProbeReturn.OK

    def _decode_output_probe(self, _pad, info):
        buffer = info.get_buffer()
        now = time.monotonic_ns()
        with self.stats_lock:
            self.stats_counts["decoded"] += int(buffer is not None)
            if buffer is not None and buffer.pts != _GST.CLOCK_TIME_NONE:
                started = self.decode_started.pop(int(buffer.pts), None)
                if started is not None:
                    self.stats_counts["decode_ns"] += now - started
                    self.stats_counts["decode_samples"] += 1
        return _GST.PadProbeReturn.OK

    def _sink_input_probe(self, _pad, info):
        with self.stats_lock:
            self.stats_counts["sink_input"] += int(info.get_buffer() is not None)
        return _GST.PadProbeReturn.OK

    def _raw_queue_overrun(self, _queue):
        with self.stats_lock:
            self.stats_counts["raw_drops"] += 1

    def _overlay_caps_changed(self, _overlay, caps):
        structure = caps.get_structure(0) if caps and caps.get_size() else None
        if structure is None:
            return
        width = structure.get_value("width")
        height = structure.get_value("height")
        if isinstance(width, int) and isinstance(height, int):
            self.overlay_width, self.overlay_height = width, height

    def _draw_overlay(self, _overlay, context, _timestamp, _duration):
        surface = self.overlay_surface if self.show_stats else None
        if surface is None:
            return
        scale = max(0.75, min(2.0, self.overlay_height / 1080))
        context.set_source_surface(surface, 12 * scale, 12 * scale)
        context.paint()

    @staticmethod
    def _element_level(element):
        if element is None or element.find_property("current-level-buffers") is None:
            return 0
        return int(element.get_property("current-level-buffers"))

    @staticmethod
    def _sink_totals(sink):
        if sink is None or sink.find_property("stats") is None:
            return 0, 0
        stats = sink.get_property("stats")
        if stats is None:
            return 0, 0
        return int(stats.get_value("rendered") or 0), int(stats.get_value("dropped") or 0)

    def _stats_snapshot(self):
        now = time.monotonic()
        elapsed = max(0.001, now - self.last_stats_time)
        self.last_stats_time = now
        with self.stats_lock:
            counts = dict(self.stats_counts)
            for key in self.stats_counts:
                self.stats_counts[key] = 0
        sink = self.stats_elements.get("receiver_sink")
        has_sink_stats = sink is not None and sink.find_property("stats") is not None
        rendered_total, dropped_total = self._sink_totals(sink)
        rendered = max(0, rendered_total - self.last_sink_rendered)
        sink_drops = max(0, dropped_total - self.last_sink_dropped)
        self.last_sink_rendered = rendered_total
        self.last_sink_dropped = dropped_total
        displayed = rendered if has_sink_stats else counts["sink_input"]
        packets = counts["packets"]
        lost = counts["lost"]
        return {
            "transport": "Wi-Fi RTP/UDP" if self.udp_transport else "Local TCP",
            "rx_kbps": counts["bytes"] * 8 / elapsed / 1000,
            "pps": packets / elapsed if self.udp_transport else None,
            "loss": lost * 100 / max(1, packets + lost) if self.udp_transport else None,
            "input_fps": counts["input"] / elapsed,
            "decoded_fps": counts["decoded"] / elapsed,
            "display_fps": displayed / elapsed,
            "display_label": "display" if has_sink_stats else "output",
            "decode_ms": (
                counts["decode_ns"] / counts["decode_samples"] / 1_000_000
                if counts["decode_samples"] else None
            ),
            "compressed_q": self._element_level(
                self.stats_elements.get("receiver_compressed_queue")
            ),
            "raw_q": self._element_level(self.stats_elements.get("receiver_raw_queue")),
            "raw_drops": counts["raw_drops"],
            "sink_drops": sink_drops,
            "decoder": self.decoder_label,
            "sink": self.sink,
        }

    @staticmethod
    def _stat(value, pattern):
        return "—" if value is None else pattern.format(value)

    def _render_stats_surface(self, snapshot):
        if not self.show_stats or not self.overlay_supported or not snapshot:
            self.overlay_surface = None
            return
        import cairo

        scale = max(0.75, min(2.0, self.overlay_height / 1080))
        lines = [
            snapshot["transport"],
            "RX {} kbps · {} pps · loss {}".format(
                self._stat(snapshot["rx_kbps"], "{:.0f}"),
                self._stat(snapshot["pps"], "{:.0f}"),
                self._stat(snapshot["loss"], "{:.1f}%"),
            ),
            "frames in/dec/{} {:.1f} / {:.1f} / {:.1f} fps".format(
                snapshot["display_label"], snapshot["input_fps"],
                snapshot["decoded_fps"], snapshot["display_fps"],
            ),
            "decode {} · q compressed/raw {}/{}".format(
                self._stat(snapshot["decode_ms"], "{:.1f} ms"),
                snapshot["compressed_q"], snapshot["raw_q"],
            ),
            "drops raw/sink {}/{}".format(
                snapshot["raw_drops"], snapshot["sink_drops"]
            ),
            "{} · {}".format(snapshot["decoder"], snapshot["sink"]),
        ]
        width, height = int(450 * scale), int(132 * scale)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        context.scale(scale, scale)
        radius = 7
        card_w, card_h = width / scale, height / scale
        context.new_sub_path()
        context.arc(card_w - radius, radius, radius, -1.5708, 0)
        context.arc(card_w - radius, card_h - radius, radius, 0, 1.5708)
        context.arc(radius, card_h - radius, radius, 1.5708, 3.1416)
        context.arc(radius, radius, radius, 3.1416, 4.7124)
        context.close_path()
        context.set_source_rgba(0, 0, 0, 0.70)
        context.fill()
        context.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        context.set_font_size(12)
        context.set_source_rgba(1, 1, 1, 1)
        for index, line in enumerate(lines):
            context.move_to(10, 19 + index * 20)
            context.show_text(line)
        self.overlay_surface = surface

    def _update_stats(self):
        if self.gst_pipeline is None:
            self.stats_timer.stop()
            return
        self.last_snapshot = self._stats_snapshot()
        self._render_stats_surface(self.last_snapshot)

    def _sink_candidates(self):
        candidates = []
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            candidates.append("waylandsink")
            candidates.append("glimagesink")
        else:
            candidates.append("glimagesink")
        if session == "x11" or os.environ.get("DISPLAY"):
            candidates.extend(["xvimagesink", "ximagesink"])
        candidates.append("autovideosink")
        seen = set()
        available = []
        for sink in candidates:
            if sink in seen:
                continue
            seen.add(sink)
            if sink == "autovideosink" or gst_has_element(sink):
                available.append(sink)
        return available or ["autovideosink"]

    def _sink_args(self, sink):
        props = dict(SINK_PROPS)
        props.update(SINK_EXTRA_PROPS.get(sink, {}))
        args = [sink]
        for name, value in props.items():
            if _gst_has_property(sink, name):
                args.append(f"{name}={value}")
        return args

    def _software_decoder_args(self):
        args = ["avdec_h264"]
        for name, value in SOFTWARE_DECODER_PROPS.items():
            if _gst_has_property("avdec_h264", name):
                args.append(f"{name}={value}")
        return args

    def _hardware_decoder_args(self, decoder):
        args = [decoder]
        for name, value in HARDWARE_DECODER_PROPS.items():
            if _gst_has_property(decoder, name):
                args.append(f"{name}={value}")
        return args

    def _select_hardware_decoder(self, index):
        self.hardware_decoder_index = index
        decoder = self.hardware_decoder_candidates[index]
        self.decoder_args = self._hardware_decoder_args(decoder)
        self.decoder_label = f"VA-API {decoder}"

    def _hardware_post_decode_args(self):
        decoder = self.decoder_args[0] if self.decoder_args else ""
        preferred = (
            ("vaapipostproc", "vapostproc")
            if decoder == "vaapih264dec" else ("vapostproc", "vaapipostproc")
        )
        postproc = next((name for name in preferred if gst_has_element(name)), None)
        if postproc:
            return [postproc, "!", "video/x-raw,format=NV12", "!"]
        return ["videoconvert", "!", "video/x-raw,format=NV12", "!"]

    def _named_decoder_args(self):
        return [self.decoder_args[0], "name=receiver_decoder", *self.decoder_args[1:]]

    def _display_chain_args(self, sink_name):
        args = []
        if self.decoder == "Hardware":
            args.extend(self._hardware_post_decode_args())
        args.extend([
            "queue", "name=receiver_raw_queue", *RAW_DROP_QUEUE[1:], "!",
            "videoconvert", "!", "video/x-raw,format=BGRx", "!",
        ])
        if gst_has_element("cairooverlay"):
            args.extend(["cairooverlay", "name=receiver_stats_overlay", "!"])
        sink_args = self._sink_args(sink_name)
        args.extend([sink_args[0], "name=receiver_sink", *sink_args[1:]])
        return args

    def _try_next_hardware_pipeline(self):
        if self.decoder != "Hardware" or not self.hardware_decoder_candidates:
            return False
        if self.sink_index + 1 < len(self.sink_candidates):
            self.sink_index += 1
        elif self.hardware_decoder_index + 1 < len(self.hardware_decoder_candidates):
            self.hardware_decoder_index += 1
            self.sink_index = 0
        else:
            return False
        self.sink = self.sink_candidates[self.sink_index]
        self._select_hardware_decoder(self.hardware_decoder_index)
        self.logAppended.emit(
            f"Hardware receiver failed immediately; retrying with "
            f"{self.decoder_label}; sink: {self.sink}"
        )
        self.process = None
        self._launch_external_pipeline(self.receiver_host, self.receiver_port, self.generation)
        return True

    def _use_receiver_fallback(self):
        if self._try_next_hardware_pipeline():
            return True
        if self.pipeline_fallback_used:
            return False
        self.pipeline_fallback_used = True
        if self.decoder != "Hardware" and self.sink_index + 1 < len(self.sink_candidates):
            self.sink_index += 1
            self.sink = self.sink_candidates[self.sink_index]
        self.decoder = "Software"
        self.hardware_decoder_candidates = []
        self.hardware_decoder_index = 0
        self.decoder_args = self._software_decoder_args()
        self.decoder_label = "Software avdec_h264"
        self.logAppended.emit(
            f"Receiver pipeline failed immediately; retrying with "
            f"{self.decoder_label}; sink: {self.sink}"
        )
        self.process = None
        self._launch_external_pipeline(self.receiver_host, self.receiver_port, self.generation)
        return True

    def _started(self, process=None, generation=None):
        process = self.process if process is None else process
        generation = self.generation if generation is None else generation
        if (
            generation != self.generation
            or (process is not self.process and process is not self.gst_pipeline)
        ):
            return
        display = "Third" if self.port == 7114 else "Second"
        self._set_status(f"Waiting for {display} display stream…")
        self.stable_generation = generation
        self.stable_process = process
        self.stable_timer.start(2000)

    def _mark_stable(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.process if process is None else process
        if (
            generation != self.generation
            or (process is not self.process and process is not self.gst_pipeline)
        ):
            return
        running = (
            process and (
                process is self.gst_pipeline
                or process.state() == QProcess.ProcessState.Running
            )
        )
        if running:
            self._inhibit_sleep()
            self.retry_count = 0
            self.stable = True
            self._set_receiving(True)
            self._set_status(f"Receiving from {self.host}:{self.port}")
            self.logAppended.emit("Stream connected in fullscreen receiver.")

    def _poll_gst_bus(self):
        if self.gst_pipeline is None or self.gst_bus is None:
            self.gst_bus_timer.stop()
            return
        Gst = _load_gst()
        while True:
            message = self.gst_bus.pop()
            if message is None:
                break
            generation = self.gst_generation
            if generation != self.generation:
                continue
            if message.type == Gst.MessageType.STATE_CHANGED:
                if message.src == self.gst_pipeline:
                    _old, new, _pending = message.parse_state_changed()
                    if new == Gst.State.PLAYING:
                        self._start_audio(generation)
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self.logAppended.emit(f"Receiver pipeline error: {error.message}")
                if debug:
                    self.logAppended.emit(debug)
                self._gst_finished(1, generation)
                break
            if message.type == Gst.MessageType.EOS:
                self._gst_finished(0, generation)
                break

    def _gst_finished(self, code, generation):
        pipeline = self.gst_pipeline
        if pipeline is None or generation != self.generation:
            return
        self._stop_gst_pipeline()
        self._handle_finished(code, generation)

    def _read_pipeline(self, process=None, generation=None):
        process = self.process if process is None else process
        generation = self.generation if generation is None else generation
        if generation != self.generation or process is not self.process:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.logAppended.emit(raw)
        if "ERROR" in raw:
            self._set_status("Error — see logs")

    def _pipeline_error(self, process=None, generation=None):
        process = self.process if process is None else process
        generation = self.generation if generation is None else generation
        if generation != self.generation or process is not self.process:
            return
        self._set_status(process.errorString())

    def _finished(self, code, _status, process=None, generation=None):
        process = self.process if process is None else process
        generation = self.generation if generation is None else generation
        if (
            generation != self.generation
            or (process is not self.process and process is not self.gst_pipeline)
        ):
            return
        self._handle_finished(code, generation)

    def _handle_finished(self, code, generation):
        self.logAppended.emit(f"Receiver process exited (code {code})")
        self.stable_timer.stop()
        elapsed = time.monotonic() - self.attempt_started
        if (
            code
            and not self.stopping
            and not self.stable
            and elapsed < 2
            and self._use_receiver_fallback()
        ):
            return
        max_retries = 30 if self.stable else 10
        if (
            not self.stopping
            and (self.stable or elapsed < 2)
            and self.retry_count < max_retries - 1
        ):
            self.retry_count += 1
            self.retry_pending = True
            display = "Third" if self.port == 7114 else "Second"
            self._set_status(
                f"Waiting for {display} display stream… "
                f"({self.retry_count}/{max_retries})"
            )
            self.process = None
            self.retry_generation = generation
            self.retry_timer.start(1000)
            return
        if self.stable:
            self._set_status("Disconnected")
            self.logAppended.emit("Stream ended. Click Disconnect to return.")
        else:
            self._set_status("Unable to start stream after 10 attempts")
            self.audio_receiver.stop()
            self._set_receiving(False)

    def stop(self):
        self.generation += 1
        self.stopping = True
        self.stable = False
        self.stable_timer.stop()
        self.retry_timer.stop()
        self.audio_receiver.stop()
        self._stop_gst_pipeline()
        stop_processes(self.process)
        self.process = None
        self.retry_pending = False
        self.pipeline_fallback_used = False
        self.stable_generation = self.stable_process = self.retry_generation = None
        kill_patterns("gst-launch-1.0.*tcpclientsrc", "gst-launch-1.0.*udpsrc")
        self._uninhibit_sleep()
        self._set_receiving(False)

    def _stop_gst_pipeline(self):
        self.gst_bus_timer.stop()
        self.stats_timer.stop()
        pipeline = self.gst_pipeline
        self.gst_pipeline = None
        self.gst_bus = None
        self.gst_generation = None
        self.gst_socket = None
        self.stats_elements = {}
        self.overlay_surface = None
        self.overlay_supported = False
        if pipeline is None:
            return
        try:
            Gst = _load_gst()
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

    def _inhibit_sleep(self):
        try:
            if self.de == "kde":
                result = subprocess.run([
                    "dbus-send", "--session", "--print-reply",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.Inhibit",
                    "string:Monitorize",
                    "string:Streaming display receiver active",
                ], capture_output=True, text=True)
                line = next((line for line in result.stdout.splitlines() if "uint32" in line), "")
                if line:
                    self.inhibit_cookie = int(line.split("uint32")[-1].strip())
            elif self.de == "hyprland":
                subprocess.run(["pkill", "-USR1", "hypridle"], capture_output=True)
        except Exception as exc:
            print(f"[Receiver] Failed to inhibit sleep: {exc}")

    def _uninhibit_sleep(self):
        try:
            if self.de == "kde" and self.inhibit_cookie is not None:
                subprocess.run([
                    "dbus-send", "--session",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.UnInhibit",
                    f"uint32:{self.inhibit_cookie}",
                ], capture_output=True)
                self.inhibit_cookie = None
            elif self.de == "hyprland":
                subprocess.run(["pkill", "-USR2", "hypridle"], capture_output=True)
        except Exception as exc:
            print(f"[Receiver] Failed to uninhibit sleep: {exc}")
