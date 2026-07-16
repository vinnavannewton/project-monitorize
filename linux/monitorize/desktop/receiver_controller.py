"""Desktop stream receiver lifecycle."""

import os
import subprocess
import sys
import time
from functools import lru_cache

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from monitorize.platform.process_utils import gst_has_element, kill_patterns, stop_processes
from monitorize.config.settings import (
    clear_receiver_credentials,
    load_receiver_credentials,
    save_receiver_credentials,
)
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
VA_MEMORY_CAPS = "video/x-raw(memory:VAMemory)"
VA_SURFACE_CAPS = "video/x-raw(memory:VASurface)"
DMABUF_CAPS = "video/x-raw(memory:DMABuf),format=DMA_DRM"
MIN_EMBEDDED_SURFACE_SIZE = 128
RESIZE_RESTART_THRESHOLD = 64
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
    "vaapisink": {"fullscreen": "true"},
    "xvimagesink": {"double-buffer": "true", "draw-borders": "true"},
}
EMBEDDED_X11_SINKS = ("xvimagesink", "ximagesink", "glimagesink")
EMBEDDED_WAYLAND_SINKS = ("waylandsink", "glimagesink")
WINDOWS_EMBEDDED_SINKS = ("d3d11videosink", "autovideosink")
SOFTWARE_DECODER_PROPS = {
    "max-threads": "2",
    "thread-type": "slice",
    "output-corrupt": "false",
    "discard-corrupted-frames": "true",
    "automatic-request-sync-points": "true",
}
HARDWARE_DECODER_PROPS = {
    "qos": "true",
    "discard-corrupted-frames": "true",
}

_GST = None
_GST_VIDEO = None
_GST_IMPORT_ERROR = None


def _load_gst():
    global _GST, _GST_VIDEO, _GST_IMPORT_ERROR
    if _GST is not None:
        return _GST
    if _GST_IMPORT_ERROR is not None:
        raise _GST_IMPORT_ERROR
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst
        from gi.repository import GstVideo

        Gst.init(None)
        _GST = Gst
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


class ReceiverController(QObject):
    receivingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    hostChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str)
    pairingRequired = pyqtSignal(str, int, str)

    def __init__(self, de, discovery, parent=None):
        super().__init__(parent)
        self.de = de
        self.discovery = discovery
        self.receiving = False
        self.status = ""
        self.host_label = ""
        self.process = None
        self.tls_process = None
        self.tls_buffer = ""
        self.auth_failed = False
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
        self.video_item = None
        self.pending_launch = None
        self.gst_pipeline = None
        self.gst_bus = None
        self.gst_generation = None
        self.gst_video_sink = None
        self.bad_geometry_logged = False
        self.receiver_surface_width = 0
        self.receiver_surface_height = 0
        self.embedded_pipeline_size = None
        self.resize_restart_used = False
        self.embedded_sink = None
        self.sink_candidates = []
        self.sink_index = 0
        self.hardware_profiles = []
        self.hardware_profile_index = 0
        self.hardware_path = ""
        self.hardware_sink_props = {}
        self.windows_profiles = []
        self.windows_profile_index = 0
        self.stable_timer = QTimer(self)
        self.stable_timer.setSingleShot(True)
        self.stable_timer.timeout.connect(
            lambda: self._mark_stable(self.stable_generation, self.stable_process)
        )
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(lambda: self._start_attempt(self.retry_generation))
        self.surface_timer = QTimer(self)
        self.surface_timer.setSingleShot(True)
        self.surface_timer.timeout.connect(self._surface_wait_expired)
        self.gst_bus_timer = QTimer(self)
        self.gst_bus_timer.setInterval(50)
        self.gst_bus_timer.timeout.connect(self._poll_gst_bus)
        self.resize_restart_timer = QTimer(self)
        self.resize_restart_timer.setSingleShot(True)
        self.resize_restart_timer.timeout.connect(
            lambda: self._restart_embedded_for_resize(self.generation)
        )

    @staticmethod
    def _is_windows():
        return sys.platform.startswith("win")

    def _windows_receiver_profiles(self, decoder):
        profiles = []
        d3d11_sink = "d3d11videosink" if gst_has_element("d3d11videosink") else ""
        if (
            decoder == "Hardware"
            and d3d11_sink
            and gst_has_element("d3d11h264dec")
        ):
            profiles.append((
                ["d3d11h264dec"],
                "D3D11 d3d11h264dec",
                d3d11_sink,
            ))
        software = self._software_decoder_args()
        if d3d11_sink:
            profiles.append((software, "Software avdec_h264", d3d11_sink))
        profiles.append((software, "Software avdec_h264", "autovideosink"))

        unique = []
        seen = set()
        for decoder_args, decoder_label, sink in profiles:
            key = (tuple(decoder_args), sink)
            if key in seen:
                continue
            seen.add(key)
            unique.append((decoder_args, decoder_label, sink))
        return unique

    def _apply_windows_receiver_profile(self):
        decoder_args, decoder_label, sink = self.windows_profiles[
            self.windows_profile_index
        ]
        self.decoder_args = list(decoder_args)
        self.decoder_label = decoder_label
        self.sink = sink

    def _hardware_receiver_profiles(self):
        elements = {
            name: gst_has_element(name)
            for name in (
                "vah264dec", "vapostproc", "waylandsink", "glimagesink",
                "vaapih264dec", "vaapisink",
            )
        }
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        wayland = session == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        x11 = session == "x11" or bool(os.environ.get("DISPLAY"))
        profiles = []

        if elements["vah264dec"] and elements["vapostproc"]:
            sinks = ("waylandsink", "glimagesink") if wayland else ("glimagesink",)
            if wayland or x11:
                for sink in sinks:
                    if elements[sink]:
                        profiles.append((
                            self._hardware_decoder_args("vah264dec"),
                            "VA-API vah264dec (VAMemory → DMABuf)",
                            sink,
                            "dmabuf",
                            {},
                        ))

        display = "wayland" if wayland else "x11" if x11 else ""
        if display and elements["vaapih264dec"] and elements["vaapisink"]:
            profiles.append((
                self._hardware_decoder_args("vaapih264dec"),
                "VA-API vaapih264dec (VASurface)",
                "vaapisink",
                "vasurface",
                {"display": display},
            ))
        return profiles

    def _apply_hardware_receiver_profile(self):
        decoder_args, decoder_label, sink, path, sink_props = self.hardware_profiles[
            self.hardware_profile_index
        ]
        self.decoder_args = list(decoder_args)
        self.decoder_label = decoder_label
        self.sink = sink
        self.hardware_path = path
        self.hardware_sink_props = dict(sink_props)

    def _set_receiving(self, value):
        self.receiving = value
        self.receivingChanged.emit(value)

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def connect(self, host, port, encrypted, fingerprint, pairing_code, decoder):
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
        self.encrypted = encrypted
        self.fingerprint = fingerprint
        self.pairing_code = pairing_code
        self.decoder = decoder
        self.sink_candidates = self._sink_candidates()
        self.sink_index = 0
        self.sink = self.sink_candidates[0]
        self.pipeline_fallback_used = False
        if self._is_windows():
            self.windows_profiles = self._windows_receiver_profiles(decoder)
            self.windows_profile_index = 0
            if not self.windows_profiles:
                self._fail_windows_embedded(
                    "Missing GStreamer receiver plugins. Install the MSYS2 UCRT64 "
                    "GStreamer base/good/bad/libav packages."
                )
                return
            self._apply_windows_receiver_profile()
        elif decoder == "Hardware":
            self.hardware_profiles = self._hardware_receiver_profiles()
            self.hardware_profile_index = 0
            if not self.hardware_profiles:
                self._set_status(
                    "Hardware zero-copy unavailable — install GStreamer VA/DMABuf plugins"
                )
                self.logAppended.emit(
                    "ERROR: Hardware mode requires a complete zero-copy profile: "
                    "vah264dec + vapostproc + waylandsink/glimagesink, or "
                    "vaapih264dec + vaapisink."
                )
                return
            self._apply_hardware_receiver_profile()
        else:
            self.decoder_args = self._software_decoder_args()
            self.decoder_label = "Software avdec_h264"
        self.retry_count = 0
        self.retry_pending = False
        self.auth_failed = False
        self.resize_restart_used = False
        self.host_label = f"{host}:{port}"
        self.hostChanged.emit(self.host_label)
        if not encrypted:
            self._set_receiving(True)
        self._set_status(f"Connecting to {host}:{port}…")
        self.logAppended.emit(f"Connecting to {host} on port {port}…")
        self._start_attempt(generation)

    def set_video_item(self, item):
        self.video_item = item
        if item is None:
            self.gst_video_sink = None
            self.receiver_surface_width = 0
            self.receiver_surface_height = 0
            self.resize_restart_timer.stop()
            return
        ready = self._update_receiver_surface_size()
        self.sync_video_geometry()
        if self.pending_launch is None:
            return
        if ready and self._launch_pending_if_surface_ready():
            return
        if not self.surface_timer.isActive():
            self.surface_timer.start(1500)

    def _launch_pending_if_surface_ready(self):
        if self.pending_launch is None or not self._receiver_surface_ready():
            return False
        host, port, generation = self.pending_launch
        self.pending_launch = None
        self.surface_timer.stop()
        self._launch_pipeline(host, port, generation)
        return True

    def _wait_for_pending_surface_size(self):
        if self.pending_launch is not None:
            if not self.surface_timer.isActive():
                self.surface_timer.start(1500)

    def _start_attempt(self, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        self.retry_pending = False
        self.auth_failed = False
        if not self.encrypted:
            self._launch_pipeline(self.host, self.port, generation)
            return
        fingerprint, token = load_receiver_credentials(self.host)
        if self.pairing_code:
            fingerprint, token = self.fingerprint, ""
        self.tls_process = QProcess(self)
        process = self.tls_process
        self.tls_process.setWorkingDirectory(LINUX_DIR)
        self.tls_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.tls_process.readyReadStandardOutput.connect(
            lambda: self._read_tls(process, generation)
        )
        self.tls_process.finished.connect(
            lambda code, status: self._tls_finished(code, status, process, generation)
        )
        args = ["-m", "monitorize.security.tls_receiver", self.host, str(self.port)]
        if fingerprint:
            args += ["--fingerprint", fingerprint]
        if token:
            args += ["--token", token]
        elif self.pairing_code:
            args += ["--code", self.pairing_code]
        self.tls_process.start(sys.executable, args)

    def _launch_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
        if self._is_windows() and not self._embedded_sink_available():
            self._fail_windows_embedded(
                "Missing GStreamer in-app video sink. Install the MSYS2 UCRT64 "
                "GStreamer plugins that provide d3d11videosink."
            )
            return
        if self.video_item is not None and self.should_use_embedded_window():
            if not self._update_receiver_surface_size():
                self.pending_launch = (host, port, generation)
                self._set_status("Preparing fullscreen receiver…")
                self.logAppended.emit("Waiting for fullscreen receiver surface size…")
                self.surface_timer.start(1500)
                return
            try:
                self._launch_embedded_pipeline(host, port, generation)
                return
            except Exception as exc:
                if self._is_windows():
                    self.logAppended.emit(
                        f"Windows in-app receiver profile failed: {exc}"
                    )
                    if self._retry_windows_embedded_fallback():
                        return
                    self._fail_windows_embedded(exc)
                    return
                self.logAppended.emit(
                    f"Embedded receiver failed; falling back to player window: {exc}"
                )
                self._stop_gst_pipeline()
        if self.video_item is None and self._should_wait_for_embedded_surface():
            self.pending_launch = (host, port, generation)
            self._set_status("Preparing fullscreen receiver…")
            self.logAppended.emit("Preparing fullscreen receiver surface…")
            self.surface_timer.start(1500)
            return
        if self._is_windows():
            self._fail_windows_embedded(
                "Windows receiver requires the app-owned fullscreen video surface."
            )
            return
        self._launch_external_pipeline(host, port, generation)

    def _surface_wait_expired(self):
        if self.pending_launch is None:
            return
        host, port, generation = self.pending_launch
        self.pending_launch = None
        if self.stopping or generation != self.generation:
            return
        if self._is_windows():
            self._fail_windows_embedded(
                "Fullscreen receiver surface was not ready; external fallback is "
                "disabled on Windows."
            )
            return
        self.logAppended.emit(
            "Fullscreen receiver surface was not ready; using fallback player window."
        )
        self._launch_external_pipeline(host, port, generation)

    def _embedded_sink_available(self):
        if self._uses_linux_hardware_profile() and getattr(self, "sink", ""):
            return gst_has_element(self.sink)
        return self._embedded_sink_name() is not None

    def _active_embedded_sink_name(self):
        if self._is_windows() or self._uses_linux_hardware_profile():
            return self.sink
        return self._embedded_sink_name()

    def _embedded_sink_name(self):
        if self.embedded_sink:
            return self.embedded_sink
        for name in self._embedded_sink_candidates():
            if gst_has_element(name):
                self.embedded_sink = name
                return name
        return None

    def _embedded_sink_candidates(self):
        if self._is_windows():
            return WINDOWS_EMBEDDED_SINKS
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            return EMBEDDED_WAYLAND_SINKS
        if session == "x11" or os.environ.get("DISPLAY"):
            return EMBEDDED_X11_SINKS
        return ("glimagesink",)

    def _should_wait_for_embedded_surface(self):
        app = QGuiApplication.instance()
        return isinstance(app, QGuiApplication) and self.should_use_embedded_window()

    def should_use_embedded_window(self):
        if self._is_windows():
            return self._embedded_sink_available()
        override = os.environ.get("MONITORIZE_RECEIVER_EMBEDDED", "").strip().lower()
        if override in {"1", "true", "yes", "on"}:
            return self._embedded_sink_available()
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        wayland = session == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        if wayland:
            return False
        return self._embedded_sink_available()

    def _launch_embedded_pipeline(self, host, port, generation):
        self.receiver_host = host
        self.receiver_port = port
        self.attempt_started = time.monotonic()
        self._stop_gst_pipeline()
        self.bad_geometry_logged = False
        Gst = _load_gst()
        sink_name = self._active_embedded_sink_name()
        if not sink_name:
            raise RuntimeError("no embeddable GStreamer video sink is available")
        description = self._embedded_pipeline_description(host, port, sink_name)
        pipeline = Gst.parse_launch(description)
        sink = pipeline.get_by_name("receiver_sink")
        if sink is None:
            raise RuntimeError("embedded receiver sink was not created")
        self._bind_embedded_sink(sink)
        self.gst_video_sink = sink
        bus = pipeline.get_bus()
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer refused to start embedded receiver pipeline")
        self.gst_pipeline = pipeline
        self.gst_bus = bus
        self.gst_generation = generation
        self.embedded_pipeline_size = (
            self.receiver_surface_width,
            self.receiver_surface_height,
        )
        self.gst_bus_timer.start()
        self.logAppended.emit(
            f"Decoder: {self.decoder_label}; embedded sink: {sink_name}; "
            f"scaled to {self.receiver_surface_width}x{self.receiver_surface_height}"
        )
        self._started(pipeline, generation)

    def _embedded_pipeline_description(self, host, port, sink_name=None):
        sink_name = sink_name or self._embedded_sink_name() or "glimagesink"
        sink_args = self._sink_args(sink_name, include_extra=False)
        sink_args[0] = sink_name
        sink_args.append("name=receiver_sink")
        parts = [
            "tcpclientsrc", f"host={host}", f"port={port}", "!",
            "h264parse", "disable-passthrough=true", "config-interval=-1", "!",
            PARSED_H264_CAPS, "!",
            *COMPRESSED_QUEUE, "!",
            *self.decoder_args, "!",
            *self._decoded_output_args(embedded=True), "!",
            *sink_args,
        ]
        return " ".join(parts)

    def _bind_embedded_sink(self, sink):
        if self.video_item is None:
            raise RuntimeError("receiver video surface is not available")
        handle = int(self.video_item.winId())
        if hasattr(sink, "set_window_handle"):
            sink.set_window_handle(handle)
        else:
            _load_gst()
            if _GST_VIDEO is None:
                raise RuntimeError("GstVideo overlay support is unavailable")
            _GST_VIDEO.VideoOverlay.set_window_handle(sink, handle)
        if hasattr(sink, "handle_events"):
            sink.handle_events(True)
        self._sync_embedded_sink_geometry(sink)

    def sync_video_geometry(self):
        self._update_receiver_surface_size()
        self._sync_embedded_sink_geometry(self.gst_video_sink)
        self._schedule_embedded_resize_restart()
        if not self._launch_pending_if_surface_ready():
            self._wait_for_pending_surface_size()

    def _sync_embedded_sink_geometry(self, sink):
        if sink is None or self.video_item is None:
            return
        self._update_receiver_surface_size()
        width, height = self._receiver_surface_size()
        if (width <= 64 or height <= 64) and not self.bad_geometry_logged:
            self.bad_geometry_logged = True
            self.logAppended.emit(
                f"Receiver video surface is not fullscreen-sized yet: {width}x{height}"
            )
        if hasattr(sink, "set_render_rectangle"):
            sink.set_render_rectangle(0, 0, width, height)
        if hasattr(sink, "expose"):
            sink.expose()

    def _receiver_surface_size(self):
        return (
            max(1, int(self.receiver_surface_width)),
            max(1, int(self.receiver_surface_height)),
        )

    def _update_receiver_surface_size(self):
        if self.video_item is None:
            return False
        try:
            width = max(1, int(self.video_item.width()))
            height = max(1, int(self.video_item.height()))
        except (TypeError, ValueError):
            width = height = 0
        self.receiver_surface_width = width
        self.receiver_surface_height = height
        return self._receiver_surface_ready(width, height)

    def _receiver_surface_ready(self, width=None, height=None):
        width = self.receiver_surface_width if width is None else width
        height = self.receiver_surface_height if height is None else height
        return width >= MIN_EMBEDDED_SURFACE_SIZE and height >= MIN_EMBEDDED_SURFACE_SIZE

    def _schedule_embedded_resize_restart(self):
        if (
            self.gst_pipeline is None
            or self.video_item is None
            or self.embedded_pipeline_size is None
            or self.resize_restart_used
            or self.stopping
            or not self._receiver_surface_ready()
        ):
            return
        width, height = self._receiver_surface_size()
        old_width, old_height = self.embedded_pipeline_size
        changed = (
            abs(width - old_width) >= RESIZE_RESTART_THRESHOLD
            or abs(height - old_height) >= RESIZE_RESTART_THRESHOLD
        )
        if changed and not self.resize_restart_timer.isActive():
            self.resize_restart_timer.start(150)

    def _restart_embedded_for_resize(self, generation=None):
        generation = self.generation if generation is None else generation
        if (
            generation != self.generation
            or self.stopping
            or self.gst_pipeline is None
            or self.video_item is None
            or self.resize_restart_used
            or not self._receiver_surface_ready()
        ):
            return
        self.resize_restart_used = True
        self.logAppended.emit(
            "Receiver surface size changed; restarting embedded receiver pipeline."
        )
        self._launch_embedded_pipeline(self.receiver_host, self.receiver_port, generation)

    def _launch_external_pipeline(self, host, port, generation=None):
        generation = self.generation if generation is None else generation
        if self.stopping or generation != self.generation:
            return
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
            *self._decoded_output_args(), "!",
            *self._sink_args(self.sink),
        ]
        if self.video_item is not None and not self.should_use_embedded_window():
            self.logAppended.emit(
                "Using external fullscreen receiver sink on Wayland; "
                "embedded receiver can render tiny or crash there."
            )
        if self._uses_linux_hardware_profile():
            self.logAppended.emit(
                f"Decoder: {self.decoder_label}; zero-copy path: "
                f"{self.hardware_path}; sink: {self.sink}"
            )
        else:
            self.logAppended.emit(f"Decoder: {self.decoder_label}; sink: {self.sink}")
        self.process.start("gst-launch-1.0", args)

    def _uses_linux_hardware_profile(self):
        return not self._is_windows() and getattr(self, "decoder", "") == "Hardware"

    def _decoded_output_args(self, embedded=False):
        if self._uses_linux_hardware_profile():
            if self.hardware_path == "dmabuf":
                caps = DMABUF_CAPS
                if embedded:
                    width, height = self._receiver_surface_size()
                    caps += (
                        f",width={width},height={height},pixel-aspect-ratio=1/1"
                    )
                return [
                    VA_MEMORY_CAPS, "!", *RAW_DROP_QUEUE, "!",
                    "vapostproc", "!", caps,
                ]
            return [VA_SURFACE_CAPS, "!", *RAW_DROP_QUEUE]

        output = [*RAW_DROP_QUEUE, "!", "videoconvert"]
        if embedded:
            width, height = self._receiver_surface_size()
            output += [
                "!", "videoscale", "add-borders=false", "!",
                f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1",
                "!", "videoconvert",
            ]
        return output

    def _sink_candidates(self):
        if self._is_windows():
            available = [
                sink for sink in WINDOWS_EMBEDDED_SINKS
                if sink == "autovideosink" or gst_has_element(sink)
            ]
            return available or ["autovideosink"]
        candidates = []
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            if self._prefer_windowless_external_sink():
                candidates.append("waylandsink")
            candidates.append("glimagesink")
            if not self._prefer_windowless_external_sink():
                candidates.append("waylandsink")
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

    def _prefer_windowless_external_sink(self):
        app = QGuiApplication.instance()
        return isinstance(app, QGuiApplication)

    def _sink_args(self, sink, include_extra=True):
        props = dict(SINK_PROPS)
        if include_extra:
            props.update(SINK_EXTRA_PROPS.get(sink, {}))
        if self._uses_linux_hardware_profile() and sink == self.sink:
            props.update(self.hardware_sink_props)
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
        props = dict(HARDWARE_DECODER_PROPS)
        if decoder == "vaapih264dec":
            props["low-latency"] = "true"
        args = [decoder]
        for name, value in props.items():
            if _gst_has_property(decoder, name):
                args.append(f"{name}={value}")
        return args

    def _retry_windows_embedded_fallback(self):
        if not self._is_windows():
            return False
        while self.windows_profile_index + 1 < len(self.windows_profiles):
            self.windows_profile_index += 1
            self._apply_windows_receiver_profile()
            self.logAppended.emit(
                f"Retrying Windows in-app receiver with "
                f"{self.decoder_label}; sink: {self.sink}"
            )
            try:
                self._launch_embedded_pipeline(
                    self.receiver_host, self.receiver_port, self.generation
                )
                return True
            except Exception as exc:
                self.logAppended.emit(
                    f"Windows in-app receiver profile failed: {exc}"
                )
                self._stop_gst_pipeline()
        return False

    def _fail_windows_embedded(self, error):
        message = (
            "Windows in-app receiver unavailable. Install the MSYS2 UCRT64 "
            "GStreamer runtime and plugins, including d3d11videosink and "
            "gst-libav. Last error: "
            f"{error}"
        )
        self.logAppended.emit(f"ERROR: {message}")
        self._set_status("Windows in-app receiver unavailable")
        self._stop_gst_pipeline()
        self._set_receiving(False)

    def _use_receiver_fallback(self):
        if self._is_windows():
            return self._retry_windows_embedded_fallback()
        if self.decoder == "Hardware":
            if self.hardware_profile_index + 1 >= len(self.hardware_profiles):
                return False
            self.hardware_profile_index += 1
            self._apply_hardware_receiver_profile()
            self.logAppended.emit(
                f"Hardware zero-copy profile failed; retrying with "
                f"{self.decoder_label}; path: {self.hardware_path}; sink: {self.sink}"
            )
            self.process = None
            self._launch_external_pipeline(
                self.receiver_host, self.receiver_port, self.generation
            )
            return True
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
        if generation != self.generation or process is not self.process:
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
                self._embedded_finished(1, generation)
                break
            if message.type == Gst.MessageType.EOS:
                self._embedded_finished(0, generation)
                break
            if message.type == Gst.MessageType.NEW_CLOCK and not self.stable:
                self._mark_stable(generation, self.gst_pipeline)

    def _embedded_finished(self, code, generation):
        pipeline = self.gst_pipeline
        self._finished(code, None, pipeline, generation)
        self._stop_gst_pipeline()

    def _read_tls(self, process=None, generation=None):
        process = self.tls_process if process is None else process
        generation = self.generation if generation is None else generation
        if generation != self.generation or process is not self.tls_process:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.tls_buffer += raw
        lines = self.tls_buffer.split("\n")
        self.tls_buffer = lines.pop() if not self.tls_buffer.endswith("\n") else ""
        for line in lines:
            if line == "[TLS RECEIVER] READY" and self.process is None:
                self._set_receiving(True)
                self._launch_pipeline("127.0.0.1", 17110, generation)
            elif line.startswith("[TLS RECEIVER] CREDENTIALS "):
                fingerprint, token = line.removeprefix(
                    "[TLS RECEIVER] CREDENTIALS "
                ).split()
                save_receiver_credentials(self.host, fingerprint, token)
                self.pairing_code = ""
                self._set_status("Authenticated; starting encrypted stream…")
            elif line.startswith("[TLS RECEIVER] AUTH_FAILED"):
                self.auth_failed = True
                fingerprint = line.removeprefix("[TLS RECEIVER] AUTH_FAILED").strip()
                clear_receiver_credentials(self.host)
                self._set_status("Pairing required")
                self.pairingRequired.emit(self.host, self.port, fingerprint)
            elif line.startswith("[TLS RECEIVER] ERROR "):
                self._set_status(line.removeprefix("[TLS RECEIVER] ERROR "))
            elif line:
                self.logAppended.emit(line)

    def _tls_finished(self, code, _status, process=None, generation=None):
        process = self.tls_process if process is None else process
        generation = self.generation if generation is None else generation
        if generation != self.generation or process is not self.tls_process:
            return
        if code and not self.auth_failed and not self.receiving and not self.retry_pending:
            self._set_status("Encrypted connection failed")

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
        self.logAppended.emit(f"Receiver process exited (code {code})")
        self.stable_timer.stop()
        elapsed = time.monotonic() - self.attempt_started
        if code and not self.stopping and not self.stable and elapsed < 2:
            if self._use_receiver_fallback():
                return
            if self.decoder == "Hardware":
                self.logAppended.emit(
                    "ERROR: Hardware zero-copy receiver failed with every available profile."
                )
                self._set_status("Hardware zero-copy receiver failed — see logs")
                self._set_receiving(False)
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
            stop_processes(self.tls_process)
            self.process = self.tls_process = None
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
        self.surface_timer.stop()
        self.pending_launch = None
        self._stop_gst_pipeline()
        stop_processes(self.process, self.tls_process)
        self.process = self.tls_process = None
        self.tls_buffer = ""
        self.auth_failed = self.retry_pending = False
        self.pipeline_fallback_used = False
        self.resize_restart_used = False
        self.receiver_surface_width = self.receiver_surface_height = 0
        self.embedded_pipeline_size = None
        self.stable_generation = self.stable_process = self.retry_generation = None
        kill_patterns("gst-launch-1.0.*tcpclientsrc")
        self._uninhibit_sleep()
        self._set_receiving(False)

    def _stop_gst_pipeline(self):
        self.gst_bus_timer.stop()
        pipeline = self.gst_pipeline
        self.gst_pipeline = None
        self.gst_bus = None
        self.gst_generation = None
        self.gst_video_sink = None
        self.embedded_pipeline_size = None
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
