"""Desktop stream receiver lifecycle."""

import os
import json
import socket
import subprocess
import sys
import time
from functools import lru_cache

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from monitorize.platform.process_utils import gst_has_element, kill_patterns, stop_processes
from monitorize.platform.utils import LINUX_DIR
from monitorize.config.validation import normalize_host, sanitize_decoder, sanitize_port, valid_host, valid_port


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

_GST = None
_GST_VIDEO = None
_GIO = None
_GST_IMPORT_ERROR = None


def _load_gst():
    global _GST, _GST_VIDEO, _GIO, _GST_IMPORT_ERROR
    if _GST is not None:
        return _GST
    if _GST_IMPORT_ERROR is not None:
        raise _GST_IMPORT_ERROR
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        gi.require_version("Gio", "2.0")
        from gi.repository import Gst
        from gi.repository import GstVideo
        from gi.repository import Gio

        Gst.init(None)
        _GST = Gst
        _GST_VIDEO = GstVideo
        _GIO = Gio
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
        self.udp_transport = False
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

    def _set_receiving(self, value):
        self.receiving = value
        self.receivingChanged.emit(value)

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def connect(self, host, port, decoder):
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
        self.decoder = decoder
        self.sink_candidates = self._sink_candidates()
        self.sink_index = 0
        self.sink = self.sink_candidates[0]
        self.pipeline_fallback_used = False
        if decoder == "Hardware":
            hardware = next((
                name for name in ("vah264dec", "vaapih264dec")
                if gst_has_element(name)
            ), None)
            if not hardware:
                self._set_status(
                    "Hardware decoder unavailable — install the GStreamer VA-API decoder"
                )
                self.logAppended.emit(
                    "ERROR: Hardware mode requires vah264dec or vaapih264dec."
                )
                return
            self.decoder_args = [hardware]
            self.decoder_label = f"VA-API {hardware}"
        else:
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

    def _start_attempt(self, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        self.retry_pending = False
        self._launch_pipeline(self.host, self.port, generation)

    def _launch_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        self._launch_external_pipeline(host, port, generation)

    def _udp_pipeline_args(self, sink_name, udp_port):
        sink_args = self._sink_args(sink_name)
        args = [
            "-e",
            "udpsrc", f"port={udp_port}", "buffer-size=524288",
            'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000', "!",
            "rtph264depay", "!", "h264parse", "disable-passthrough=true", "config-interval=-1", "!",
            PARSED_H264_CAPS, "!",
            *COMPRESSED_QUEUE, "!",
            *self.decoder_args, "!",
            *RAW_DROP_QUEUE, "!",
            "videoconvert", "!",
            *sink_args,
        ]
        return args

    def _launch_udp_pipeline(self, host, port, generation, sink_name):
        self.receiver_host = host
        self.receiver_port = port
        self.attempt_started = time.monotonic()
        self._stop_gst_pipeline()
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(("", 0))
        udp_port = raw.getsockname()[1]
        try:
            ready = _negotiate_udp(host, port, udp_port)
        except Exception:
            raw.close()
            raise
        # Close the Python socket so gst-launch-1.0 can bind to the same port.
        # The streamer is already sending to this port, so packets queue in
        # the kernel until gst-launch-1.0 re-binds.
        raw.close()
        self.process = QProcess(self)
        process = self.process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.started.connect(lambda: self._started(process, generation))
        process.readyReadStandardOutput.connect(
            lambda: self._read_pipeline(process, generation)
        )
        process.finished.connect(
            lambda code, status: self._finished(code, status, process, generation)
        )
        process.errorOccurred.connect(
            lambda _error: self._pipeline_error(process, generation)
        )
        args = self._udp_pipeline_args(sink_name, udp_port)
        self.logAppended.emit(
            f"UDP ready: {ready.get('width', '?')}x{ready.get('height', '?')}@{ready.get('fps', '?')}; "
            f"decoder: {self.decoder_label}; sink: {sink_name}"
        )
        self.process.start("gst-launch-1.0", args)

    def _launch_external_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        if self.udp_transport:
            return self._launch_udp_pipeline(host, port, generation, self.sink)
        self.receiver_host = host
        self.receiver_port = port
        self.attempt_started = time.monotonic()
        self.process = QProcess(self)
        process = self.process
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.started.connect(lambda: self._started(process, generation))
        self.process.readyReadStandardOutput.connect(
            lambda: self._read_pipeline(process, generation)
        )
        self.process.finished.connect(
            lambda code, status: self._finished(code, status, process, generation)
        )
        self.process.errorOccurred.connect(
            lambda _error: self._pipeline_error(process, generation)
        )
        args = [
            "-e", "tcpclientsrc", f"host={host}", f"port={port}", "!",
            "h264parse", "disable-passthrough=true", "config-interval=-1", "!",
            PARSED_H264_CAPS, "!",
            *COMPRESSED_QUEUE, "!",
            *self.decoder_args, "!",
            *RAW_DROP_QUEUE, "!",
            "videoconvert", "!",
            *self._sink_args(self.sink),
        ]
        self.logAppended.emit(
            f"Decoder: {self.decoder_label}; standalone sink: {self.sink}"
        )
        self.process.start("gst-launch-1.0", args)

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

    def _use_receiver_fallback(self):
        if self.pipeline_fallback_used:
            return False
        self.pipeline_fallback_used = True
        if self.sink_index + 1 < len(self.sink_candidates):
            self.sink_index += 1
            self.sink = self.sink_candidates[self.sink_index]
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
            if message.type == Gst.MessageType.NEW_CLOCK and not self.stable:
                self._mark_stable(generation, self.gst_pipeline)

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
            self._set_receiving(False)

    def stop(self):
        self.generation += 1
        self.stopping = True
        self.stable = False
        self.stable_timer.stop()
        self.retry_timer.stop()
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
        pipeline = self.gst_pipeline
        self.gst_pipeline = None
        self.gst_bus = None
        self.gst_generation = None
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
