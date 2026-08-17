"""Primary stream and input bridge lifecycle."""

import json
import re
import subprocess
import sys

from PyQt6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)

try:
    from PyQt6.QtDBus import QDBusConnection
except ImportError:  
    QDBusConnection = None

from monitorize.platform.display_controller import DisplayController
from monitorize.platform.gnome_virtual_monitor import (
    save_current_virtual_layout as save_current_gnome_virtual_layout,
)
from monitorize.platform.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from monitorize.config.settings import (
    load_general_settings,
)
from monitorize.platform.utils import LINUX_DIR
from monitorize.config.validation import (
    DEFAULT_BITRATE,
    DEFAULT_FPS,
    DEFAULT_PRIMARY_RESOLUTION,
    DEFAULT_SECONDARY_RESOLUTION,
    sanitize_bitrate,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_encoder_profile,
    sanitize_fec_mode,
    sanitize_fps,
    sanitize_resolution,
    sanitize_streaming_backend,
    sanitize_video_codec,
)
from monitorize.input_bridge.uinput_backend import UINPUT_PERMISSION_HINT


GNOME_LAYOUT_CHANGE_DEBOUNCE_MS = 750
WIFI_STREAM_RESTART_DELAY_MS = 1000
DUAL_WIFI_BITRATE_BUDGET_KBPS = 30000
MIN_SECONDARY_BITRATE_KBPS = 4000
THIRD_STREAM_PUBLIC_PORT = 7114
THIRD_STREAM_BACKEND_PORT = 7115
THIRD_INPUT_PUBLIC_PORT = 7117
THIRD_INPUT_BACKEND_PORT = 7118
AUDIO_PORT = 7120
THIRD_AUDIO_PORT = 7121
GNOME_DISPLAY_CONFIG_SERVICE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"
GNOME_DISPLAY_CONFIG_IFACE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_SIGNAL = "MonitorsChanged"
class StreamingController(QObject):
    streamingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    countdownChanged = pyqtSignal(int)
    secondStreamChanged = pyqtSignal(bool)
    primaryReadyChanged = pyqtSignal(bool)
    logAppended = pyqtSignal(str, str)
    telemetryChanged = pyqtSignal()
    clientConnected = pyqtSignal(str, str)

    def __init__(self, de, local_ip, discovery, parent=None):
        super().__init__(parent)
        self.de = de
        self.local_ip = local_ip
        self.discovery = discovery
        self.display = DisplayController(de)
        self.streaming = False
        self.status = ""
        self.countdown = 0
        self.wifi = False
        self.telemetry = self._empty_telemetry()
        self.bitrate = DEFAULT_BITRATE
        self.width, self.height = DEFAULT_PRIMARY_RESOLUTION
        self.fps = DEFAULT_FPS
        self.streamer = self.input_bridge = self.audio_process = None
        self.gst_pids = set()
        self.input_launched = False
        self.generation = 0
        self.streamer_has_pipewire_node = False
        self.kde_event_buffer = ""
        self.gnome_event_buffer = ""
        self.gnome_outputs = {}
        self.primary_ready = False
        self.streamer_was_ready = False
        self.third_streamer = None
        self.third_streaming = False
        self.third_ready = False
        self.third_width, self.third_height = DEFAULT_SECONDARY_RESOLUTION
        self.third_fps = DEFAULT_FPS
        self.third_generation = 0
        self.third_gst_pids = set()
        self.third_event_buffer = ""
        self.third_input_bridge = None
        self.third_audio_process = None
        self.third_input_launched = False
        self.third_touch_enabled = True
        self.third_stylus_enabled = False
        self.third_fec_mode = "Off"
        self.third_audio_enabled = False
        self.third_output = ""
        self.third_env = None
        self.encoder_profile = "Low Latency"
        self.fec_mode = "Off"
        self.audio_enabled = False
        self.streaming_backend = "Monitorize"
        self.runtime_general = None
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        self.sunshine_watchdog_timer = QTimer(self)
        self.sunshine_watchdog_timer.setInterval(1000)
        self.sunshine_watchdog_timer.timeout.connect(self._check_sunshine_health)
        self.gnome_layout_change_timer = QTimer(self)
        self.gnome_layout_change_timer.setSingleShot(True)
        self.gnome_layout_change_timer.setInterval(GNOME_LAYOUT_CHANGE_DEBOUNCE_MS)
        self.gnome_layout_change_timer.timeout.connect(self._save_gnome_virtual_layout)
        self.gnome_display_config_bus = None
        self.gnome_display_config_connected = False
        self._gnome_monitors_changed_slot = self._on_gnome_monitors_changed
        self._is_stopping = False

    @staticmethod
    def _empty_telemetry():
        return {"available": False}

    def _reset_telemetry(self):
        self.telemetry = self._empty_telemetry()
        self.telemetryChanged.emit()

    @staticmethod
    def _metric_values(line):
        return dict(re.findall(r"([A-Za-z][A-Za-z0-9]*)=([^\s]+)", line))

    @staticmethod
    def _metric_number(values, name, suffix=""):
        value = values.get(name)
        if value is None or value == "None":
            return None
        try:
            return float(value.removesuffix(suffix))
        except ValueError:
            return None

    def _update_rtp_telemetry(self, line):
        if not line.startswith(("[RTP][Host]", "[RTP][Client]")):
            return False
        values = self._metric_values(line)
        update = {"available": True}
        if line.startswith("[RTP][Host]"):
            update.update({
                "hostCaptureFps": self._metric_number(values, "capture", "fps"),
                "hostPacedFps": self._metric_number(values, "paced", "fps"),
                "hostEncodedFps": self._metric_number(values, "encoded", "fps"),
                "hostRtpPps": self._metric_number(values, "rtp", "pps"),
                "hostTxKbps": self._metric_number(values, "tx", "kbps"),
                "bitrateKbps": self._metric_number(values, "bitrate", "kbps"),
                "videoBitrateKbps": self._metric_number(
                    values, "videoBitrate", "kbps"
                ),
                "effectiveFecPercent": self._metric_number(values, "fec", "%"),
                "hostFecPps": self._metric_number(values, "fecPps"),
                "pacingKbps": self._metric_number(values, "pacing", "kbps"),
                "senderQueue": self._metric_number(values, "senderQueue"),
                "senderDelayMs": self._metric_number(values, "senderDelay", "ms"),
                "senderDrops": self._metric_number(values, "senderDrops"),
                "senderErrors": self._metric_number(values, "sendErrors"),
                "encodePath": values.get("encodePath"),
                "scheduledIdr": self._metric_number(values, "scheduledIdr"),
                "recoveryIdr": self._metric_number(values, "recoveryIdr"),
                "confirmedIdr": self._metric_number(values, "confirmedIdr"),
                "coalescedIdr": self._metric_number(values, "coalescedIdr"),
                "idrKiB": self._metric_number(values, "idrKiB"),
                "idrMs": self._metric_number(values, "idrMs"),
            })
        elif line.startswith("[RTP][Client]"):
            update.update({
                "clientRxKbps": self._metric_number(values, "rx", "kbps"),
                "clientPps": self._metric_number(values, "pps"),
                "clientLossPercent": self._metric_number(values, "loss", "%"),
                "clientIncomplete": self._metric_number(values, "incomplete"),
                "clientRenderFps": self._metric_number(values, "render", "fps"),
                "clientQueue": self._metric_number(values, "queue"),
                "clientDecodeMs": self._metric_number(values, "decode", "ms"),
                "clientDisplayMs": self._metric_number(values, "renderLatency", "ms"),
                "clientDropped": self._metric_number(values, "dropped"),
                "clientMediaPackets": self._metric_number(values, "media"),
                "clientFecPackets": self._metric_number(values, "fec"),
                "clientFecRecovered": self._metric_number(values, "recovered"),
                "clientFecUnrecoverable": self._metric_number(
                    values, "unrecoverable"
                ),
                "clientResidualLost": self._metric_number(values, "residual"),
                "clientAssemblyP95Ms": self._metric_number(values, "assemblyP95", "ms"),
                "clientLateFrames": self._metric_number(values, "late"),
            })
        self.telemetry.update({key: value for key, value in update.items() if value is not None})
        self.telemetry["available"] = True
        self.telemetryChanged.emit()
        return True

    def _set_streaming(self, value):
        if self.streaming == value:
            return
        self.streaming = value
        self.streamingChanged.emit(value)

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def update_ip(self, value):
        self.local_ip = value
        self._advertise()

    def start(
        self, res, fps, bitrate, display_type, encoder, encoder_profile, wifi,
        options=None, video_codec="H.264 (AVC)", fec_mode="Off", enable_audio=False,
        streaming_backend="Monitorize",
    ):
        self.stop()
        self.generation += 1
        self.streamer_was_ready = False
        self._reset_telemetry()
        options = options or {}
        self.wifi = wifi
        self.streaming_backend = sanitize_streaming_backend(streaming_backend)
        width, height = sanitize_resolution(res, DEFAULT_PRIMARY_RESOLUTION)
        self.width, self.height = width, height
        self.fps, self.bitrate = sanitize_fps(fps), sanitize_bitrate(bitrate)
        self.display_type = sanitize_display_type(display_type)
        self.encoder = sanitize_encoder(encoder)
        self.encoder_profile = sanitize_encoder_profile(encoder_profile)
        self.video_codec = sanitize_video_codec(video_codec)
        if self.encoder == "Software (CPU / x264enc)":
            self.video_codec = "H.264 (AVC)"

        self.fec_mode = sanitize_fec_mode(fec_mode) if wifi else "Off"
        self.audio_enabled = bool(enable_audio)
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_STREAMING_BACKEND", self.streaming_backend)
        enc_lower = str(self.encoder or "").lower()
        if "nvidia" in enc_lower or "nvenc" in enc_lower:
            enc_setting = "nvidia"
        elif "va-api" in enc_lower or "vaapi" in enc_lower or "intel" in enc_lower or "amd" in enc_lower:
            enc_setting = "vaapi"
        else:
            enc_setting = "cpu"
        self.env.insert("MONITORIZE_ENCODER", enc_setting)
        self.env.insert("MONITORIZE_ENCODER_PROFILE", self.encoder_profile)
        is_hevc = self.video_codec in ("H.265 (HEVC)", "h265", "H.265") and self.encoder != "Software (CPU / x264enc)"
        self.env.insert("MONITORIZE_VIDEO_CODEC", "h265" if is_hevc else "h264")
        self.env.insert("MONITORIZE_REQUIRE_HARDWARE_ENCODER", "0")
        if wifi:
            self.env.insert("MONITORIZE_VIDEO_TRANSPORT", "rtp-udp-v1")
            self.env.insert(
                "MONITORIZE_FEC_PERCENT",
                "10" if self.fec_mode in ("RS-FEC 10%", "ULPFEC 10%") else "0",
            )
        self.runtime_general = options.get("general")
        if self.de in ("kde", "hyprland") and self.display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
        if self.de == "gnome" and self.display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
            self.env.insert("MONITORIZE_GNOME_VIRTUAL_SLOT", "primary")
        if wifi:
            subprocess.run(["adb", "reverse", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", f"tcp:{AUDIO_PORT}"], capture_output=True)
        defer_streaming_ui = self.de == "kde" and self.display_type == "Extend"
        if not defer_streaming_ui:
            self._set_streaming(True)
        self._prepare_display()

    def _prepare_display(self):
        if self.display_type == "Mirror" and self.de in ("kde", "hyprland"):
            self._launch_streamer()
        elif self.de == "kde":
            self._prepare_kde_native_virtual_display()
        elif self.de == "hyprland":
            self._set_status("Setting up virtual monitor on Hyprland…")
            output, error = self.display.prepare_hyprland(
                self.width, self.height, self.fps, "primary"
            )
            if error:
                self._fail(error)
                return
            self.logAppended.emit("STREAMER", f"Created headless monitor: {output}")
            self.env.insert("MONITORIZE_OUTPUT", output)
            self._set_status("Waiting for virtual monitor to be ready…")
            verified = self.display.wait_for_headless_ready(
                output, self.width, self.height,
            )
            if verified:
                self.logAppended.emit(
                    "STREAMER",
                    f"Virtual monitor {output} verified ready — launching streamer",
                )
                self._launch_streamer()
            else:
                self.logAppended.emit(
                    "STREAMER",
                    f"Could not verify {output} readiness — using 3 s fallback",
                )
                self._start_countdown(3)
        else:
            self._launch_streamer()

    def _prepare_kde_native_virtual_display(self):
        self.env.insert("MONITORIZE_KDE_VIRTUAL_SLOT", "primary")
        self._set_status("Creating KDE virtual display…")
        self._set_streaming(True)
        self._launch_streamer()

    def _fail(self, message):
        self.logAppended.emit("STREAMER", f"ERROR: {message}")
        self._set_status(message)
        self._set_streaming(False)

    def _new_process(self, use_env=True):
        process = QProcess(self)
        process.setWorkingDirectory(LINUX_DIR)
        if use_env:
            process.setProcessEnvironment(self.env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        return process

    def _new_third_process(self, env):
        process = QProcess(self)
        process.setWorkingDirectory(LINUX_DIR)
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        return process

    def _start_countdown(self, seconds):
        self.countdown = seconds
        self.countdownChanged.emit(seconds)
        self.countdown_timer.start()

    def _countdown_tick(self):
        self.countdown -= 1
        self.countdownChanged.emit(self.countdown)
        if self.countdown > 0:
            self._set_status(f"Starting virtual monitor…  {self.countdown}")
        else:
            self.countdown_timer.stop()
            self._launch_streamer()

    def _launch_streamer(self, generation=None):
        generation = self.generation if generation is None else generation
        if not self.streaming or generation != self.generation:
            return
        self.streamer_has_pipewire_node = False
        if getattr(self, "streaming_backend", "Monitorize") != "Sunshine":
            self._launch_audio(generation)
        self.kde_event_buffer = ""
        self.gnome_event_buffer = ""
        self.streamer = self._new_process()
        process = self.streamer
        self.streamer.readyReadStandardOutput.connect(
            lambda: self._read_streamer(generation, process)
        )
        self.streamer.finished.connect(
            lambda code, status: self._streamer_finished(
                code, status, generation, process
            )
        )
        self.streamer.errorOccurred.connect(
            lambda _error: self._process_error(
                "STREAMER", generation, process, self.streamer
            )
        )
        if getattr(self, "streaming_backend", "Monitorize") == "Sunshine":
            if self.display_type == "Mirror":
                self.streamer = None
                try:
                    from monitorize.config.settings import load_wifi_settings
                    from monitorize.platform.sunshine_service import (
                        is_sunshine_running,
                        start_sunshine,
                        sync_sunshine_stream_config,
                    )
                    wifi_settings = load_wifi_settings()
                    enc = wifi_settings.get("sunshine_encoder", "Auto")
                    codec = wifi_settings.get("sunshine_codec", "Auto")
                    pen_touch = wifi_settings.get("sunshine_native_pen_touch", True)
                    sync_sunshine_stream_config("", enc, codec, pen_touch, instance=1)
                    if not is_sunshine_running(1):
                        start_sunshine(1)
                    QTimer.singleShot(1500, self.sunshine_watchdog_timer.start)
                except Exception as exc:
                    app_log.warning(f"Could not initialize Sunshine mirror mode: {exc}")
                self.streamer_was_ready = True
                self._set_primary_ready(True)
                self._set_status("Sunshine mirroring primary display — Ready for Moonlight")
                return

            args = [
                "-m", "monitorize.streaming.headless_virtual_display",
                str(self.width), str(self.height), str(self.fps), "primary", str(self.de),
            ]
            self.streamer.start(sys.executable, args)
            self._set_status("Starting virtual display for Sunshine…")
            return

        module = {
            "kde": "monitorize.streaming.Streamer_kde",
            "gnome": "monitorize.streaming.Streamer_gnome",
            "hyprland": "monitorize.streaming.Streamer_hyprland",
        }.get(self.de, "monitorize.streaming.Streamer_gnome")
        args = [
            "-m", module,
            str(self.width), str(self.height), str(self.fps), str(self.bitrate),
            "wifi" if self.wifi else "usb",
        ]
        if self.de == "hyprland":
            args.append(self.display.created_output or "mirror")
        if self.de == "gnome":
            args.append(self.display_type.replace(" ", "_"))
        self.streamer.start(sys.executable, args)
        self._start_gnome_layout_tracking()
        self._advertise()
        if self._uses_kde_native_virtual_source():
            self.input_launched = False
        elif self.de == "kde":
            QTimer.singleShot(400, lambda: self._launch_input(generation))
        elif self.de == "gnome":
            self.input_launched = False
        else:
            self.input_launched = False
            self.streamer_buffer = ""
        self._set_status(
            "Waiting for Android receiver…"
            if self.wifi
            else "Status: Streaming…"
        )

    def _launch_audio(self, generation=None):
        generation = self.generation if generation is None else generation
        if (
            not self.streaming
            or generation != self.generation
            or not self.audio_enabled
            or self.audio_process is not None
        ):
            return
        self.audio_process = self._new_process()
        process = self.audio_process
        process.readyReadStandardOutput.connect(
            lambda: self._read_audio(generation, process)
        )
        process.finished.connect(
            lambda code, status: self._audio_finished(
                code, status, generation, process
            )
        )
        process.errorOccurred.connect(
            lambda _error: self._process_error(
                "AUDIO", generation, process, self.audio_process
            )
        )
        process.start(sys.executable, [
            "-m", "monitorize.streaming.audio_sender",
            "wifi" if self.wifi else "usb",
            "--port", str(AUDIO_PORT),
        ])

    def _read_audio(self, generation, process):
        if generation != self.generation or process is not self.audio_process:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if raw:
            self.logAppended.emit("AUDIO", raw)

    def _audio_finished(self, code, _status, generation, process):
        if generation != self.generation or process is not self.audio_process:
            return
        self.audio_process = None
        self.logAppended.emit(
            "AUDIO", f"Audio sender exited (code {code}); video continues."
        )

    def _launch_input(self, generation=None):
        generation = self.generation if generation is None else generation
        if not self.streaming or generation != self.generation:
            return
        if (
            self.input_bridge is not None
            and self.input_bridge.state() != QProcess.ProcessState.NotRunning
        ):
            return
        general = self.runtime_general or load_general_settings()
        touch = general.get("enable_touch", True)
        stylus = (
            general.get("enable_stylus_features", False)
            and self.de in ("kde", "gnome", "hyprland")
        )
        if not touch and not stylus:
            self.logAppended.emit("INPUT", "Input is disabled in settings.")
            return
        self.input_bridge = self._new_process()
        process = self.input_bridge
        self.input_bridge.readyReadStandardOutput.connect(
            lambda: self._read_input(generation, process)
        )
        self.input_bridge.finished.connect(
            lambda code, status: self._input_finished(
                code, status, generation, process
            )
        )
        self.input_bridge.errorOccurred.connect(
            lambda _error: self._process_error(
                "INPUT", generation, process, self.input_bridge
            )
        )
        args = ["-m", "monitorize.input_bridge.touch_daemon", str(self.width), str(self.height)]
        if self.wifi:
            args.append("--wifi")
        if self.de == "gnome" and self.display_type == "Mirror":
            args.append("--gnome-primary")
        if stylus:
            args.append("--stylus-features")
        if stylus and not touch:
            args.append("--stylus-only")
        self.input_bridge.start(sys.executable, args)
        if stylus:
            self._set_status("Stylus input starting via uinput…")
        else:
            self._set_status("Touch service starting via uinput…")

    def _uses_kde_native_virtual_source(self):
        return (
            self.de == "kde"
            and hasattr(self, "env")
            and self.env.value("MONITORIZE_KDE_VIRTUAL_SLOT") == "primary"
        )

    def _read_streamer(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.streamer if process is None else process
        if generation != self.generation or process is not self.streamer:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        visible_lines = [
            line for line in raw.splitlines()
            if not self._update_rtp_telemetry(line)
        ]
        if visible_lines:
            self.logAppended.emit("STREAMER", "\n".join(visible_lines) + "\n")
        if self.de in ("kde", "gnome"):
            if self.de == "kde":
                self.kde_event_buffer += raw
                lines = self.kde_event_buffer.split("\n")
                self.kde_event_buffer = lines.pop()
            else:
                self.gnome_event_buffer += raw
                lines = self.gnome_event_buffer.split("\n")
                self.gnome_event_buffer = lines.pop()
        else:
            lines = raw.splitlines()
        for line in lines:
            self._track_gst_pid(line)
            event = self._structured_event(line)
            if event and event.get("type") == "headless_ready":
                output_name = str(event.get("name") or "Virtual-Monitorize-1")
                if hasattr(self, "env") and self.env is not None:
                    self.env.insert("MONITORIZE_OUTPUT", output_name)
                self.width = int(event.get("width") or self.width)
                self.height = int(event.get("height") or self.height)
                refresh = float(event.get("fps") or self.fps)
                self.streamer_was_ready = True
                self._set_primary_ready(True)
                try:
                    from monitorize.config.settings import load_wifi_settings
                    from monitorize.platform.sunshine_service import (
                        is_sunshine_running,
                        start_sunshine,
                        sync_sunshine_stream_config,
                    )
                    wifi_settings = load_wifi_settings()
                    enc = wifi_settings.get("sunshine_encoder", "Auto")
                    codec = wifi_settings.get("sunshine_codec", "Auto")
                    pen_touch = wifi_settings.get("sunshine_native_pen_touch", True)
                    sync_sunshine_stream_config(output_name, enc, codec, pen_touch, instance=1)
                    if not is_sunshine_running(1):
                        start_sunshine(1)
                    QTimer.singleShot(1500, self.sunshine_watchdog_timer.start)
                except Exception as exc:
                    app_log.warning(f"Could not auto-configure Sunshine output: {exc}")
                self._set_status(
                    f"Virtual display {output_name} linked to Sunshine "
                    f"({self.width}x{self.height}@{refresh:g}Hz) — Ready for Moonlight"
                )
            if line == "[Pipeline] READY":
                self.streamer_was_ready = True
                self._set_primary_ready(True)
                self._set_status("Status: Streaming…")
            if self.de == "kde":
                self._handle_kde_streamer_line(line, generation)
            elif self.de == "gnome":
                self._handle_gnome_streamer_line(line, generation)
        self._maybe_start_wlroots_input(raw, generation)

    def _track_gst_pid(self, line):
        if "[GStreamer] PID:" not in line:
            return
        try:
            self.gst_pids.add(int(line.split("PID:")[1].strip()))
        except ValueError:
            pass

    def _track_third_gst_pid(self, line):
        if "[GStreamer] PID:" not in line:
            return
        try:
            self.third_gst_pids.add(int(line.split("PID:")[1].strip()))
        except ValueError:
            pass

    @staticmethod
    def _structured_event(line):
        if not line.startswith("MONITORIZE_EVENT "):
            return None
        try:
            return json.loads(line.split(" ", 1)[1])
        except ValueError:
            return None

    def _handle_kde_streamer_line(self, line, generation):
        event = self._structured_event(line)
        if event and event.get("type") == "kde_output_ready":
            if event.get("slot") != "primary":
                return
            output_name = str(event.get("name") or "")
            if output_name == "Virtual-Monitorize-1":
                self.env.insert("MONITORIZE_OUTPUT", output_name)
            self.width = int(event.get("width") or self.width)
            self.height = int(event.get("height") or self.height)
            refresh = float(event.get("refresh_rate") or self.fps)
            self._set_status(
                f"KDE virtual display ready: {output_name} "
                f"{self.width}x{self.height}@{refresh:g}"
            )
        elif event and event.get("type") == "kde_capture_ready":
            self.streamer_has_pipewire_node = True
            self._set_status("KDE native capture ready; stream pipeline starting…")
            self._maybe_start_kde_native_input(generation)
        elif "[Portal] Creating session" in line:
            self._set_status("KDE portal opened — choose the display to mirror.")
        elif "[Portal] Got PipeWire node=" in line:
            self.streamer_has_pipewire_node = True
            self._set_status("KDE display selected; stream pipeline starting…")
        elif line.startswith("[ERROR]"):
            self._set_status(line.removeprefix("[ERROR]").strip())

    def _handle_gnome_streamer_line(self, line, generation):
        event = self._structured_event(line)
        if not event:
            return
        slot = event.get("slot")
        if event.get("type") == "gnome_output_ready" and slot == "primary":
            connector = str(event.get("connector") or "")
            if connector:
                self.gnome_outputs["primary"] = connector
                self.env.insert("MONITORIZE_OUTPUT", connector)
            self.width = int(event.get("width") or self.width)
            self.height = int(event.get("height") or self.height)
            refresh = float(event.get("refresh_rate") or self.fps)
            self._set_status(
                f"GNOME display ready: {connector} {self.width}x{self.height}@{refresh:g}"
            )
        elif event.get("type") == "gnome_capture_ready" and slot == "primary":
            self.streamer_has_pipewire_node = True
            if not self.input_launched:
                self.input_launched = True
                self._launch_input(generation)
        elif event.get("type") == "gnome_retry" and slot == "primary":
            self.logAppended.emit("STREAMER", "[GNOME] Retrying once after verified cleanup.")
            stop_processes(self.input_bridge)
            self.input_bridge = None
            self.input_launched = False
            self._set_primary_ready(False)
        elif event.get("type") == "gnome_error":
            self._set_status(str(event.get("message") or "GNOME virtual display failed"))

    def _maybe_start_kde_native_input(self, generation):
        if not self._uses_kde_native_virtual_source() or self.input_launched:
            return
        self.input_launched = True
        QTimer.singleShot(500, lambda: self._launch_input(generation))

    def _maybe_start_wlroots_input(self, raw, generation):
        if self.de == "hyprland" and not self.input_launched:
            self.streamer_buffer += raw
            if "[Portal] Got PipeWire node=" in self.streamer_buffer:
                self.input_launched = True
                QTimer.singleShot(500, lambda: self._launch_input(generation))

    def _read_input(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.input_bridge if process is None else process
        if generation != self.generation or process is not self.input_bridge:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if "MONITORIZE_UINPUT_PERMISSION:" in raw:
            self._set_status(UINPUT_PERMISSION_HINT.split(": ", 1)[1])
        elif "[TouchDaemon] INFO READY" in raw:
            self._set_status("Touch and stylus input ready")
        self.logAppended.emit(
            "INPUT",
            raw,
        )

    def _process_error(self, label, generation, process, current_process):
        if generation != self.generation or process is not current_process:
            return
        self.logAppended.emit(label, f"Process error: {process.errorString()}")

    def _streamer_finished(
        self, code, _status, generation=None, process=None
    ):
        if (
            generation is not None
            and (generation != self.generation or process is not self.streamer)
        ):
            return
        self.logAppended.emit("STREAMER", f"Process exited (code {code})")
        self.streamer = None
        self._set_primary_ready(False)
        if self.wifi and self.streamer_was_ready and self.streaming:
            if self.gst_pids:
                kill_tracked_pids(set(self.gst_pids))
                self.gst_pids.clear()
            self._set_status("Stream interrupted; restarting…")
            self.logAppended.emit(
                "STREAMER",
                "Wi-Fi streamer exited after READY; restarting in 1 s.",
            )
            QTimer.singleShot(
                WIFI_STREAM_RESTART_DELAY_MS,
                lambda: self._restart_wifi_streamer(generation),
            )
            return
        if self.de == "gnome" and code and self.streaming:
            message = self.status or "GNOME virtual display failed — see logs"
            self.stop()
            self._set_status(message)
        elif self.de == "kde" and code and self.streaming:
            message = self.status or "KDE streaming setup failed — see logs"
            self.stop()
            self._set_status(message)

    def _check_sunshine_health(self):
        try:
            if not self.streaming or getattr(self, "streaming_backend", "Monitorize") != "Sunshine":
                self.sunshine_watchdog_timer.stop()
                return

            from monitorize.platform.sunshine_service import check_sunshine_health
            alive, exit_code, last_error = check_sunshine_health(instance=1)
            if not alive:
                self.sunshine_watchdog_timer.stop()
                err_msg = "Sunshine backend terminated unexpectedly"
                if exit_code is not None:
                    err_msg += f" (exit code {exit_code})"
                if last_error:
                    err_msg += f": {last_error}"
                self.logAppended.emit("STREAMER", f"ERROR: {err_msg}")
                app_log.error(f"Watchdog detected Sunshine failure: {err_msg}")
                status_msg = f"Sunshine backend crashed (code {exit_code}) — see logs" if exit_code is not None else "Sunshine backend crashed — see logs"
                self._set_status(status_msg)
                QTimer.singleShot(0, self.stop)
                return

            if self.third_streaming:
                alive2, exit_code2, last_error2 = check_sunshine_health(instance=2)
                if not alive2:
                    err_msg2 = "Sunshine Instance 2 terminated unexpectedly"
                    if exit_code2 is not None:
                        err_msg2 += f" (exit code {exit_code2})"
                    if last_error2:
                        err_msg2 += f": {last_error2}"
                    self.logAppended.emit("THIRD_STREAMER", f"ERROR: {err_msg2}")
                    app_log.error(f"Watchdog detected Sunshine Instance 2 failure: {err_msg2}")
                    self._set_status("Sunshine Instance 2 crashed — see logs")
                    QTimer.singleShot(0, self.stop_third)
        except Exception as exc:
            app_log.error(f"Error in Sunshine watchdog callback: {exc}")

    def _restart_wifi_streamer(self, generation):
        if (
            generation != self.generation
            or not self.streaming
            or not self.wifi
            or self.streamer is not None
        ):
            return
        self._launch_streamer(generation)

    def _input_finished(self, code, _status, generation=None, process=None):
        if (
            generation is not None
            and (generation != self.generation or process is not self.input_bridge)
        ):
            return
        self.logAppended.emit("INPUT", f"Bridge exited (code {code})")
        if code == 0 and self.streaming:
            self.logAppended.emit(
                "INPUT",
                "ℹ️  Touch input not available — streaming continues without touch.",
            )

    def _should_track_gnome_virtual_layout(self):
        return (
            self.de == "gnome"
            and self.streaming
            and bool(self.gnome_outputs)
        )

    def _start_gnome_layout_tracking(self):
        if self.de == "gnome" and self.streaming and self.display_type == "Extend":
            self._connect_gnome_display_config_signal()

    def _stop_gnome_layout_tracking(self):
        self.gnome_layout_change_timer.stop()
        self._disconnect_gnome_display_config_signal()

    def _save_gnome_virtual_layout(self):
        if self._should_track_gnome_virtual_layout():
            topology = "+".join(
                role for role in ("primary", "additional")
                if self.gnome_outputs.get(role)
            )
            return save_current_gnome_virtual_layout(
                topology, role_connectors=dict(self.gnome_outputs)
            )
        return False

    def _connect_gnome_display_config_signal(self):
        if self.gnome_display_config_connected or QDBusConnection is None:
            return
        try:
            bus = QDBusConnection.sessionBus()
            connected = bus.connect(
                GNOME_DISPLAY_CONFIG_SERVICE,
                GNOME_DISPLAY_CONFIG_PATH,
                GNOME_DISPLAY_CONFIG_IFACE,
                GNOME_DISPLAY_CONFIG_SIGNAL,
                self._gnome_monitors_changed_slot,
            )
        except Exception:
            return
        if connected:
            self.gnome_display_config_bus = bus
            self.gnome_display_config_connected = True

    def _disconnect_gnome_display_config_signal(self):
        if not self.gnome_display_config_connected:
            return
        try:
            self.gnome_display_config_bus.disconnect(
                GNOME_DISPLAY_CONFIG_SERVICE,
                GNOME_DISPLAY_CONFIG_PATH,
                GNOME_DISPLAY_CONFIG_IFACE,
                GNOME_DISPLAY_CONFIG_SIGNAL,
                self._gnome_monitors_changed_slot,
            )
        except Exception:
            pass
        self.gnome_display_config_bus = None
        self.gnome_display_config_connected = False

    @pyqtSlot()
    def _on_gnome_monitors_changed(self):
        if not self._should_track_gnome_virtual_layout():
            return
        self.gnome_layout_change_timer.start()

    def start_third(
        self, res, fps, bitrate, encoder, encoder_profile, enable_touch=True,
        enable_stylus_features=False, fec_mode="Off", enable_audio=False,
        sunshine_encoder="Auto", sunshine_codec="Auto", sunshine_native_pen_touch=True,
    ):
        if self.de not in ("kde", "gnome", "hyprland"):
            self.logAppended.emit(
                "STREAMER",
                "[Third display] Additional displays are only enabled on supported Wayland desktops.",
            )
            self.secondStreamChanged.emit(False)
            self._advertise()
            return
        if not self.streaming:
            self.logAppended.emit(
                "STREAMER",
                "[Third display] Start the primary stream before adding a display.",
            )
            self.secondStreamChanged.emit(False)
            self._advertise()
            return
        if not self.primary_ready:
            self.logAppended.emit(
                "STREAMER",
                "[Third display] Primary stream is not ready yet.",
            )
            self.secondStreamChanged.emit(False)
            self._advertise()
            return
        if self.third_streaming:
            self.stop_third()

        width, height = sanitize_resolution(res, DEFAULT_SECONDARY_RESOLUTION)
        third_fps = sanitize_fps(fps)
        third_bitrate = sanitize_bitrate(bitrate)
        third_encoder = sanitize_encoder(encoder)
        third_encoder_profile = sanitize_encoder_profile(encoder_profile)
        third_fec_mode = sanitize_fec_mode(fec_mode) if self.wifi else "Off"
        third_touch_enabled = bool(enable_touch)
        third_stylus_enabled = bool(enable_stylus_features)
        third_audio_enabled = bool(enable_audio) and self.wifi
        if self.wifi:
            available = max(
                MIN_SECONDARY_BITRATE_KBPS,
                DUAL_WIFI_BITRATE_BUDGET_KBPS - self.bitrate,
            )
            if third_bitrate > available:
                self.logAppended.emit(
                    "STREAMER",
                    f"[Third display] Bitrate limited from {third_bitrate} to "
                    f"{available} kbps to keep dual Wi-Fi streams responsive.",
                )
                third_bitrate = available

        third_output = ""
        if self.de == "hyprland":
            self._set_status("Creating additional Hyprland headless display…")
            third_output, error = self.display.prepare_hyprland(
                width, height, third_fps, "additional"
            )
            if error:
                self.logAppended.emit("STREAMER", f"[Third display] ERROR: {error}")
                self._set_status(error)
                self.secondStreamChanged.emit(False)
                self._advertise()
                return
            if not self.display.wait_for_headless_ready(third_output, width, height):
                self.display.remove_hyprland_output("additional")
                error = f"Hyprland did not make {third_output} ready"
                self.logAppended.emit("STREAMER", f"[Third display] ERROR: {error}")
                self._set_status(error)
                self.secondStreamChanged.emit(False)
                self._advertise()
                return
            self.logAppended.emit(
                "STREAMER", f"[Third display] Created headless monitor: {third_output}"
            )

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        third_enc_lower = str(third_encoder or "").lower()
        if "nvidia" in third_enc_lower or "nvenc" in third_enc_lower:
            third_enc_setting = "nvidia"
        elif "va-api" in third_enc_lower or "vaapi" in third_enc_lower or "intel" in third_enc_lower or "amd" in third_enc_lower:
            third_enc_setting = "vaapi"
        else:
            third_enc_setting = "cpu"
        env.insert("MONITORIZE_ENCODER", third_enc_setting)
        env.insert("MONITORIZE_ENCODER_PROFILE", third_encoder_profile)
        if self.wifi:
            env.insert("MONITORIZE_VIDEO_TRANSPORT", "rtp-udp-v1")
            env.insert(
                "MONITORIZE_FEC_PERCENT",
                "10" if third_fec_mode in ("RS-FEC 10%", "ULPFEC 10%") else "0",
            )
        if self.de == "kde":
            env.insert("MONITORIZE_KDE_VIRTUAL_SLOT", "additional")
            env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
        elif self.de == "gnome":
            env.insert("MONITORIZE_GNOME_VIRTUAL_SLOT", "additional")
            env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
            if self.gnome_outputs.get("primary"):
                env.insert("MONITORIZE_GNOME_PRIMARY_OUTPUT", self.gnome_outputs["primary"])
        else:
            env.insert("MONITORIZE_PORTAL_SOURCE_TYPE", "1")
            env.insert(
                "MONITORIZE_PORTAL_SELECTOR_HINT",
                f"Select {third_output} for Monitorize's additional display.",
            )
        env.insert(
            "MONITORIZE_PORT",
            str(THIRD_STREAM_PUBLIC_PORT),
        )
        env.insert(
            "MONITORIZE_HOST",
            "127.0.0.1" if not self.wifi else "0.0.0.0",
        )
        if third_output:
            env.insert("MONITORIZE_OUTPUT", third_output)
        if not self.wifi:
            self._configure_third_usb_reverse(
                third_touch_enabled or third_stylus_enabled
            )

        self.third_generation += 1
        generation = self.third_generation
        self.third_width = width
        self.third_height = height
        self.third_fps = third_fps
        self.third_bitrate = third_bitrate
        self.third_encoder = third_encoder
        self.third_encoder_profile = third_encoder_profile
        self.third_fec_mode = third_fec_mode
        self.third_touch_enabled = third_touch_enabled
        self.third_stylus_enabled = third_stylus_enabled
        self.third_audio_enabled = third_audio_enabled
        self.third_sunshine_encoder = str(sunshine_encoder or "Auto")
        self.third_sunshine_codec = str(sunshine_codec or "Auto")
        self.third_sunshine_native_pen_touch = bool(sunshine_native_pen_touch)
        self.third_audio_process = None
        self.third_output = third_output
        self.third_env = env
        self.third_ready = False
        self.third_streaming = True
        self.third_input_launched = False
        self.third_input_bridge = None
        self.third_gst_pids.clear()
        self.third_event_buffer = ""
        self.third_streamer = self._new_third_process(env)
        process = self.third_streamer
        process.readyReadStandardOutput.connect(
            lambda: self._read_third_streamer(generation, process)
        )
        process.finished.connect(
            lambda code, status: self._third_streamer_finished(
                code, status, generation, process
            )
        )
        process.errorOccurred.connect(
            lambda _error: self._third_process_error(generation, process)
        )
        if getattr(self, "streaming_backend", "Monitorize") == "Sunshine":
            args = [
                "-m", "monitorize.streaming.headless_virtual_display",
                str(width), str(height), str(third_fps), "additional", str(self.de),
            ]
            process.start(sys.executable, args)
            self.secondStreamChanged.emit(True)
            self._set_status("Starting additional virtual display for Sunshine (Instance 2)…")
            self.logAppended.emit(
                "STREAMER",
                "[Third display] Starting virtual display for Sunshine Instance 2 on port 49089.",
            )
            return

        module = {
            "kde": "monitorize.streaming.Streamer_kde",
            "gnome": "monitorize.streaming.Streamer_gnome",
            "hyprland": "monitorize.streaming.Streamer_hyprland",
        }[self.de]
        process.start(sys.executable, [
            "-m", module,
            str(width), str(height), str(third_fps), str(third_bitrate),
            "wifi" if self.wifi else "usb",
            *([third_output] if self.de == "hyprland" else []),
        ])
        self.secondStreamChanged.emit(True)
        self._advertise()
        action = (
            "Creating KDE virtual display" if self.de == "kde" else
            "Creating GNOME virtual display" if self.de == "gnome" else
            f"Select {third_output} in the portal picker"
        )
        self.logAppended.emit(
            "STREAMER",
            f"[Third display] {action} on port "
            f"{THIRD_STREAM_PUBLIC_PORT}.",
        )

    @staticmethod
    def _run_adb_reverse(*args):
        try:
            subprocess.run(["adb", "reverse", *args], capture_output=True)
        except OSError:
            pass

    def _configure_third_usb_reverse(self, touch_enabled):
        self._run_adb_reverse("--remove", f"tcp:{THIRD_STREAM_PUBLIC_PORT}")
        self._run_adb_reverse("--remove", f"tcp:{THIRD_STREAM_BACKEND_PORT}")
        self._run_adb_reverse(
            f"tcp:{THIRD_STREAM_PUBLIC_PORT}", f"tcp:{THIRD_STREAM_PUBLIC_PORT}"
        )
        if touch_enabled:
            self._run_adb_reverse(
                f"tcp:{THIRD_STREAM_BACKEND_PORT}",
                f"tcp:{THIRD_STREAM_BACKEND_PORT}",
            )

    def _maybe_launch_third_input(self, generation):
        if (
            not self.third_streaming
            or generation != self.third_generation
            or not self.third_ready
            or not (self.third_touch_enabled or self.third_stylus_enabled)
            or not self.third_output
        ):
            return
        if self.third_input_launched or (
            self.third_input_bridge is not None
            and self.third_input_bridge.state() != QProcess.ProcessState.NotRunning
        ):
            return
        env = QProcessEnvironment(
            self.third_env or QProcessEnvironment.systemEnvironment()
        )
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("MONITORIZE_OUTPUT", self.third_output)
        self.third_input_bridge = self._new_third_process(env)
        process = self.third_input_bridge
        process.readyReadStandardOutput.connect(
            lambda: self._read_third_input(generation, process)
        )
        process.finished.connect(
            lambda code, status: self._third_input_finished(
                code, status, generation, process
            )
        )
        process.errorOccurred.connect(
            lambda _error: self._third_input_error(generation, process)
        )
        port = THIRD_INPUT_PUBLIC_PORT if self.wifi else THIRD_STREAM_BACKEND_PORT
        args = [
            "-m", "monitorize.input_bridge.touch_daemon",
            str(self.third_width), str(self.third_height),
            "--additional", "--port", str(port),
        ]
        if self.wifi:
            args.append("--wifi")
        if self.third_stylus_enabled:
            args.append("--stylus-features")
            if not self.third_touch_enabled:
                args.append("--stylus-only")
        process.start(sys.executable, args)
        self.third_input_launched = True
        self.logAppended.emit(
            "INPUT",
            f"[Third display] Touch bridge starting on port {port} for {self.third_output}.",
        )

    def _read_third_input(self, generation, process):
        if generation != self.third_generation or process is not self.third_input_bridge:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if "MONITORIZE_UINPUT_PERMISSION:" in raw:
            self._set_status(UINPUT_PERMISSION_HINT.split(": ", 1)[1])
        elif "[TouchDaemon] INFO READY" in raw:
            self._set_status("Additional-display touch and stylus ready")
        self.logAppended.emit("INPUT", f"[Third display] {raw}")

    def _third_input_finished(self, code, _status, generation, process):
        if generation != self.third_generation or process is not self.third_input_bridge:
            return
        self.logAppended.emit("INPUT", f"[Third display] Touch bridge exited (code {code})")
        self.third_input_bridge = None
        self.third_input_launched = False

    def _third_input_error(self, generation, process):
        if generation == self.third_generation and process is self.third_input_bridge:
            self.logAppended.emit(
                "INPUT", f"[Third display] Touch bridge error: {process.errorString()}"
            )

    def _stop_third_input(self):
        if self.third_input_bridge is not None:
            stop_processes(self.third_input_bridge)
        self.third_input_bridge = None
        self.third_input_launched = False

    def _launch_third_audio(self, generation):
        if (not self.third_streaming or generation != self.third_generation or
                not self.third_audio_enabled or self.third_audio_process is not None):
            return
        self.third_audio_process = self._new_third_process(
            self.third_env or QProcessEnvironment.systemEnvironment()
        )
        process = self.third_audio_process
        process.readyReadStandardOutput.connect(lambda: self._read_third_audio(generation, process))
        process.finished.connect(lambda code, status: self._third_audio_finished(code, status, generation, process))
        process.start(sys.executable, ["-m", "monitorize.streaming.audio_sender", "wifi", "--port", str(THIRD_AUDIO_PORT)])

    def _read_third_audio(self, generation, process):
        if generation == self.third_generation and process is self.third_audio_process:
            raw = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if raw:
                self.logAppended.emit("AUDIO", f"[Third display] {raw}")

    def _third_audio_finished(self, code, _status, generation, process):
        if generation == self.third_generation and process is self.third_audio_process:
            self.third_audio_process = None
            self.logAppended.emit("AUDIO", f"[Third display] Audio sender exited (code {code}); video continues.")

    def _stop_third_audio(self):
        if self.third_audio_process is not None:
            stop_processes(self.third_audio_process)
        self.third_audio_process = None

    def _read_third_streamer(self, generation=None, process=None):
        generation = self.third_generation if generation is None else generation
        process = self.third_streamer if process is None else process
        if generation != self.third_generation or process is not self.third_streamer:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.third_event_buffer += raw
        lines = self.third_event_buffer.split("\n")
        self.third_event_buffer = lines.pop()
        if lines:
            message = "\n".join(f"[Third display] {line}" for line in lines)
            if raw.endswith("\n"):
                message += "\n"
            self.logAppended.emit("STREAMER", message)
        for line in lines:
            self._track_third_gst_pid(line)
            event = self._structured_event(line)
            if event and event.get("type") == "headless_ready":
                output_name = str(event.get("name") or "Virtual-Monitorize-2")
                if hasattr(self, "third_env") and self.third_env is not None:
                    self.third_env.insert("MONITORIZE_OUTPUT", output_name)
                self.third_output = output_name
                self.third_width = int(event.get("width") or self.third_width)
                self.third_height = int(event.get("height") or self.third_height)
                refresh = float(event.get("fps") or self.third_fps)
                self.third_ready = True
                try:
                    from monitorize.platform.sunshine_service import (
                        is_sunshine_running,
                        start_sunshine,
                        sync_sunshine_stream_config,
                    )
                    sync_sunshine_stream_config(
                        output_name,
                        getattr(self, "third_sunshine_encoder", "Auto"),
                        getattr(self, "third_sunshine_codec", "Auto"),
                        getattr(self, "third_sunshine_native_pen_touch", True),
                        instance=2,
                    )
                    if not is_sunshine_running(instance=2):
                        start_sunshine(instance=2)
                    QTimer.singleShot(1500, self.sunshine_watchdog_timer.start)
                except Exception as exc:
                    app_log.warning(f"Could not auto-configure Sunshine instance 2 output: {exc}")
                self._set_status(
                    f"Additional virtual display {output_name} linked to Sunshine Instance 2 "
                    f"({self.third_width}x{self.third_height}@{refresh:g}Hz) — Ready for Moonlight (Port 49089)"
                )
            elif event and event.get("type") == "kde_output_ready":
                self.third_output = str(event.get("name") or self.third_output)
                if self.third_env is not None and self.third_output:
                    self.third_env.insert("MONITORIZE_OUTPUT", self.third_output)
                self.third_width = int(event.get("width") or self.third_width)
                self.third_height = int(event.get("height") or self.third_height)
                refresh = float(event.get("refresh_rate") or self.third_fps)
                self._set_status(
                    f"Additional KDE display ready: {self.third_width}x"
                    f"{self.third_height}@{refresh:g}"
                )
            elif event and event.get("type") == "kde_capture_ready":
                self._set_status("Additional KDE capture ready; stream pipeline starting...")
            elif event and event.get("type") == "gnome_output_ready":
                connector = str(event.get("connector") or "")
                if connector:
                    self.gnome_outputs["additional"] = connector
                    self.third_output = connector
                    if self.third_env is not None:
                        self.third_env.insert("MONITORIZE_OUTPUT", connector)
                    self._connect_gnome_display_config_signal()
                self.third_width = int(event.get("width") or self.third_width)
                self.third_height = int(event.get("height") or self.third_height)
                refresh = float(event.get("refresh_rate") or self.third_fps)
                self._set_status(
                    f"Additional GNOME display ready: {connector} {self.third_width}x"
                    f"{self.third_height}@{refresh:g}"
                )
            elif event and event.get("type") == "gnome_capture_ready":
                self._set_status("Additional GNOME capture ready; stream pipeline starting...")
            elif "[Portal] Got PipeWire node=" in line:
                self._set_status("Third display selected; stream pipeline starting...")
            elif line.startswith("[ERROR]"):
                self._set_status(line.removeprefix("[ERROR]").strip())
            if line == "[Pipeline] READY":
                if not self.third_ready:
                    self.third_ready = True
                    self._advertise()
                    self._set_status("Third display streaming")
                self._launch_third_audio(generation)
                self._maybe_launch_third_input(generation)
            elif event and event.get("type") in (
                "kde_output_ready", "gnome_output_ready",
            ):
                self._maybe_launch_third_input(generation)

    def _third_streamer_finished(
        self, code, _status, generation=None, process=None
    ):
        if (
            generation is not None
            and (generation != self.third_generation or process is not self.third_streamer)
        ):
            return
        self.logAppended.emit("STREAMER", f"[Third display] Streamer exited (code {code})")
        self._stop_third_input()
        self._stop_third_audio()
        self.third_streamer = None
        self.third_streaming = False
        self.third_ready = False
        self.third_output = ""
        self.third_env = None
        self.third_gst_pids.clear()
        if self.de == "hyprland":
            self.display.remove_hyprland_output("additional")
        self.secondStreamChanged.emit(False)
        self._advertise()

    def _third_process_error(self, generation, process):
        if generation != self.third_generation or process is not self.third_streamer:
            return
        self.logAppended.emit("STREAMER", f"[Third display] Process error: {process.errorString()}")

    def third_active(self):
        return self.third_streaming

    def _set_primary_ready(self, value):
        if self.primary_ready == value:
            return
        self.primary_ready = value
        self.primaryReadyChanged.emit(value)

    def active_configuration(self):
        general = dict(self.runtime_general or load_general_settings())
        config = {
            "version": 1,
            "mode": "wifi" if self.wifi else "usb",
            "primary": {
                "resolution": f"{self.width}x{self.height}",
                "fps": str(self.fps),
                "bitrate": str(self.bitrate),
                "display_type": self.display_type,
                "encoder": self.encoder,
                "encoder_profile": self.encoder_profile,
                "fec_mode": self.fec_mode,
                "enable_audio": self.audio_enabled,
            },
            "general": general,
            "third": {"enabled": False},
        }
        if self.third_streaming:
            config["third"] = {
                "enabled": True,
                "resolution": f"{self.third_width}x{self.third_height}",
                "fps": str(self.third_fps),
                "bitrate": str(self.third_bitrate),
                "encoder": self.third_encoder,
                "encoder_profile": self.third_encoder_profile,
                "fec_mode": self.third_fec_mode,
                "enable_touch": self.third_touch_enabled,
                "enable_stylus_features": self.third_stylus_enabled,
                "enable_audio": self.third_audio_enabled,
            }
        return config

    def stop_third(self):
        if self.de == "gnome":
            self._save_gnome_virtual_layout()
        self.third_generation += 1
        self._stop_third_input()
        self._stop_third_audio()
        if self.third_streamer is not None:
            try:
                if self.third_streamer.state() == QProcess.ProcessState.Running:
                    self.third_streamer.write(b"quit\n")
                    self.third_streamer.waitForBytesWritten(500)
            except Exception:
                pass
            stop_processes(self.third_streamer)
        self.third_streamer = None
        if self.third_gst_pids:
            kill_tracked_pids(set(self.third_gst_pids))
            self.third_gst_pids.clear()
        kill_patterns(
            f"gst-launch-1.0.*port={THIRD_STREAM_PUBLIC_PORT}",
            f"gst-launch-1.0.*port={THIRD_STREAM_BACKEND_PORT}",
            "monitorize-kde-virtual-output.*(Monitorize-2|monitorize-additional)",
        )
        self.third_streaming = False
        self.third_ready = False
        self.third_output = ""
        self.third_env = None
        self.third_touch_enabled = True
        self.third_stylus_enabled = False
        self.third_fec_mode = "Off"
        self.third_audio_enabled = False
        if not self.wifi:
            self._run_adb_reverse("--remove", f"tcp:{THIRD_STREAM_PUBLIC_PORT}")
            self._run_adb_reverse("--remove", f"tcp:{THIRD_STREAM_BACKEND_PORT}")
        if self.de == "hyprland":
            self.display.remove_hyprland_output("additional")
        self.gnome_outputs.pop("additional", None)
        if getattr(self, "streaming_backend", "Monitorize") == "Sunshine":
            try:
                from monitorize.platform.sunshine_service import stop_sunshine
                stop_sunshine(instance=2)
            except Exception:
                pass
        self._advertise()
        self.secondStreamChanged.emit(False)
        self.logAppended.emit("STREAMER", "[Third display] Stopped.")

    def _advertise(self, *_args):
        if self.streaming and self.wifi:
            args = (
                self.local_ip,
                self.third_streaming,
                self.fps,
                self.third_fps,
                self.width,
                self.height,
                self.third_width if self.third_streaming else None,
                self.third_height if self.third_streaming else None,
            )
            self.discovery.advertise(*args)

    def stop(self):
        if getattr(self, "_is_stopping", False):
            return
        self._is_stopping = True
        try:
            should_track_layout = self._should_track_gnome_virtual_layout()
            saved_layout = self._save_gnome_virtual_layout()
            if should_track_layout and not saved_layout:
                self.logAppended.emit(
                    "STREAMER",
                    "GNOME virtual layout save failed before stop; using last saved layout.",
                )
            self._stop_gnome_layout_tracking()
            self.generation += 1
            self.countdown_timer.stop()
            self.sunshine_watchdog_timer.stop()
            self.streamer_has_pipewire_node = False
            self.kde_event_buffer = ""
            self.gnome_event_buffer = ""
            self._reset_telemetry()
            self.streamer_was_ready = False
            self._set_primary_ready(False)
            self.runtime_general = None
            if (
                self.third_streaming
                or self.third_streamer is not None
                or self.third_input_bridge is not None
                or self.third_audio_process is not None
            ):
                self.stop_third()
            stop_processes(self.streamer, self.input_bridge, self.audio_process)
            self.streamer = self.input_bridge = self.audio_process = None
            kill_tracked_pids(set(self.gst_pids))
            self.gst_pids.clear()
            kill_patterns(
                "gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112",
                "gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115",
                "monitorize\\.streaming\\.Streamer_.*",
                "monitorize\\.input_bridge\\.touch_daemon",
                "monitorize\\.streaming\\.audio_sender",
                "monitorize-kde-virtual-output",
            )
            self.display.cleanup()
            self.gnome_outputs.clear()
            self.discovery.stop_advertising()
            if getattr(self, "streaming_backend", "Monitorize") == "Sunshine":
                try:
                    from monitorize.platform.sunshine_service import stop_sunshine
                    stop_sunshine()
                except Exception:
                    pass
            self._set_streaming(False)
        finally:
            self._is_stopping = False
