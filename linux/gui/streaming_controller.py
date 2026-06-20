"""Primary stream, TLS proxy and input bridge lifecycle."""

import os
import secrets
import subprocess
import sys

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from gui.display_controller import DisplayController
from gui.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from gui.settings import load_general_settings, load_wifi_settings
from gui.third_stream_controller import ThirdStreamController
from gui.utils import LINUX_DIR


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
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)

    def _set_streaming(self, value):
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
        self.wifi = wifi
        try:
            width, height = map(int, (res or "").split()[0].split("x"))
        except ValueError:
            width, height = 1920, 1200
        self.width, self.height = width, height
        self.fps, self.bitrate = int(fps), int(bitrate)
        self.display_type, self.encoder = display_type, encoder
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(encoder, "cpu"))
        settings = load_wifi_settings() if wifi else {}
        self.encrypted = settings.get("use_encryption", True) if wifi else False
        self.env.insert("MONITORIZE_STREAM_TYPE", settings.get("stream_type", "Speed"))
        if self.encrypted:
            self.env.insert("MONITORIZE_HOST", "127.0.0.1")
            self.env.insert("MONITORIZE_PORT", "7112")
        if self.de in ("kde", "hyprland", "sway") and display_type == "Extend":
            self.env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")
        if wifi:
            subprocess.run(["adb", "reverse", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)
            if self.encrypted:
                self._launch_tls()
        self._set_streaming(True)
        self._prepare_display()

    def _prepare_display(self):
        if self.display_type == "Mirror" and self.de in ("kde", "hyprland"):
            self._launch_streamer()
        elif self.de == "kde":
            self._set_status("Starting virtual monitor…  5")
            subprocess.run(["killall", "krfb-virtualmonitor"], capture_output=True)
            self.krfb = QProcess(self)
            self.krfb.setWorkingDirectory(LINUX_DIR)
            self.krfb.setProcessEnvironment(self.env)
            self.krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.krfb.readyReadStandardOutput.connect(self._read_krfb)
            self.krfb.finished.connect(
                lambda code, _status: self.logAppended.emit(
                    "KRFB", f"Process exited (code {code})"
                )
            )
            self.krfb.start("krfb-virtualmonitor", [
                "--resolution", f"{self.width}x{self.height}",
                "--name", "TabletDisplay",
                "--password", secrets.token_urlsafe(6),
                "--port", "5900",
            ])
            self._start_countdown(1)
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

    def _launch_streamer(self):
        self.streamer = QProcess(self)
        self.streamer.setWorkingDirectory(LINUX_DIR)
        self.streamer.setProcessEnvironment(self.env)
        self.streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.streamer.finished.connect(self._streamer_finished)
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
        if self.de in ("kde", "gnome"):
            QTimer.singleShot(400, self._launch_input)
        else:
            self.input_launched = False
            self.streamer_buffer = ""
        self._set_status("Status: Streaming…")

    def _launch_tls(self):
        self.tls_proxy = QProcess(self)
        self.tls_proxy.setWorkingDirectory(LINUX_DIR)
        self.tls_proxy.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.tls_proxy.readyReadStandardOutput.connect(self._read_tls)
        self.tls_proxy.start(sys.executable, [os.path.join(LINUX_DIR, "tls_proxy.py")])

    def _read_tls(self):
        self.tls_buffer += bytes(self.tls_proxy.readAllStandardOutput()).decode(
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

    def _launch_input(self):
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
        self.input_bridge.setWorkingDirectory(LINUX_DIR)
        self.input_bridge.setProcessEnvironment(self.env)
        self.input_bridge.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.input_bridge.readyReadStandardOutput.connect(self._read_input)
        self.input_bridge.finished.connect(self._input_finished)
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

    def _read_krfb(self):
        self.logAppended.emit(
            "KRFB",
            bytes(self.krfb.readAllStandardOutput()).decode("utf-8", errors="replace"),
        )

    def _read_streamer(self):
        raw = bytes(self.streamer.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.logAppended.emit("STREAMER", raw)
        for line in raw.splitlines():
            if "[GStreamer] PID:" in line:
                try:
                    self.gst_pids.add(int(line.split("PID:")[1].strip()))
                except ValueError:
                    pass
        if self.de in ("hyprland", "sway") and not self.input_launched:
            self.streamer_buffer += raw
            if "[Portal] Got PipeWire node=" in self.streamer_buffer:
                self.input_launched = True
                QTimer.singleShot(500, self._launch_input)

    def _read_input(self):
        self.logAppended.emit(
            "INPUT",
            bytes(self.input_bridge.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            ),
        )

    def _streamer_finished(self, code, _status):
        self.logAppended.emit("STREAMER", f"Process exited (code {code})")
        if self.de == "sway" and code and self.streaming:
            self._set_status("Sway capture failed — check xdg-desktop-portal-wlr")
        elif self.de == "gnome" and code and self.streaming:
            self.logAppended.emit(
                "STREAMER", "↺  GNOME streamer crashed — auto-restarting in 2s…"
            )
            self._set_status("↺  Stream reconnecting after display config change…")
            QTimer.singleShot(2000, self._restart_gnome)

    def _input_finished(self, code, _status):
        self.logAppended.emit("INPUT", f"Bridge exited (code {code})")
        if code == 0 and self.streaming:
            self.logAppended.emit(
                "INPUT",
                "ℹ️  Touch input not available — streaming continues without touch.",
            )

    def _restart_gnome(self):
        if not self.streaming:
            return
        kill_tracked_pids(self.gst_pids)
        kill_patterns("gst-launch-1.0.*port=7110", "gst-launch-1.0.*port=7112")
        self._launch_streamer()
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
        self.countdown_timer.stop()
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
