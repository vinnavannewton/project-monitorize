"""Primary stream, TLS proxy and input bridge lifecycle."""

import os
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
from monitorize.platform.kde_virtual_monitor import save_current_virtual_layout
from monitorize.platform.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from monitorize.config.settings import (
    load_general_settings,
    load_gnome_virtual_layout,
    load_wifi_settings,
)
from monitorize.desktop.third_stream_controller import ThirdStreamController
from monitorize.platform.utils import LINUX_DIR
from monitorize.config.validation import (
    DEFAULT_PRIMARY_RESOLUTION,
    sanitize_bitrate,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_encoder_profile,
    sanitize_fps,
    sanitize_resolution,
)


GNOME_LAYOUT_SAVE_INTERVAL_MS = 1000
GNOME_LAYOUT_CHANGE_DEBOUNCE_MS = 750
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
        self.third = ThirdStreamController(self)
        self.third.activeChanged.connect(self.secondStreamChanged)
        self.third.readinessChanged.connect(self._advertise)
        self.third.logAppended.connect(self.logAppended)
        self.streaming = False
        self.status = ""
        self.countdown = 0
        self.pairing_code = ""
        self.wifi = False
        self.encrypted = False
        self.streamer = self.input_bridge = self.tls_proxy = None
        self.gst_pids = set()
        self.tls_buffer = ""
        self.input_launched = False
        self.generation = 0
        self.streamer_has_pipewire_node = False
        self.kde_portal_terminal_error = False
        self.primary_ready = False
        self.encoder_profile = "Low Latency"
        self.runtime_general = None
        self.pending_third = None
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        self.gnome_layout_timer = QTimer(self)
        self.gnome_layout_timer.setInterval(GNOME_LAYOUT_SAVE_INTERVAL_MS)
        self.gnome_layout_timer.timeout.connect(self._save_gnome_virtual_layout)
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
        self.pending_third = options.get("third")
        if self.encrypted:
            self.env.insert("MONITORIZE_HOST", "127.0.0.1")
            self.env.insert("MONITORIZE_PORT", "7112")
        if self.de in ("kde", "hyprland", "sway") and self.display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
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
            self._prepare_kde_portal_virtual_display()
        elif self.de == "hyprland":
            self._set_status("Setting up virtual monitor on Hyprland…")
            output, error = self.display.prepare_hyprland(
                self.width, self.height, self.fps
            )
            if error:
                self._fail(error)
                return
            self.logAppended.emit("STREAMER", f"Created headless monitor: {output}")
            self._start_countdown(2)
        elif self.de == "sway":
            if self.display_type == "Mirror":
                output = self.display.sway_mirror_output()
                if not output:
                    self._fail("Sway has no active output to mirror")
                    return
                self.env.insert("MONITORIZE_OUTPUT", output)
                self._launch_streamer()
            else:
                self._set_status("Setting up virtual monitor on Sway…")
                output, error = self.display.prepare_sway(
                    self.width, self.height, self.fps
                )
                if error:
                    self._fail(error)
                    return
                self.env.insert("MONITORIZE_OUTPUT", output)
                self._start_countdown(2)
        else:
            self._launch_streamer()

    def _prepare_kde_portal_virtual_display(self):
        self.env.insert("MONITORIZE_PORTAL_SOURCE_TYPE", "4")
        self.env.insert("MONITORIZE_VIRTUAL_SLOT", "primary")
        self.env.insert(
            "MONITORIZE_PORTAL_SELECTOR_HINT",
            "KDE will create a virtual monitor for Monitorize.",
        )
        self._set_status("Opening KDE virtual display portal…")
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
        self.kde_portal_terminal_error = False
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
            "sway": "monitorize.streaming.Streamer_hyprland",
        }.get(self.de, "monitorize.streaming.Streamer_gnome")
        args = [
            "-m", module,
            str(self.width), str(self.height), str(self.fps), str(self.bitrate),
            "wifi" if self.wifi else "usb",
        ]
        if self.de in ("hyprland", "sway"):
            args.append(self.display.created_output or "mirror")
        if self.de == "gnome":
            args += ["1.0", self.display_type.replace(" ", "_")]
            position = load_gnome_virtual_layout("primary")["position"]
            if position:
                args += [str(position[0]), str(position[1])]
        self.streamer.start(sys.executable, args)
        self._start_gnome_layout_tracking()
        self._advertise()
        if self._uses_kde_portal_virtual_source():
            self.input_launched = False
        elif self.de in ("kde", "gnome"):
            QTimer.singleShot(400, lambda: self._launch_input(generation))
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
            and self.de in ("kde", "gnome", "hyprland", "sway")
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
        if stylus:
            args.append("--stylus-features")
        if stylus and not touch:
            args.append("--stylus-only")
        self.input_bridge.start(sys.executable, args)
        if stylus:
            self._set_status("Stylus input starting via uinput…")
        else:
            self._set_status("Touch service starting via uinput…")

    def _uses_kde_portal_virtual_source(self):
        return self.de == "kde" and self.env.value("MONITORIZE_PORTAL_SOURCE_TYPE") == "4"

    def _stopping_kde_portal_streamer(self):
        return (
            self.streamer is not None
            and hasattr(self, "env")
            and self._uses_kde_portal_virtual_source()
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
        for line in raw.splitlines():
            self._track_gst_pid(line)
            if "Setting pipeline to PLAYING" in line or "New clock:" in line:
                self._set_primary_ready(True)
            if self.de == "kde":
                self._handle_kde_streamer_line(line, generation)
        self._maybe_start_wlroots_input(raw, generation)

    def _track_gst_pid(self, line):
        if "[GStreamer] PID:" not in line:
            return
        try:
            self.gst_pids.add(int(line.split("PID:")[1].strip()))
        except ValueError:
            pass

    def _handle_kde_streamer_line(self, line, generation):
        if "[Portal] Creating session" in line:
            self._set_status("KDE portal opened — approve the virtual display.")
        elif "[Portal] Virtual output ready name=" in line:
            output_name = line.split("name=", 1)[1].split(" mode=", 1)[0].strip()
            if output_name.lower().startswith("virtual-"):
                self.env.insert("MONITORIZE_OUTPUT", output_name)
            self._set_status(f"KDE virtual display configured: {output_name}")
        elif "[ERROR] KDE virtual display configuration failed:" in line:
            self.kde_portal_terminal_error = True
            self._set_status(line.split("failed:", 1)[1].strip())
        elif "[Portal] Got PipeWire node=" in line:
            self.streamer_has_pipewire_node = True
            self._set_status("KDE display selected; stream pipeline starting…")
            self._maybe_start_kde_portal_input(generation)
        elif "[ERROR] No streams" in line:
            self.kde_portal_terminal_error = True
            self._set_status(
                "KDE portal returned no display; the virtual display may have disappeared"
            )
        elif "Portal denied" in line:
            self.kde_portal_terminal_error = True
            self._set_status("KDE portal selection was cancelled or denied")

    def _maybe_start_kde_portal_input(self, generation):
        if not self._uses_kde_portal_virtual_source() or self.input_launched:
            return
        self.input_launched = True
        QTimer.singleShot(500, lambda: self._launch_input(generation))

    def _maybe_start_wlroots_input(self, raw, generation):
        if self.de in ("hyprland", "sway") and not self.input_launched:
            self.streamer_buffer += raw
            if "[Portal] Got PipeWire node=" in self.streamer_buffer:
                self.input_launched = True
                QTimer.singleShot(500, lambda: self._launch_input(generation))

    def _read_input(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.input_bridge if process is None else process
        if generation != self.generation or process is not self.input_bridge:
            return
        self.logAppended.emit(
            "INPUT",
            bytes(process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            ),
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
        if self.de == "sway" and code and self.streaming:
            self._set_status("Sway capture failed — check xdg-desktop-portal-wlr")
        elif self.de == "gnome" and code and self.streaming:
            self.logAppended.emit(
                "STREAMER", "↺  GNOME streamer crashed — auto-restarting in 2s…"
            )
            self._set_status("↺  GNOME streamer crashed — restarting…")
            current_generation = self.generation
            QTimer.singleShot(
                2000, lambda: self._restart_gnome(current_generation)
            )
        elif self.de == "kde" and code and self.streaming:
            if (
                self._uses_kde_portal_virtual_source()
                and not self.streamer_has_pipewire_node
                and not self.kde_portal_terminal_error
            ):
                message = "KDE portal crashed while starting screen capture. Try again."
            else:
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

    def _restart_gnome(self, generation=None):
        if not self.streaming or (
            generation is not None and generation != self.generation
        ):
            return
        self._save_gnome_virtual_layout()
        kill_tracked_pids(self.gst_pids)
        kill_patterns("gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112")
        self._launch_streamer(self.generation)
        self._set_status("Status: Streaming…  (restarted)")

    def _should_track_gnome_virtual_layout(self):
        return (
            self.de == "gnome"
            and self.streaming
            and getattr(self, "display_type", "") == "Extend"
        )

    def _start_gnome_layout_tracking(self):
        if self._should_track_gnome_virtual_layout():
            self.gnome_layout_timer.start()
            self._connect_gnome_display_config_signal()

    def _stop_gnome_layout_tracking(self):
        self.gnome_layout_timer.stop()
        self.gnome_layout_change_timer.stop()
        self._disconnect_gnome_display_config_signal()

    def _save_gnome_virtual_layout(self):
        if self._should_track_gnome_virtual_layout():
            return save_current_gnome_virtual_layout("primary")
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
        self.third.start(res, fps, bitrate, encoder, encoder_profile, self.encrypted)

    def _set_primary_ready(self, value):
        if self.primary_ready == value:
            return
        self.primary_ready = value
        self.primaryReadyChanged.emit(value)
        if value and self.pending_third:
            third = self.pending_third
            self.pending_third = None
            if self.de == "kde":
                self.start_third(
                    third["resolution"],
                    third["fps"],
                    third["bitrate"],
                    third["encoder"],
                    third.get("encoder_profile", "Low Latency"),
                )
            else:
                self.logAppended.emit(
                    "STREAMER",
                    "Saved additional display skipped: it is only supported on KDE.",
                )

    def active_configuration(self):
        general = dict(self.runtime_general or load_general_settings())
        third = {"enabled": self.third.active or bool(self.pending_third)}
        if self.third.active:
            third.update({
                "resolution": f"{self.third.width}x{self.third.height}",
                "fps": str(self.third.fps),
                "bitrate": str(self.third.bitrate),
                "encoder": self.third.encoder,
                "encoder_profile": self.third.encoder_profile,
            })
        elif self.pending_third:
            third.update(self.pending_third)
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
            "third": third,
        }
        if self.wifi:
            config["wifi"] = {
                "stream_type": self.env.value("MONITORIZE_STREAM_TYPE"),
                "use_encryption": self.encrypted,
            }
        return config

    def stop_third(self):
        self.third.stop()
        self._advertise()
        self.logAppended.emit("STREAMER", "[Third display] Stopped.")

    def _advertise(self, *_args):
        if self.streaming and self.wifi:
            self.discovery.advertise(self.local_ip, self.encrypted, self.third.ready)

    def stop(self):
        stopping_kde_portal = self._stopping_kde_portal_streamer()
        self._save_gnome_virtual_layout()
        self._stop_gnome_layout_tracking()
        self.generation += 1
        self.countdown_timer.stop()
        self.streamer_has_pipewire_node = False
        self.kde_portal_terminal_error = False
        self._set_primary_ready(False)
        self.runtime_general = None
        self.pending_third = None
        self.third.stop()
        portal_clean = True
        if stopping_kde_portal:
            save_current_virtual_layout(
                "primary", self.env.value("MONITORIZE_OUTPUT", "")
            )
            portal_clean = stop_processes(self.streamer, timeout_ms=8000)
            self.streamer = None
            stop_processes(self.input_bridge, self.tls_proxy)
        else:
            stop_processes(self.streamer, self.input_bridge, self.tls_proxy)
        self.streamer = self.input_bridge = self.tls_proxy = None
        if stopping_kde_portal and portal_clean:
            self.gst_pids.clear()
        else:
            kill_tracked_pids(self.gst_pids)
            kill_patterns(
                "gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112",
                "gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115",
                "monitorize\\.streaming\\.Streamer_.*",
                "monitorize\\.security\\.tls_proxy",
            )
        self.display.cleanup()
        self.discovery.stop_advertising()
        self.pairing_code = ""
        self.pairingCodeChanged.emit("")
        self._set_streaming(False)
