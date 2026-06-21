"""Primary stream, TLS proxy and input bridge lifecycle."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from gui.display_controller import DisplayController
from gui.kde_virtual_monitor import (
    ensure_krfb_virtualmonitor_desktop_entry,
    should_use_legacy_krfb,
)
from gui.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from gui.settings import load_general_settings, load_wifi_settings
from gui.third_stream_controller import ThirdStreamController
from gui.utils import LINUX_DIR
from gui.validation import (
    DEFAULT_PRIMARY_RESOLUTION,
    sanitize_bitrate,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_fps,
    sanitize_resolution,
)


class StreamingController(QObject):
    streamingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    countdownChanged = pyqtSignal(int)
    pairingCodeChanged = pyqtSignal(str)
    secondStreamChanged = pyqtSignal(bool)
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
        self.krfb = self.streamer = self.input_bridge = self.tls_proxy = None
        self.gst_pids = set()
        self.tls_buffer = ""
        self.input_launched = False
        self.generation = 0
        self.kde_display_name = "monitorize"
        self.kde_display_baseline = None
        self.kde_ready_generation = None
        self.kde_ready_process = None
        self.kde_ready_deadline = 0.0
        self.kde_ready_fallback_at = 0.0
        self.streamer_has_pipewire_node = False
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        self.kde_ready_timer = QTimer(self)
        self.kde_ready_timer.setInterval(500)
        self.kde_ready_timer.timeout.connect(self._check_kde_virtual_display)

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

    def start(self, res, fps, bitrate, display_type, encoder, wifi):
        self.stop()
        self.generation += 1
        self.wifi = wifi
        width, height = sanitize_resolution(res, DEFAULT_PRIMARY_RESOLUTION)
        self.width, self.height = width, height
        self.fps, self.bitrate = sanitize_fps(fps), sanitize_bitrate(bitrate)
        self.display_type = sanitize_display_type(display_type)
        self.encoder = sanitize_encoder(encoder)
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(self.encoder, "cpu"))
        settings = load_wifi_settings() if wifi else {}
        self.encrypted = settings.get("use_encryption", True) if wifi else False
        self.env.insert("MONITORIZE_STREAM_TYPE", settings.get("stream_type", "Speed"))
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
            if self._use_legacy_kde_krfb():
                self._prepare_kde_krfb_virtual_display()
            else:
                self.kde_display_baseline = (
                    self._kde_output_snapshot()
                    or self._kde_connected_output_ids()
                )
                self.env.insert("MONITORIZE_PORTAL_SOURCE_TYPE", "4")
                self.env.insert(
                    "MONITORIZE_PORTAL_SELECTOR_HINT",
                    "KDE will create a virtual monitor for Monitorize.",
                )
                self._set_status("Opening KDE virtual display portal…")
                self._set_streaming(True)
                self._launch_streamer()
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

    def _use_legacy_kde_krfb(self):
        return should_use_legacy_krfb(
            lambda message: self.logAppended.emit("KRFB", message)
        )

    def _prepare_kde_krfb_virtual_display(self):
        self._set_status("Starting KDE virtual monitor…")
        alias_message = ensure_krfb_virtualmonitor_desktop_entry()
        if alias_message:
            self.logAppended.emit("KRFB", alias_message)
        self.kde_display_baseline = (
            self._kde_output_snapshot()
            or self._kde_connected_output_ids()
        )
        self.krfb = QProcess(self)
        self.krfb.setWorkingDirectory(LINUX_DIR)
        self.krfb.setProcessEnvironment(self.env)
        self.krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        generation = self.generation
        process = self.krfb
        self.krfb.readyReadStandardOutput.connect(
            lambda: self._read_krfb(generation, process)
        )
        self.krfb.finished.connect(
            lambda code, status: self._krfb_finished(code, status, generation, process)
        )
        self.krfb.errorOccurred.connect(
            lambda _error: self._krfb_error(generation, process)
        )
        self.krfb.start("krfb-virtualmonitor", [
            "--resolution", f"{self.width}x{self.height}",
            "--name", self.kde_display_name,
            "--password", secrets.token_urlsafe(6),
            "--port", self._allocate_kde_virtual_monitor_port(),
        ])
        self.logAppended.emit(
            "KRFB",
            f"Started KDE virtual display helper for {self.kde_display_name}.",
        )
        self._start_kde_virtual_display_wait(generation, process)

    def _fail(self, message):
        self.logAppended.emit("STREAMER", f"ERROR: {message}")
        self._set_status(message)
        self._set_streaming(False)

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

    def _start_kde_virtual_display_wait(self, generation, process):
        self.countdown = 0
        self.countdownChanged.emit(0)
        self.kde_ready_generation = generation
        self.kde_ready_process = process
        now = time.monotonic()
        self.kde_ready_deadline = now + 8.0
        self.kde_ready_fallback_at = now + 2.0
        self._set_status(f"Waiting for KDE virtual display {self.kde_display_name}…")
        self.kde_ready_timer.start()

    @staticmethod
    def _allocate_kde_virtual_monitor_port():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                return str(sock.getsockname()[1])
        except OSError:
            return "5900"

    @staticmethod
    def _parse_kscreen_outputs(raw):
        outputs = set()
        current = None
        connected = enabled = False
        for line in raw.splitlines():
            if line.startswith("Output:"):
                if current and connected and enabled:
                    outputs.add(current)
                parts = line.split()
                current = parts[1] if len(parts) > 1 else line
                connected = enabled = False
            elif current:
                stripped = line.strip().lower()
                if stripped == "connected":
                    connected = True
                elif stripped == "enabled":
                    enabled = True
        if current and connected and enabled:
            outputs.add(current)
        return outputs

    def _kde_connected_output_ids(self):
        try:
            result = subprocess.run(
                ["kscreen-doctor", "-o"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                return self._parse_kscreen_outputs(result.stdout)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None

    def _kde_outputs_json(self):
        try:
            result = subprocess.run(
                ["kscreen-doctor", "-j"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return json.loads(result.stdout).get("outputs", [])
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return []

    def _kde_output_snapshot(self):
        outputs = self._kde_outputs_json()
        if not outputs:
            return None
        return {
            "ids": {
                str(output.get("id"))
                for output in outputs
                if output.get("connected") and output.get("enabled")
            },
            "names": {
                str(output.get("name"))
                for output in outputs
                if output.get("connected") and output.get("enabled")
            },
            "outputs": {
                str(output.get("name")): {
                    "id": str(output.get("id")),
                    "type": output.get("type"),
                    "priority": output.get("priority"),
                    "currentModeId": output.get("currentModeId"),
                    "scale": output.get("scale"),
                }
                for output in outputs
                if output.get("connected") and output.get("enabled")
            },
        }

    @staticmethod
    def _is_kde_virtual_output(output):
        name = str(output.get("name", "")).lower()
        return name.startswith("virtual-")

    @staticmethod
    def _is_safe_kde_virtual_output(output):
        name = str(output.get("name", ""))
        if not name.lower().startswith("virtual-"):
            return False
        if not output.get("connected") or not output.get("enabled"):
            return False
        if output.get("primary") is True or output.get("priority") == 1:
            return False
        return True

    def _kde_baseline_ids_and_names(self):
        baseline = self.kde_display_baseline
        if not baseline:
            return None, None
        if isinstance(baseline, dict):
            return set(baseline.get("ids", ())), set(baseline.get("names", ()))
        values = {str(value) for value in baseline}
        return values, values

    def _kde_portal_virtual_output(self):
        outputs = [
            output for output in self._kde_outputs_json()
            if output.get("connected") and output.get("enabled")
        ]
        if not outputs:
            return None
        portal_virtual = self.env.value("MONITORIZE_PORTAL_SOURCE_TYPE") == "4"
        if not portal_virtual:
            expected_name = f"Virtual-{self.kde_display_name}".lower()
            return next(
                (
                    output for output in outputs
                    if self._is_safe_kde_virtual_output(output)
                    and str(output.get("name", "")).lower() == expected_name
                ),
                None,
            )

        baseline_ids, baseline_names = self._kde_baseline_ids_and_names()
        if baseline_ids is None or baseline_names is None:
            return None
        candidates = [
            output for output in outputs
            if self._is_safe_kde_virtual_output(output)
            and str(output.get("id")) not in baseline_ids
            and str(output.get("name")) not in baseline_names
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _configure_kde_virtual_display(self, generation=None, attempt=0, on_done=None):
        generation = self.generation if generation is None else generation
        if generation != self.generation:
            return
        output = self._kde_portal_virtual_output()
        if not output:
            if attempt < 16:
                QTimer.singleShot(
                    300,
                    lambda: self._configure_kde_virtual_display(
                        generation, attempt + 1, on_done
                    ),
                )
            else:
                self.logAppended.emit(
                    "KRFB",
                    (
                        "KDE virtual output could not be identified safely; "
                        "continuing without changing any display mode or scale."
                    ),
                )
                if callable(on_done) and generation == self.generation:
                    on_done()
            return

        output_ref = str(output.get("name") or output.get("id"))
        output_name = str(output.get("name") or output_ref)
        self.env.insert("MONITORIZE_OUTPUT", output_name)
        mode = f"{self.width}x{self.height}@{self.fps}"
        refresh_mhz = self.fps * 1000
        add_mode_command = [
            "kscreen-doctor",
            (
                f"output.{output_ref}.addCustomMode."
                f"{self.width}.{self.height}.{refresh_mhz}.full"
            ),
        ]
        mode_command = ["kscreen-doctor", f"output.{output_ref}.mode.{mode}"]
        scale_command = ["kscreen-doctor", f"output.{output_ref}.scale.1.0"]
        try:
            add_mode = subprocess.run(
                add_mode_command, capture_output=True, text=True, timeout=3
            )
            if add_mode.returncode != 0:
                warning = add_mode.stderr.strip() or add_mode.stdout.strip()
                self.logAppended.emit(
                    "KRFB",
                    (
                        f"KDE custom mode registration returned "
                        f"{add_mode.returncode} for {output_name}: {warning}"
                    ),
                )
            mode_result = subprocess.run(
                mode_command, capture_output=True, text=True, timeout=3
            )
            scale_result = subprocess.run(
                scale_command, capture_output=True, text=True, timeout=3
            )
            if mode_result.returncode == 0 and scale_result.returncode == 0:
                self.logAppended.emit(
                    "KRFB",
                    f"Configured KDE virtual output {output_name} to {mode} scale 1.0.",
                )
            else:
                error = (
                    mode_result.stderr.strip()
                    or scale_result.stderr.strip()
                    or mode_result.stdout.strip()
                    or scale_result.stdout.strip()
                )
                self.logAppended.emit(
                    "KRFB",
                    f"Could not set KDE virtual output {output_name} to {mode}: {error}",
                )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            self.logAppended.emit(
                "KRFB",
                f"Could not configure KDE virtual output {output_name}: {exc}",
            )
        finally:
            if callable(on_done) and generation == self.generation:
                on_done()

    def _configure_kde_portal_virtual_display(self, generation=None, attempt=0, on_done=None):
        self._configure_kde_virtual_display(generation, attempt, on_done)

    def _kde_virtual_display_visible(self):
        outputs = self._kde_connected_output_ids()
        if outputs is not None:
            baseline = self.kde_display_baseline
            if isinstance(baseline, dict):
                baseline = set(baseline.get("ids", ()))
            if baseline is not None and outputs - set(baseline):
                return True
            if any(self.kde_display_name in output for output in outputs):
                return True
            return False
        app = QGuiApplication.instance()
        screens = app.screens() if app and hasattr(app, "screens") else []
        if any(self.kde_display_name in (screen.name() or "") for screen in screens):
            return True
        return time.monotonic() >= self.kde_ready_fallback_at

    def _check_kde_virtual_display(self):
        generation = self.kde_ready_generation
        process = self.kde_ready_process
        if (
            generation != self.generation
            or process is not self.krfb
        ):
            self.kde_ready_timer.stop()
            return
        if process.state() == QProcess.ProcessState.NotRunning:
            self._fail_kde_virtual_display(
                "KDE virtual display did not stay active", process
            )
            return
        if self._kde_virtual_display_visible():
            self.kde_ready_timer.stop()
            self.kde_ready_generation = self.kde_ready_process = None
            self.logAppended.emit(
                "KRFB",
                f"KDE virtual display {self.kde_display_name} is active.",
            )
            self._set_status(
                f"KDE virtual display {self.kde_display_name} is ready; applying mode…"
            )
            self._set_streaming(True)
            self._configure_kde_virtual_display(
                generation,
                on_done=lambda: self._launch_streamer(generation),
            )
            return
        if time.monotonic() >= self.kde_ready_deadline:
            self._fail_kde_virtual_display(
                "KDE virtual display did not stay active", process
            )

    def _fail_kde_virtual_display(self, message, process=None):
        self.kde_ready_timer.stop()
        self.kde_ready_generation = self.kde_ready_process = None
        self.kde_display_baseline = None
        self.countdown_timer.stop()
        krfb = self.krfb if process is None or process is self.krfb else None
        if krfb is not None:
            self.krfb = None
        stop_processes(krfb, self.streamer, self.input_bridge, self.tls_proxy)
        self.streamer = self.input_bridge = self.tls_proxy = None
        self.discovery.stop_advertising()
        if self.pairing_code:
            self.pairing_code = ""
            self.pairingCodeChanged.emit("")
        self._fail(message)

    def _launch_streamer(self, generation=None):
        generation = self.generation if generation is None else generation
        if not self.streaming or generation != self.generation:
            return
        self.streamer_has_pipewire_node = False
        self.streamer = QProcess(self)
        process = self.streamer
        self.streamer.setWorkingDirectory(LINUX_DIR)
        self.streamer.setProcessEnvironment(self.env)
        self.streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
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
        script = {
            "kde": "Streamer_kde.py",
            "gnome": "Streamer_gnome.py",
            "hyprland": "Streamer_hyprland.py",
            "sway": "Streamer_hyprland.py",
        }.get(self.de, "Streamer_gnome.py")
        args = [
            os.path.join(LINUX_DIR, script),
            str(self.width), str(self.height), str(self.fps), str(self.bitrate),
            "wifi" if self.wifi else "usb",
        ]
        if self.de in ("hyprland", "sway"):
            args.append(self.display.created_output or "mirror")
        if self.de == "gnome":
            args += ["1.0", self.display_type.replace(" ", "_")]
        self.streamer.start(sys.executable, args)
        self._advertise()
        if self.de == "kde" and self.env.value("MONITORIZE_PORTAL_SOURCE_TYPE") == "4":
            self.input_launched = False
        elif self.de in ("kde", "gnome"):
            QTimer.singleShot(400, lambda: self._launch_input(generation))
        else:
            self.input_launched = False
            self.streamer_buffer = ""
        self._set_status("Status: Streaming…")

    def _launch_tls(self):
        self.tls_proxy = QProcess(self)
        generation = self.generation
        process = self.tls_proxy
        self.tls_proxy.setWorkingDirectory(LINUX_DIR)
        self.tls_proxy.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
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
        self.tls_proxy.start(sys.executable, [os.path.join(LINUX_DIR, "tls_proxy.py")])

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
        general = load_general_settings()
        touch = general.get("enable_touch", True)
        stylus = (
            general.get("enable_stylus_features", False)
            and self.de in ("kde", "gnome", "hyprland", "sway")
        )
        if not touch and not stylus:
            self.logAppended.emit("INPUT", "Input is disabled in settings.")
            return
        self.input_bridge = QProcess(self)
        process = self.input_bridge
        self.input_bridge.setWorkingDirectory(LINUX_DIR)
        self.input_bridge.setProcessEnvironment(self.env)
        self.input_bridge.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
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
        args = [os.path.join(LINUX_DIR, "touch_daemon.py"), str(self.width), str(self.height)]
        if self.wifi and not self.encrypted:
            args.append("--wifi")
        if stylus:
            args.append("--stylus-features")
        if stylus and not touch:
            args.append("--stylus-only")
        self.input_bridge.start(sys.executable, args)
        if stylus:
            self._set_status("Stylus input starting via uinput…")
        elif self.de in ("hyprland", "sway"):
            self._set_status("Touch service starting via uinput…")
        else:
            self._set_status("Touch service starting…")

    def _read_krfb(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.krfb if process is None else process
        if generation != self.generation or process is not self.krfb:
            return
        self.logAppended.emit(
            "KRFB",
            bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace"),
        )

    def _krfb_error(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.krfb if process is None else process
        if generation != self.generation or process is not self.krfb:
            return
        message = f"KDE virtual display helper error: {process.errorString()}"
        self.logAppended.emit("KRFB", message)
        if not self.streamer_has_pipewire_node:
            self._fail_kde_virtual_display(
                "KDE virtual display did not stay active", process
            )

    def _krfb_finished(self, code, _status, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.krfb if process is None else process
        if generation != self.generation or process is not self.krfb:
            return
        self.logAppended.emit("KRFB", f"Process exited (code {code})")
        if not self.streamer_has_pipewire_node:
            self._fail_kde_virtual_display(
                "KDE virtual display did not stay active", process
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
            if "[GStreamer] PID:" in line:
                try:
                    self.gst_pids.add(int(line.split("PID:")[1].strip()))
                except ValueError:
                    pass
            if self.de == "kde":
                if "[Portal] Creating session" in line:
                    if self.env.value("MONITORIZE_PORTAL_SOURCE_TYPE") == "4":
                        self._set_status(
                            "KDE portal opened — approve the virtual display."
                        )
                    elif self._kde_virtual_display_visible():
                        self._set_status(
                            f"KDE picker opened — select {self.kde_display_name}."
                        )
                    else:
                        self._set_status(
                            "KDE virtual display disappeared before portal selection"
                        )
                elif "[Portal] Got PipeWire node=" in line:
                    self.streamer_has_pipewire_node = True
                    self._set_status("KDE display selected; stream pipeline starting…")
                    if (
                        self.env.value("MONITORIZE_PORTAL_SOURCE_TYPE") == "4"
                        and not self.input_launched
                    ):
                        self.input_launched = True
                        current_generation = self.generation
                        self._configure_kde_portal_virtual_display(
                            current_generation,
                            on_done=lambda: QTimer.singleShot(
                                500, lambda: self._launch_input(current_generation)
                            ),
                        )
                    else:
                        self._configure_kde_portal_virtual_display(self.generation)
                elif "[ERROR] No streams" in line:
                    self._set_status(
                        "KDE portal returned no display; the virtual display may have disappeared"
                    )
                elif "Portal denied" in line:
                    self._set_status("KDE portal selection was cancelled or denied")
        if self.de in ("hyprland", "sway") and not self.input_launched:
            self.streamer_buffer += raw
            if "[Portal] Got PipeWire node=" in self.streamer_buffer:
                self.input_launched = True
                generation = self.generation
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
            self._set_status("↺  Stream reconnecting after display config change…")
            current_generation = self.generation
            QTimer.singleShot(
                2000, lambda: self._restart_gnome(current_generation)
            )

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
        kill_tracked_pids(self.gst_pids)
        kill_patterns("gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112")
        self._launch_streamer(self.generation)
        self._set_status("Status: Streaming…  (restarted)")

    def start_third(self, res, fps, bitrate, encoder):
        self.third.start(res, fps, bitrate, encoder, self.encrypted)

    def stop_third(self):
        self.third.stop()
        self._advertise()
        self.logAppended.emit("STREAMER", "[Third display] Stopped.")

    def _advertise(self, *_args):
        if self.streaming and self.wifi:
            self.discovery.advertise(self.local_ip, self.encrypted, self.third.ready)

    def stop(self):
        self.generation += 1
        self.countdown_timer.stop()
        self.kde_ready_timer.stop()
        self.kde_ready_generation = self.kde_ready_process = None
        self.kde_display_baseline = None
        self.streamer_has_pipewire_node = False
        self.third.stop()
        stop_processes(self.krfb, self.streamer, self.input_bridge, self.tls_proxy)
        self.krfb = self.streamer = self.input_bridge = self.tls_proxy = None
        kill_tracked_pids(self.gst_pids)
        kill_patterns(
            "gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112",
            "gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115",
            "Streamer_.*\\.py", "tls_proxy.py",
        )
        self.display.cleanup()
        self.discovery.stop_advertising()
        self.pairing_code = ""
        self.pairingCodeChanged.emit("")
        self._set_streaming(False)
