"""Primary stream, TLS proxy and input bridge lifecycle."""

import json
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
    load_wifi_settings,
)
from monitorize.platform.utils import LINUX_DIR
from monitorize.config.validation import (
    DEFAULT_FPS,
    DEFAULT_PRIMARY_RESOLUTION,
    DEFAULT_SECONDARY_RESOLUTION,
    sanitize_bitrate,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_encoder_profile,
    sanitize_fps,
    sanitize_resolution,
)
from monitorize.input_bridge.uinput_backend import UINPUT_PERMISSION_HINT


GNOME_LAYOUT_CHANGE_DEBOUNCE_MS = 750
THIRD_STREAM_PUBLIC_PORT = 7114
THIRD_STREAM_BACKEND_PORT = 7115
GNOME_DISPLAY_CONFIG_SERVICE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"
GNOME_DISPLAY_CONFIG_IFACE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_SIGNAL = "MonitorsChanged"


class StreamingController(QObject):
    streamingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    countdownChanged = pyqtSignal(int)
    pairingCodeChanged = pyqtSignal(str)
    secondStreamChanged = pyqtSignal(bool)
    primaryReadyChanged = pyqtSignal(bool)
    logAppended = pyqtSignal(str, str)

    def __init__(self, de, local_ip, discovery, parent=None):
        super().__init__(parent)
        self.de = de
        self.local_ip = local_ip
        self.discovery = discovery
        self.display = DisplayController(de)
        self.streaming = False
        self.status = ""
        self.countdown = 0
        self.pairing_code = ""
        self.wifi = False
        self.encrypted = False
        self.fps = DEFAULT_FPS
        self.streamer = self.input_bridge = self.tls_proxy = None
        self.gst_pids = set()
        self.tls_buffer = ""
        self.input_launched = False
        self.generation = 0
        self.streamer_has_pipewire_node = False
        self.kde_event_buffer = ""
        self.gnome_event_buffer = ""
        self.gnome_outputs = {}
        self.primary_ready = False
        self.third_streamer = None
        self.third_streaming = False
        self.third_ready = False
        self.third_generation = 0
        self.third_gst_pids = set()
        self.third_event_buffer = ""
        self.encoder_profile = "Low Latency"
        self.runtime_general = None
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        self.gnome_layout_change_timer = QTimer(self)
        self.gnome_layout_change_timer.setSingleShot(True)
        self.gnome_layout_change_timer.setInterval(GNOME_LAYOUT_CHANGE_DEBOUNCE_MS)
        self.gnome_layout_change_timer.timeout.connect(self._save_gnome_virtual_layout)
        self.gnome_display_config_bus = None
        self.gnome_display_config_connected = False
        self._gnome_monitors_changed_slot = self._on_gnome_monitors_changed

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
        options=None,
    ):
        self.stop()
        self.generation += 1
        options = options or {}
        self.wifi = wifi
        width, height = sanitize_resolution(res, DEFAULT_PRIMARY_RESOLUTION)
        self.width, self.height = width, height
        self.fps, self.bitrate = sanitize_fps(fps), sanitize_bitrate(bitrate)
        self.display_type = sanitize_display_type(display_type)
        self.encoder = sanitize_encoder(encoder)
        self.encoder_profile = sanitize_encoder_profile(encoder_profile)
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(self.encoder, "cpu"))
        self.env.insert("MONITORIZE_ENCODER_PROFILE", self.encoder_profile)
        settings = options.get("wifi") or (load_wifi_settings() if wifi else {})
        self.encrypted = settings.get("use_encryption", True) if wifi else False
        self.env.insert("MONITORIZE_STREAM_TYPE", settings.get("stream_type", "Speed"))
        self.runtime_general = options.get("general")
        if self.encrypted:
            self.env.insert("MONITORIZE_HOST", "127.0.0.1")
            self.env.insert("MONITORIZE_PORT", "7112")
        if self.de in ("kde", "hyprland") and self.display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
        if self.de == "gnome" and self.display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
            self.env.insert("MONITORIZE_GNOME_VIRTUAL_SLOT", "primary")
        if wifi:
            subprocess.run(["adb", "reverse", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)
            if self.encrypted:
                self._launch_tls()
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
                self.width, self.height, self.fps
            )
            if error:
                self._fail(error)
                return
            self.logAppended.emit("STREAMER", f"Created headless monitor: {output}")
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
        self._set_status("Status: Streaming…")

    def _launch_tls(self):
        self.tls_proxy = self._new_process(use_env=False)
        generation = self.generation
        process = self.tls_proxy
        self.tls_proxy.readyReadStandardOutput.connect(
            lambda: self._read_tls(generation, process)
        )
        self.tls_proxy.finished.connect(lambda code, _status: (
            self.logAppended.emit("TLS", f"Proxy exited (code {code})")
            if generation == self.generation and process is self.tls_proxy else None
        ))
        self.tls_proxy.errorOccurred.connect(
            lambda _error: self._process_error("TLS", generation, process, self.tls_proxy)
        )
        self.tls_proxy.start(sys.executable, ["-m", "monitorize.security.tls_proxy"])

    def _read_tls(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.tls_proxy if process is None else process
        if generation != self.generation or process is not self.tls_proxy:
            return
        self.tls_buffer += bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        lines = self.tls_buffer.split("\n")
        self.tls_buffer = lines.pop() if not self.tls_buffer.endswith("\n") else ""
        for line in lines:
            if line.startswith("[TLS CONTROL] PAIRING_CODE "):
                self.pairing_code = line.rsplit(" ", 1)[-1]
                self.pairingCodeChanged.emit(self.pairing_code)
            elif "Pairing accepted" in line or "Client authenticated" in line:
                self._set_status("Status: Streaming securely")

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
            if self.encrypted:
                args.append("--local-udp")
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
        self.logAppended.emit("STREAMER", raw)
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
            if "Setting pipeline to PLAYING" in line or "New clock:" in line:
                self._set_primary_ready(True)
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
            self._set_primary_ready(True)
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
        if self.de == "gnome" and code and self.streaming:
            message = self.status or "GNOME virtual display failed — see logs"
            self.stop()
            self._set_status(message)
        elif self.de == "kde" and code and self.streaming:
            message = self.status or "KDE streaming setup failed — see logs"
            self.stop()
            self._set_status(message)

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

    def start_third(self, res, fps, bitrate, encoder, encoder_profile):
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

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(third_encoder, "cpu"))
        env.insert("MONITORIZE_ENCODER_PROFILE", third_encoder_profile)
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
                "Choose the display to stream as Monitorize's third display.",
            )
        env.insert(
            "MONITORIZE_PORT",
            str(THIRD_STREAM_BACKEND_PORT if self.encrypted else THIRD_STREAM_PUBLIC_PORT),
        )
        env.insert(
            "MONITORIZE_HOST",
            "127.0.0.1" if (self.encrypted or not self.wifi) else "0.0.0.0",
        )

        self.third_generation += 1
        generation = self.third_generation
        self.third_width = width
        self.third_height = height
        self.third_fps = third_fps
        self.third_bitrate = third_bitrate
        self.third_encoder = third_encoder
        self.third_encoder_profile = third_encoder_profile
        self.third_ready = False
        self.third_streaming = True
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
        module = {
            "kde": "monitorize.streaming.Streamer_kde",
            "gnome": "monitorize.streaming.Streamer_gnome",
            "hyprland": "monitorize.streaming.Streamer_hyprland",
        }[self.de]
        process.start(sys.executable, [
            "-m", module,
            str(width), str(height), str(third_fps), str(third_bitrate),
            "wifi" if self.wifi else "usb",
        ])
        self.secondStreamChanged.emit(True)
        self._advertise()
        action = (
            "Creating KDE virtual display" if self.de == "kde" else
            "Creating GNOME virtual display" if self.de == "gnome" else
            "Portal picker opened"
        )
        self.logAppended.emit(
            "STREAMER",
            f"[Third display] {action} on port "
            f"{THIRD_STREAM_PUBLIC_PORT if not self.encrypted else THIRD_STREAM_BACKEND_PORT}.",
        )

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
            if event and event.get("type") == "kde_output_ready":
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
            if "Setting pipeline to PLAYING" in line or "New clock:" in line:
                if not self.third_ready:
                    self.third_ready = True
                    self._advertise()
                    self._set_status("Third display streaming")

    def _third_streamer_finished(
        self, code, _status, generation=None, process=None
    ):
        if (
            generation is not None
            and (generation != self.third_generation or process is not self.third_streamer)
        ):
            return
        self.logAppended.emit("STREAMER", f"[Third display] Streamer exited (code {code})")
        self.third_streamer = None
        self.third_streaming = False
        self.third_ready = False
        self.third_gst_pids.clear()
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
            },
            "general": general,
            "third": {"enabled": False},
        }
        if self.wifi:
            config["wifi"] = {
                "stream_type": self.env.value("MONITORIZE_STREAM_TYPE"),
                "use_encryption": self.encrypted,
            }
        if self.third_streaming:
            config["third"] = {
                "enabled": True,
                "resolution": f"{self.third_width}x{self.third_height}",
                "fps": str(self.third_fps),
                "bitrate": str(self.third_bitrate),
                "encoder": self.third_encoder,
                "encoder_profile": self.third_encoder_profile,
            }
        return config

    def stop_third(self):
        if self.de == "gnome":
            self._save_gnome_virtual_layout()
        self.third_generation += 1
        if self.third_streamer is not None:
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
        self.gnome_outputs.pop("additional", None)
        self._advertise()
        self.secondStreamChanged.emit(False)
        self.logAppended.emit("STREAMER", "[Third display] Stopped.")

    def _advertise(self, *_args):
        if self.streaming and self.wifi:
            self.discovery.advertise(
                self.local_ip,
                self.encrypted,
                self.third_ready,
                self.fps,
            )

    def stop(self):
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
        self.streamer_has_pipewire_node = False
        self.kde_event_buffer = ""
        self.gnome_event_buffer = ""
        self._set_primary_ready(False)
        self.runtime_general = None
        if self.third_streaming or self.third_streamer is not None:
            self.stop_third()
        stop_processes(self.streamer, self.input_bridge, self.tls_proxy)
        self.streamer = self.input_bridge = self.tls_proxy = None
        kill_tracked_pids(self.gst_pids)
        kill_patterns(
            "gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112",
            "gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115",
            "monitorize\\.streaming\\.Streamer_.*",
            "monitorize\\.security\\.tls_proxy",
            "monitorize-kde-virtual-output",
        )
        self.display.cleanup()
        self.gnome_outputs.clear()
        self.discovery.stop_advertising()
        self.pairing_code = ""
        self.pairingCodeChanged.emit("")
        self._set_streaming(False)
