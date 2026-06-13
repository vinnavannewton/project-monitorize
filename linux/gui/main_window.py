"""
Monitorize GUI — Main application window and entry point (QML Backend Bridge).
"""

import sys
import os
import subprocess
import shutil

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, QDialog, QMessageBox
)
from PyQt6.QtCore import (
    Qt, QProcess, QProcessEnvironment, QTimer, QSize, QUrl,
    pyqtSignal, pyqtProperty, pyqtSlot
)
from PyQt6.QtGui import QColor, QPalette, QIcon
from PyQt6.QtQuickWidgets import QQuickWidget

from gui.utils import (
    _make_tray_icon, detect_desktop_environment, get_local_ip, LINUX_DIR
)
from gui.settings import (
    load_general_settings, save_general_settings,
    load_usb_settings, save_usb_settings,
    load_wifi_settings, save_wifi_settings
)


class MonitorizeWindow(QMainWindow):
    """Main application window. Hosts the QML UI via QQuickWidget and
    exposes backend slots, properties, and signals to drive streaming,
    USB scanning, settings persistence, and system-tray behaviour."""

    
    detectedDeChanged = pyqtSignal(str)
    localIpChanged = pyqtSignal(str)
    usbStatusTextChanged = pyqtSignal(str)
    usbBusyChanged = pyqtSignal(bool)
    isStreamingChanged = pyqtSignal(bool)
    countdownChanged = pyqtSignal(int)
    streamingStatusChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str, str) 

    def __init__(self):
        """Initialise the window, detect the desktop environment, set up
        the system tray, and load the QML interface."""
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)

        
        app_icon_path = os.path.join(LINUX_DIR, "assets", "monitorize-icon.png")
        if os.path.exists(app_icon_path):
            self.setWindowIcon(QIcon(app_icon_path))

        
        subprocess.Popen(["killall", "-9", "gst-launch-1.0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "Streamer_.*\\.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        
        detected = detect_desktop_environment()
        if detected:
            self._detected_de = detected
        else:
            self._detected_de = self._ask_desktop_environment()

        
        self._local_ip = get_local_ip()
        self._usb_status_text = ""
        self._usb_busy = False
        self._is_streaming = False
        self._countdown = 0
        self._streaming_status = ""

        self.initial_headless_monitors = []
        if self._detected_de == "hyprland":
            self.initial_headless_monitors = self._get_current_headless_monitors()
            print(f"[Hyprland] Initial virtual monitors detected: {self.initial_headless_monitors}")
        self.created_headless_monitor = None

        self.process_krfb:          QProcess | None = None
        self.process_streamer:      QProcess | None = None
        self.process_input_bridge:  QProcess | None = None

        self._proc_adb_dev:  QProcess | None = None
        self._proc_adb_fwd:  QProcess | None = None
        self._proc_adb_fwd2:  QProcess | None = None

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        
        self._zc = None
        self._info = None
        QTimer.singleShot(0, self._setup_zeroconf)

        
        self._network_timer = QTimer(self)
        self._network_timer.setInterval(5000)
        self._network_timer.timeout.connect(self._check_network_ip)
        self._network_timer.start()

        
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon())
        self._tray.setToolTip("Monitorize")

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self._restore_from_tray)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self._quit_app)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.hide()

        
        self.quick_widget = QQuickWidget(self)
        self.quick_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        
        self.quick_widget.rootContext().setContextProperty("backend", self)

        
        qml_path = os.path.join(LINUX_DIR, "gui", "main.qml")
        self.quick_widget.setSource(QUrl.fromLocalFile(qml_path))

        
        errors = self.quick_widget.errors()
        if errors:
            print("=== QML ERRORS ===")
            for err in errors:
                print(err.toString())
            print("==================")

        self.setCentralWidget(self.quick_widget)

    
    @pyqtProperty(str, notify=detectedDeChanged)
    def detectedDe(self):
        return self._detected_de

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(str, notify=usbStatusTextChanged)
    def usbStatusText(self):
        return self._usb_status_text

    @pyqtProperty(bool, notify=usbBusyChanged)
    def usbBusy(self):
        return self._usb_busy

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self._is_streaming

    @pyqtProperty(int, notify=countdownChanged)
    def countdown(self):
        return self._countdown

    @pyqtProperty(str, notify=streamingStatusChanged)
    def streamingStatus(self):
        return self._streaming_status

    
    def set_usb_status_text(self, text):
        self._usb_status_text = text
        self.usbStatusTextChanged.emit(text)

    def set_usb_busy(self, busy):
        self._usb_busy = busy
        self.usbBusyChanged.emit(busy)

    def set_is_streaming(self, streaming):
        self._is_streaming = streaming
        self.isStreamingChanged.emit(streaming)

    def set_streaming_status(self, text):
        self._streaming_status = text
        self.streamingStatusChanged.emit(text)

    def append_log(self, type, msg):
        self.logAppended.emit(type, msg)

    
    @pyqtSlot()
    def startUsbScan(self):
        """Begin the ADB device scan → port-forward chain for USB mode."""
        self.set_usb_busy(True)
        self.set_usb_status_text("Running adb devices…")

        self._proc_adb_dev = QProcess(self)
        self._proc_adb_dev.finished.connect(self._adb_devices_done)
        self._proc_adb_dev.start("adb", ["devices"])

    def _adb_devices_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Error: adb devices failed. Is ADB installed?")
            self.set_usb_busy(False)
            return

        self.set_usb_status_text("Setting up reverse proxy tcp:7110 (video)…")
        self._proc_adb_fwd = QProcess(self)
        self._proc_adb_fwd.finished.connect(self._adb_forward_done)
        self._proc_adb_fwd.start("adb", ["reverse", "tcp:7110", "tcp:7112"])

    def _adb_forward_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Error: Reverse port setup failed. Is a device connected?")
            self.set_usb_busy(False)
            return

        self.set_usb_status_text("Setting up reverse proxy tcp:7111 (touch)…")
        self._proc_adb_fwd2 = QProcess(self)
        self._proc_adb_fwd2.finished.connect(self._adb_forward2_done)
        self._proc_adb_fwd2.start("adb", ["reverse", "tcp:7111", "tcp:7111"])

    def _adb_forward2_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Warning: tcp:7111 reverse failed — touch disabled")
        else:
            self.set_usb_status_text("Device ready!")
        self.set_usb_busy(False)

    @pyqtSlot()
    def resetUsbStatus(self):
        self.set_usb_status_text("")
        self.set_usb_busy(False)

    @pyqtSlot(result='QVariant')
    def loadUsbSettings(self):
        return load_usb_settings()

    @pyqtSlot(result='QVariant')
    def loadWifiSettings(self):
        return load_wifi_settings()

    @pyqtSlot(result='QVariant')
    def loadGeneralSettings(self):
        return load_general_settings()

    @pyqtSlot(bool, bool)
    def saveGeneralSettings(self, minimize_to_tray, enable_touch):
        save_general_settings(minimize_to_tray=minimize_to_tray, enable_touch=enable_touch)

    @pyqtSlot(str, str, str, str, str, str, str, str)
    def saveUsbSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder):
        save_usb_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            bitrate=bitrate,
            display_type=display_type,
            encoder=encoder
        )

    @pyqtSlot(str, str, str, str, str, str, str, str)
    def saveWifiSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder):
        save_wifi_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            bitrate=bitrate,
            display_type=display_type,
            encoder=encoder
        )

    @pyqtSlot()
    def stopStreaming(self):
        self._on_stop_streaming()

    @pyqtSlot()
    def configureDisplay(self):
        self._on_configure_display()

    @pyqtSlot(str, str, str, str, str, bool)
    def startStreaming(self, res, fps, bitrate, display_type, encoder, is_wifi):
        """Parse streaming parameters from the QML UI and launch the
        appropriate DE-specific streamer subprocess."""
        self._is_wifi = is_wifi
        try:
            clean_res = res.split()[0] if res else ""
            self._stream_width, self._stream_height = map(int, clean_res.split("x"))
        except ValueError:
            self._stream_width, self._stream_height = 1920, 1200
        self._stream_fps = int(fps)
        self._stream_bitrate = int(bitrate)
        self._selected_display_type = display_type
        self._selected_encoder = encoder

        self._do_start_streaming()

    def _do_start_streaming(self):
        """Prepare the environment, kill stale processes, and trigger
        the streamer launch (with optional virtual-monitor setup on KDE/Hyprland)."""
        script_dir = LINUX_DIR

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")

        
        encoder_map = {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
            "Software (CPU / x264enc)": "cpu"
        }
        pref = encoder_map.get(self._selected_encoder, "cpu")
        env.insert("MONITORIZE_ENCODER", pref)

        self._script_dir = script_dir
        self._env        = env

        
        subprocess.run(["killall", "-9", "gst-launch-1.0"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "Streamer_.*\\.py"], capture_output=True)

        self._cleanup_zeroconf()

        if self._is_wifi:
            subprocess.run(["adb", "reverse", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)

        
        self.set_is_streaming(True)

        if self._detected_de == "kde":
            if self._selected_display_type == "Mirror":
                self.set_streaming_status("Launching streamer (Mirror mode)…")
                self._launch_streamer()
            else:
                self.set_streaming_status("Starting virtual monitor…  5")
                self.process_krfb = QProcess(self)
                self.process_krfb.setWorkingDirectory(script_dir)
                self.process_krfb.setProcessEnvironment(env)
                self.process_krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
                self.process_krfb.readyReadStandardOutput.connect(self._read_krfb)
                self.process_krfb.finished.connect(
                    lambda code, _: self.append_log("KRFB", f"Process exited (code {code})")
                )
                subprocess.run(["killall", "krfb-virtualmonitor"], capture_output=True)

                self.process_krfb.start("krfb-virtualmonitor", [
                    "--resolution", f"{self._stream_width}x{self._stream_height}",
                    "--name",       "TabletDisplay",
                    "--password",   "test123",
                    "--port",       "5900",
                ])
                self._countdown = 1
                self.countdownChanged.emit(self._countdown)
                self._countdown_timer.start()
        elif self._detected_de == "hyprland":
            if self._selected_display_type == "Mirror":
                self.set_streaming_status("Launching streamer (Mirror mode)…")
                self._launch_streamer()
            else:
                self.set_streaming_status("Setting up virtual monitor on Hyprland…")
                old_monitors = set(self._get_current_headless_monitors())
                subprocess.run(["hyprctl", "output", "create", "headless"], capture_output=True)

                new_monitors = set(self._get_current_headless_monitors())
                diff = new_monitors - old_monitors
                if diff:
                    new_name = list(diff)[0]
                else:
                    new_name = "HEADLESS-1"

                self.created_headless_monitor = new_name

                subprocess.run(["hyprctl", "keyword", "monitor", f"{new_name},{self._stream_width}x{self._stream_height}@{self._stream_fps},auto,1"], capture_output=True)
                subprocess.run(["hyprctl", "eval", f"hl.monitor({{ output = '{new_name}', mode = '{self._stream_width}x{self._stream_height}@{self._stream_fps}', position = 'auto', scale = 1.0 }})"], capture_output=True)
                print(f"[Hyprland] Created new headless monitor: {new_name} at {self._stream_width}x{self._stream_height}@{self._stream_fps}")
                self.append_log("STREAMER", f"Created new headless monitor: {new_name} at {self._stream_width}x{self._stream_height}@{self._stream_fps}")
                self._launch_streamer()
        else:
            self.set_streaming_status("Launching streamer…")
            self._launch_streamer()

    def _countdown_tick(self):
        self._countdown -= 1
        self.countdownChanged.emit(self._countdown)

        if self._countdown > 0:
            self.set_streaming_status(f"Starting virtual monitor…  {self._countdown}")
            return

        self._countdown_timer.stop()
        self._launch_streamer()

    def _launch_streamer(self):
        """Spawn the correct DE-specific streamer script as a QProcess."""
        self.process_streamer = QProcess(self)
        self.process_streamer.setWorkingDirectory(self._script_dir)
        self.process_streamer.setProcessEnvironment(self._env)
        self.process_streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.process_streamer.finished.connect(self._on_streamer_finished)

        _streamer_map = {
            "kde":      "Streamer_kde.py",
            "gnome":    "Streamer_gnome.py",
            "hyprland": "Streamer_hyprland.py",
        }
        script_name = _streamer_map.get(self._detected_de, "Streamer_gnome.py")
        script_path = os.path.join(self._script_dir, script_name)

        args = [
            script_path,
            str(self._stream_width),
            str(self._stream_height),
            str(self._stream_fps),
            str(self._stream_bitrate),
        ]
        if self._is_wifi:
            args.append("wifi")
        else:
            args.append("usb")

        if self._detected_de == "hyprland":
            if getattr(self, "created_headless_monitor", None):
                args.append(self.created_headless_monitor)
            else:
                args.append("mirror")

        if self._detected_de == "gnome":
            args.append("1.0") 
            args.append(self._selected_display_type.replace(" ", "_"))

        self.process_streamer.start(sys.executable, args)

        if self._detected_de in ("kde", "gnome"):
            QTimer.singleShot(400, self._launch_input_bridge)
        elif self._detected_de == "hyprland":
            self._input_bridge_launched = False
            self._streamer_buffer = ""

        self.set_streaming_status("Status: Streaming…")

    def _launch_input_bridge(self):
        """Spawn touch_daemon.py to relay Android touch/pen events."""
        gen = load_general_settings()
        if not gen.get("enable_touch", True):
            self.append_log("INPUT", "ℹ️  Touch input is disabled in settings.")
            self.set_streaming_status("Status: Streaming (Touch Disabled)")
            return

        self.process_input_bridge = QProcess(self)
        self.process_input_bridge.setWorkingDirectory(self._script_dir)
        self.process_input_bridge.setProcessEnvironment(self._env)
        self.process_input_bridge.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_input_bridge.readyReadStandardOutput.connect(self._read_input_bridge)
        self.process_input_bridge.finished.connect(self._on_input_bridge_finished)
        args = [
            os.path.join(self._script_dir, "touch_daemon.py"),
            str(self._stream_width),
            str(self._stream_height),
        ]
        if getattr(self, "_is_wifi", False):
            args.append("--wifi")
        self.process_input_bridge.start(sys.executable, args)

        if self._detected_de == "hyprland":
            self.set_streaming_status("Touch service starting via uinput…")
        else:
            self.set_streaming_status("Touch service starting… Watch for 'Allow Remote Control' popup and click Allow")

    def _on_streamer_finished(self, code, _status):
        self.append_log("STREAMER", f"Process exited (code {code})")

        if (self._detected_de == "gnome" and code != 0 and self.isStreaming):
            self.append_log("STREAMER", "↺  GNOME streamer crashed — auto-restarting in 2s…")
            self.set_streaming_status("↺  Stream reconnecting after display config change…")
            QTimer.singleShot(2000, self._gnome_restart_streamer)

    def _on_input_bridge_finished(self, code, _status):
        self.append_log("INPUT", f"Bridge exited (code {code})")
        if code == 0 and self.isStreaming:
            self.append_log("INPUT", "ℹ️  Touch input not available — streaming continues without touch.")

    def _gnome_restart_streamer(self):
        if not self.isStreaming:
            return
        subprocess.run(["killall", "-9", "gst-launch-1.0"], capture_output=True)
        self._launch_streamer()
        self.set_streaming_status("Status: Streaming…  (restarted)")

    def _gnome_restart_input_bridge(self):
        if not self.isStreaming:
            return
        self._launch_input_bridge()

    def _read_krfb(self):
        if self.process_krfb is None:
            return
        raw = bytes(self.process_krfb.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("KRFB", raw)

    def _read_streamer(self):
        if self.process_streamer is None:
            return
        raw = bytes(self.process_streamer.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("STREAMER", raw)

        if self._detected_de == "hyprland":
            if not getattr(self, "_input_bridge_launched", False):
                if not hasattr(self, "_streamer_buffer"):
                    self._streamer_buffer = ""
                self._streamer_buffer += raw
                if "[Portal] Got PipeWire node=" in self._streamer_buffer:
                    self._input_bridge_launched = True
                    QTimer.singleShot(500, self._launch_input_bridge)

    def _read_input_bridge(self):
        if self.process_input_bridge is None:
            return
        raw = bytes(self.process_input_bridge.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("INPUT", raw)

    def _on_stop_streaming(self):
        """Terminate all streaming subprocesses and return to the main menu."""
        self._kill_stream_procs()
        self.set_is_streaming(False)
        self._setup_zeroconf()

    def _on_configure_display(self):
        """Launch nwg-displays for Hyprland display configuration."""
        cmd = "nwg-displays"
        if shutil.which(cmd) is None:
            QMessageBox.warning(self, "nwg-displays Not Installed", "nwg-displays is not installed.")
            return

        try:
            QProcess.startDetached(cmd)
        except Exception as e:
            if "not found" in str(e).lower() or isinstance(e, FileNotFoundError):
                QMessageBox.warning(self, "nwg-displays Not Installed", "nwg-displays is not installed.")
            else:
                QMessageBox.warning(self, "Error", f"Failed to run nwg-displays:\n{str(e)}")

    def _kill_stream_procs(self):
        """Terminate and clean up all streaming-related QProcess instances."""
        self._countdown_timer.stop()
        self._countdown = 0

        for proc in (self.process_krfb, self.process_streamer, self.process_input_bridge):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self.process_krfb          = None
        self.process_streamer      = None
        self.process_input_bridge  = None

        
        subprocess.run(["killall", "-9", "gst-launch-1.0"], capture_output=True)

        if self._detected_de == "hyprland" and getattr(self, "created_headless_monitor", None):
            print(f"[Hyprland] Removing created headless monitor: {self.created_headless_monitor}")
            subprocess.run(["hyprctl", "output", "remove", self.created_headless_monitor], capture_output=True)
            self.created_headless_monitor = None

    def _get_current_headless_monitors(self):
        """Query hyprctl for currently existing HEADLESS-* monitor names."""
        headless_names = []
        try:
            res = subprocess.run(["hyprctl", "monitors", "all", "-j"], capture_output=True, text=True)
            if res.returncode == 0:
                import json
                monitors = json.loads(res.stdout)
                for mon in monitors:
                    name = mon.get("name", "")
                    if name.startswith("HEADLESS"):
                        headless_names.append(name)
                return headless_names
        except Exception: pass

        try:
            import re
            res = subprocess.run(["hyprctl", "monitors", "all"], capture_output=True, text=True)
            if res.returncode == 0:
                matches = re.findall(r"\bHEADLESS-\d+\b", res.stdout)
                headless_names = list(set(matches))
        except Exception: pass
        return headless_names

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self._tray.hide()
        self.showNormal()
        self.activateWindow()

    def _quit_app(self):
        """Hard quit from the tray menu — always terminates processes."""
        self._kill_stream_procs()
        self._cleanup_zeroconf()
        QApplication.quit()

    def _setup_zeroconf(self):
        """Initial Zeroconf setup."""
        self._update_zeroconf_registration(self._local_ip)

    def _cleanup_zeroconf(self):
        """Clean up old registration and close Zeroconf instance if any."""
        if self._zc is None:
            return

        if self._info is not None:
            try:
                self._zc.unregister_service(self._info)
            except Exception:
                pass
        try:
            self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._info = None

    def _update_zeroconf_registration(self, ip_addr):
        """Clean up old registration and register the service under the new IP."""
        try:
            from zeroconf import ServiceInfo, Zeroconf
            import socket

            self._cleanup_zeroconf()

            
            hostname = socket.gethostname()
            self._zc = Zeroconf()
            desc = {'name': hostname, 'port': 7110}

            self._info = ServiceInfo(
                "_monitorize._tcp.local.",
                f"{hostname}._monitorize._tcp.local.",
                addresses=[socket.inet_aton(ip_addr)],
                port=7110,
                properties=desc,
                server=f"{hostname}.local.",
            )
            self._zc.register_service(self._info)
            print(f"[Zeroconf] Service registered successfully on {ip_addr}")
        except Exception as e:
            print("Zeroconf registration/update failed:", e)

    def _check_network_ip(self):
        """Periodically check the local IP and update Zeroconf registration on changes."""
        current_ip = get_local_ip()
        if current_ip != self._local_ip:
            print(f"[Network] IP changed from {self._local_ip} to {current_ip}")
            self._local_ip = current_ip
            self.localIpChanged.emit(current_ip)
            self._update_zeroconf_registration(current_ip)

    def closeEvent(self, event):
        gen = load_general_settings()
        if gen.get("minimize_to_tray", False):
            event.ignore()
            self.hide()
            self._tray.show()
            self._tray.showMessage(
                "Monitorize",
                "Running in the background. Double-click the tray icon to restore.",
                QSystemTrayIcon.MessageIcon.Information,
                5000
            )
        else:
            self._kill_stream_procs()
            self._cleanup_zeroconf()
            event.accept()

    def _ask_desktop_environment(self) -> str:
        dlg = QDialog()
        dlg.setWindowTitle("Select Desktop Environment")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 28, 32, 28)

        lbl = QLabel(
            "Could not automatically detect your desktop environment.\n"
            "Please select which one you are running:"
        )
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        kde_btn = QPushButton("KDE Plasma")
        gnome_btn = QPushButton("GNOME")
        row1.addWidget(kde_btn)
        row1.addWidget(gnome_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        hypr_btn = QPushButton("Hyprland")
        other_btn = QPushButton("Other (WIP)")
        row2.addWidget(hypr_btn)
        row2.addWidget(other_btn)
        layout.addLayout(row2)

        selected_de = "gnome"

        def pick(value):
            nonlocal selected_de
            selected_de = value
            dlg.accept()

        kde_btn.clicked.connect(lambda: pick("kde"))
        gnome_btn.clicked.connect(lambda: pick("gnome"))
        hypr_btn.clicked.connect(lambda: pick("hyprland"))
        other_btn.clicked.connect(lambda: pick("other"))

        dlg.exec()
        if selected_de == "other":
            QMessageBox.information(
                None, "Work In Progress",
                "Support for other environments is coming soon. The app will close now."
            )
            sys.exit(0)
        return selected_de


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setDesktopFileName("monitorize")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#0c0d14"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#d4d6f0"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#12142a"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#16182a"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#16182a"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#d4d6f0"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#3538b0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    win = MonitorizeWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
