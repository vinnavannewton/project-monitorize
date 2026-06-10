"""
Monitorize GUI — Main application window and entry point.
"""

import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QSystemTrayIcon, QMenu,
    QDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QSize
from PyQt6.QtGui import QColor, QPalette, QIcon

from gui.styles import DARK_QSS
from gui.utils import (
    _make_tray_icon, detect_desktop_environment, get_local_ip, LINUX_DIR,
)
from gui.widgets import make_scrollable
from gui.constants import PAGE_MAIN, PAGE_WIFI, PAGE_USB1, PAGE_USB2, PAGE_STREAMING
from gui.pages import MainMenuPage, WifiPage, UsbStep1Page, UsbStep2Page, StreamingPage


class MonitorizeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)

        
        app_icon_path = os.path.join(LINUX_DIR, "assets", "monitorize-icon.png")
        if os.path.exists(app_icon_path):
            self.setWindowIcon(QIcon(app_icon_path))

        import subprocess
        subprocess.Popen(["killall", "-9", "gst-launch-1.0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "Streamer_.*\\.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        detected = detect_desktop_environment()
        if detected:
            self.detected_de = detected
        else:
            self.detected_de = self._ask_desktop_environment()

        self.initial_headless_monitors = []
        if self.detected_de == "hyprland":
            self.initial_headless_monitors = self._get_current_headless_monitors()
            print(f"[Hyprland] Initial virtual monitors detected: {self.initial_headless_monitors}")
        self.created_headless_monitor = None

        self.process_krfb:          QProcess | None = None
        self.process_streamer:      QProcess | None = None
        self.process_input_bridge:  QProcess | None = None

        self._proc_adb_dev:  QProcess | None = None
        self._proc_adb_fwd:  QProcess | None = None
        self._proc_adb_fwd2:  QProcess | None = None

        self._countdown: int = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._page_main      = MainMenuPage(self._go_usb1, self._go_wifi)
        self._page_wifi      = WifiPage(self._go_main, self._on_start_streaming_wifi, detected_de=self.detected_de)
        self._page_usb1      = UsbStep1Page(self._go_main, self._on_connected)
        self._page_usb2      = UsbStep2Page(self._go_usb1, self._on_start_streaming, detected_de=self.detected_de)
        self._page_streaming = StreamingPage(self._on_stop_streaming, self._on_configure_display)

        self._stack.addWidget(self._page_main)
        self._stack.addWidget(make_scrollable(self._page_wifi))
        self._stack.addWidget(self._page_usb1)
        self._stack.addWidget(make_scrollable(self._page_usb2))
        self._stack.addWidget(self._page_streaming)

        self._page_main.update_de_badge(self.detected_de)

        self._zc = None
        self._info = None
        QTimer.singleShot(0, self._setup_zeroconf)

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

    def _ask_desktop_environment(self) -> str:
        """
        Show a QDialog asking the user to pick their DE.
        Returns one of: "kde", "gnome", "hyprland", "sway".
        Shows a WIP message and exits if the user picks "Other".
        """
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
        script_dir = LINUX_DIR
        kde_svg = os.path.join(script_dir, "assets", "svg", "kde-logo.svg")
        gnome_svg = os.path.join(script_dir, "assets", "svg", "gnome-logo.svg")
        hypr_svg = os.path.join(script_dir, "assets", "svg", "hyprland-logo.svg")

        kde_btn   = QPushButton("  KDE")
        if os.path.exists(kde_svg):
            kde_btn.setIcon(QIcon(kde_svg))
            kde_btn.setIconSize(QSize(20, 20))

        gnome_btn = QPushButton("  GNOME")
        if os.path.exists(gnome_svg):
            gnome_btn.setIcon(QIcon(gnome_svg))
            gnome_btn.setIconSize(QSize(20, 20))

        row1.addWidget(kde_btn)
        row1.addWidget(gnome_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        hypr_btn  = QPushButton("  Hyprland")
        if os.path.exists(hypr_svg):
            hypr_btn.setIcon(QIcon(hypr_svg))
            hypr_btn.setIconSize(QSize(20, 20))

        sway_btn  = QPushButton("Sway")
        row2.addWidget(hypr_btn)
        row2.addWidget(sway_btn)
        layout.addLayout(row2)

        other_btn = QPushButton("Other / Unsupported")
        other_btn.setObjectName("backBtn")
        layout.addWidget(other_btn)

        dlg.setMinimumWidth(420)

        chosen = [""]

        def pick(value):
            chosen[0] = value
            dlg.accept()

        kde_btn.clicked.connect(lambda: pick("kde"))
        gnome_btn.clicked.connect(lambda: pick("gnome"))
        hypr_btn.clicked.connect(lambda: pick("hyprland"))
        sway_btn.clicked.connect(lambda: pick("sway"))
        other_btn.clicked.connect(lambda: pick("other"))

        dlg.exec()

        if chosen[0] in ("other", ""):
            msg = QMessageBox()
            msg.setWindowTitle("Unsupported Desktop Environment")
            msg.setText(
                "Only KDE, GNOME, Hyprland, and Sway are supported.\n"
                "Support for other desktop environments is a work in progress."
            )
            msg.setIcon(QMessageBox.Icon.Information)
            msg.exec()
            sys.exit(0)

        return chosen[0]

    def _go_main(self):
        self._stack.setCurrentIndex(PAGE_MAIN)

    def _go_wifi(self):
        self._is_wifi = True
        self._stack.setCurrentIndex(PAGE_WIFI)

    def _go_usb1(self):
        self._page_usb1.set_status("")
        self._page_usb1.set_busy(False)
        self._stack.setCurrentIndex(PAGE_USB1)

    def _on_connected(self):
        self._page_usb1.set_busy(True)
        self._page_usb1.set_status("Running adb devices\u2026")

        self._proc_adb_dev = QProcess(self)
        self._proc_adb_dev.finished.connect(self._adb_devices_done)
        self._proc_adb_dev.start("adb", ["devices"])

    def _adb_devices_done(self, exit_code, _status):
        if exit_code != 0:
            self._page_usb1.set_status("Error: adb devices failed. Is ADB installed?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("Forwarding port tcp:7110\u2026")
        self._proc_adb_fwd = QProcess(self)
        self._proc_adb_fwd.finished.connect(self._adb_forward_done)
        self._proc_adb_fwd.start("adb", ["forward", "tcp:7110", "tcp:7110"])

    def _adb_forward_done(self, exit_code, _status):
        if exit_code != 0:
            self._page_usb1.set_status("Error: Port forward failed. Is a device connected?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("Setting up reverse proxy tcp:7111 (touch)\u2026")
        self._proc_adb_fwd2 = QProcess(self)
        self._proc_adb_fwd2.finished.connect(self._adb_forward2_done)
        self._proc_adb_fwd2.start("adb", ["reverse", "tcp:7111", "tcp:7111"])

    def _adb_forward2_done(self, exit_code, _status):
        if exit_code != 0:
            self._page_usb1.set_status("Warning: tcp:7111 reverse failed \u2014 touch disabled")
        else:
            self._page_usb1.set_status("Device ready!")
        self._page_usb1.set_busy(False)
        QTimer.singleShot(600, lambda: self._stack.setCurrentIndex(PAGE_USB2))

    def _on_start_streaming_wifi(self):
        self._is_wifi = True
        self._do_start_streaming(self._page_wifi)

    def _on_start_streaming(self):
        self._is_wifi = False
        self._do_start_streaming(self._page_usb2)

    def _do_start_streaming(self, config_page):
        script_dir = LINUX_DIR
        self._page_streaming.clear_log()

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self._script_dir = script_dir
        self._env        = env

        self._page_streaming.set_stop_enabled(False)
        self._page_streaming.set_configure_visible(self.detected_de == "hyprland")
        self._stack.setCurrentIndex(PAGE_STREAMING)

        width, height = config_page.selected_resolution()
        fps = config_page.selected_fps()
        bitrate = config_page.selected_bitrate()
        self._stream_width  = width
        self._stream_height = height
        self._stream_fps    = fps
        self._stream_bitrate = bitrate
        import subprocess
        subprocess.run(["killall", "-9", "gst-launch-1.0"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "Streamer_.*\\.py"], capture_output=True)

        if self._is_wifi:
            subprocess.run(["adb", "forward", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)

        display_type = config_page.gnome_type()

        if self.detected_de == "kde":
            if display_type == "Mirror":
                self._page_streaming.set_status("Launching streamer (Mirror mode)\u2026")
                self._launch_streamer()
            else:
                self._page_streaming.set_status("Starting virtual monitor\u2026  5")
                self.process_krfb = QProcess(self)
                self.process_krfb.setWorkingDirectory(script_dir)
                self.process_krfb.setProcessEnvironment(env)
                self.process_krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
                self.process_krfb.readyReadStandardOutput.connect(self._read_krfb)
                self.process_krfb.finished.connect(
                    lambda code, _: self._page_streaming.append_log("KRFB", f"Process exited (code {code})")
                )
                import subprocess
                subprocess.run(["killall", "krfb-virtualmonitor"], capture_output=True)

                self.process_krfb.start("krfb-virtualmonitor", [
                    "--resolution", f"{width}x{height}",
                    "--name",       "TabletDisplay",
                    "--password",   "test123",
                    "--port",       "5900",
                ])
                self._countdown = 1
                self._countdown_timer.start()
        elif self.detected_de == "hyprland":
            if display_type == "Mirror":
                self._page_streaming.set_status("Launching streamer (Mirror mode)\u2026")
                self._launch_streamer()
            else:
                self._page_streaming.set_status("Setting up virtual monitor on Hyprland\u2026")
                import subprocess
                old_monitors = set(self._get_current_headless_monitors())
                subprocess.run(["hyprctl", "output", "create", "headless"], capture_output=True)

                new_monitors = set(self._get_current_headless_monitors())
                diff = new_monitors - old_monitors
                if diff:
                    new_name = list(diff)[0]
                else:
                    new_name = "HEADLESS-1"

                self.created_headless_monitor = new_name

                subprocess.run(["hyprctl", "keyword", "monitor", f"{new_name},{width}x{height}@{fps},auto,1"], capture_output=True)
                subprocess.run(["hyprctl", "eval", f"hl.monitor({{ output = '{new_name}', mode = '{width}x{height}@{fps}', position = 'auto', scale = 1.0 }})"], capture_output=True)
                print(f"[Hyprland] Created new headless monitor: {new_name} at {width}x{height}@{fps}")
                self._page_streaming.append_log("STREAMER", f"Created new headless monitor: {new_name} at {width}x{height}@{fps}")
                self._launch_streamer()
        else:
            self._page_streaming.set_status("Launching streamer\u2026")
            self._launch_streamer()

    def _countdown_tick(self):
        """Called every 1 s by _countdown_timer. Starts the streamer at 0."""
        self._countdown -= 1

        if self._countdown > 0:
            self._page_streaming.set_status(
                f"Starting virtual monitor\u2026  {self._countdown}"
            )
            return

        self._countdown_timer.stop()
        self._launch_streamer()

    def _launch_streamer(self):
        """Spawn the correct DE-specific streamer script as a QProcess."""
        self.process_streamer = QProcess(self)
        self.process_streamer.setWorkingDirectory(self._script_dir)
        self.process_streamer.setProcessEnvironment(self._env)
        self.process_streamer.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process_streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.process_streamer.finished.connect(self._on_streamer_finished)

        _streamer_map = {
            "kde":      "Streamer_kde_usb.py",
            "gnome":    "Streamer_gnome_wifi.py" if self._is_wifi else "Streamer_gnome_usb.py",
            "hyprland": "Streamer_hyprland_wifi.py" if self._is_wifi else "Streamer_hyprland_usb.py",
            "sway":     "Streamer_sway_usb.py",
        }
        streamer_script = _streamer_map.get(self.detected_de, "Streamer_kde_usb.py")

        args = [
            streamer_script,
            str(self._stream_width),
            str(self._stream_height),
            str(self._stream_fps),
            str(self._stream_bitrate),
        ]
        if self._is_wifi:
            args.append("wifi")
        else:
            args.append("usb")

        if self.detected_de == "hyprland":
            if getattr(self, "created_headless_monitor", None):
                args.append(self.created_headless_monitor)
            else:
                args.append("mirror")

        if self.detected_de == "gnome":
            active_page = self._page_wifi if self._is_wifi else self._page_usb2
            args.append(active_page.gnome_scale())
            args.append(active_page.gnome_type().replace(" ", "_"))

        self.process_streamer.start("python3", args)

        if self.detected_de in ("kde", "gnome"):
            QTimer.singleShot(400, self._launch_input_bridge)
        elif self.detected_de == "hyprland":
            self._input_bridge_launched = False
            self._streamer_buffer = ""

        self._page_streaming.set_status("Status: Streaming\u2026")
        self._page_streaming.set_stop_enabled(True)

    def _launch_input_bridge(self):
        """Spawn input_bridge.py -- listens on port 7111 for Android touch/pen events."""
        self.process_input_bridge = QProcess(self)
        self.process_input_bridge.setWorkingDirectory(self._script_dir)
        self.process_input_bridge.setProcessEnvironment(self._env)
        self.process_input_bridge.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process_input_bridge.readyReadStandardOutput.connect(self._read_input_bridge)
        self.process_input_bridge.finished.connect(self._on_input_bridge_finished)
        args = [
            os.path.join(self._script_dir, "touch_daemon.py"),
            str(self._stream_width),
            str(self._stream_height),
        ]
        if getattr(self, "_is_wifi", False):
            args.append("--wifi")
        self.process_input_bridge.start("python3", args)

        if self.detected_de == "hyprland":
            self._page_streaming.set_status(
                "Touch service starting via uinput\u2026"
            )
        else:
            self._page_streaming.set_status(
                "Touch service starting\u2026 Watch for 'Allow Remote Control' popup and click Allow"
            )

    def _on_streamer_finished(self, code, _status):
        """Handle streamer process exit. On GNOME, auto-restart after crashes."""
        self._page_streaming.append_log("STREAMER", f"Process exited (code {code})")

        if (self.detected_de == "gnome"
                and code != 0
                and self._stack.currentIndex() == PAGE_STREAMING):
            self._page_streaming.append_log(
                "STREAMER",
                "\u27f3  GNOME streamer crashed \u2014 auto-restarting in 2s\u2026"
            )
            self._page_streaming.set_status(
                "\u27f3  Stream reconnecting after display config change\u2026"
            )
            QTimer.singleShot(2000, self._gnome_restart_streamer)

    def _on_input_bridge_finished(self, code, _status):
        """Handle touch daemon process exit."""
        self._page_streaming.append_log("INPUT", f"Bridge exited (code {code})")

        if code == 0 and self._stack.currentIndex() == PAGE_STREAMING:
            self._page_streaming.append_log(
                "INPUT",
                "\u2139\ufe0f  Touch input not available \u2014 streaming continues without touch."
            )

    def _gnome_restart_streamer(self):
        """Auto-restart the GNOME streamer after a crash."""
        if self._stack.currentIndex() != PAGE_STREAMING:
            return
        import subprocess
        subprocess.run(["killall", "-9", "gst-launch-1.0"], capture_output=True)
        self._launch_streamer()
        self._page_streaming.set_status("Status: Streaming\u2026  (restarted)")

    def _gnome_restart_input_bridge(self):
        """Auto-restart the touch daemon after a crash on GNOME."""
        if self._stack.currentIndex() != PAGE_STREAMING:
            return
        self._launch_input_bridge()

    def _read_krfb(self):
        if self.process_krfb is None:
            return
        raw = bytes(self.process_krfb.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._page_streaming.append_log("KRFB", raw)

    def _read_streamer(self):
        if self.process_streamer is None:
            return
        raw = bytes(self.process_streamer.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._page_streaming.append_log("STREAMER", raw)

        if self.detected_de == "hyprland":
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
        raw = bytes(self.process_input_bridge.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._page_streaming.append_log("INPUT", raw)

    def _on_stop_streaming(self):
        self._kill_stream_procs()
        self._go_main()

    def _on_configure_display(self):
        """Run nwg-displays for Hyprland display configuration."""
        import shutil
        cmd = "nwg-displays"
        if shutil.which(cmd) is None:
            QMessageBox.warning(
                self,
                "nwg-displays Not Installed",
                "nwg-displays is not installed."
            )
            return

        try:
            QProcess.startDetached(cmd)
        except Exception as e:
            if "not found" in str(e).lower() or isinstance(e, FileNotFoundError):
                QMessageBox.warning(
                    self,
                    "nwg-displays Not Installed",
                    "nwg-displays is not installed."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to run nwg-displays:\n{str(e)}"
                )

    def _kill_stream_procs(self):
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

        if self.detected_de == "hyprland" and getattr(self, "created_headless_monitor", None):
            print(f"[Hyprland] Removing created headless monitor: {self.created_headless_monitor}")
            import subprocess
            subprocess.run(["hyprctl", "output", "remove", self.created_headless_monitor], capture_output=True)
            self.created_headless_monitor = None

    def _get_current_headless_monitors(self):
        """Query and return a list of currently existing headless monitor names."""
        import subprocess, json, re
        headless_names = []

        try:
            res = subprocess.run(["hyprctl", "monitors", "all", "-j"], capture_output=True, text=True)
            if res.returncode == 0:
                monitors = json.loads(res.stdout)
                for mon in monitors:
                    name = mon.get("name", "")
                    if name.startswith("HEADLESS"):
                        headless_names.append(name)
                return headless_names
        except Exception:
            pass

        try:
            res = subprocess.run(["hyprctl", "monitors", "all"], capture_output=True, text=True)
            if res.returncode == 0:
                matches = re.findall(r"\bHEADLESS-\d+\b", res.stdout)
                headless_names = list(set(matches))
        except Exception:
            pass
        return headless_names

    def _tray_activated(self, reason):
        """Restore the window when the tray icon is clicked."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self._tray.hide()
        self.showNormal()
        self.activateWindow()

    def _quit_app(self):
        """Hard quit from the tray menu -- always terminates processes."""
        self._kill_stream_procs()
        if hasattr(self, '_zc') and hasattr(self, '_info') and self._zc is not None and self._info is not None:
            try:
                self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception: pass
        QApplication.quit()

    def _setup_zeroconf(self):
        """Register the Monitorize service via Zeroconf in the background."""
        try:
            from zeroconf import ServiceInfo, Zeroconf
            import socket
            hostname = socket.gethostname()
            self._zc = Zeroconf()
            desc = {'name': hostname, 'port': 7110}

            ip_addr = get_local_ip()

            self._info = ServiceInfo(
                "_monitorize._tcp.local.",
                f"{hostname}._monitorize._tcp.local.",
                addresses=[socket.inet_aton(ip_addr)],
                port=7110,
                properties=desc,
                server=f"{hostname}.local.",
            )
            self._zc.register_service(self._info)
        except Exception as e:
            print("Zeroconf registration failed:", e)

    def closeEvent(self, event):
        if self._page_main.tray_checkbox.isChecked():
            event.ignore()
            self.hide()
            self._tray.show()
            self._tray.showMessage(
                "Monitorize",
                "Running in the background. Double-click the tray icon to restore.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        else:
            self._kill_stream_procs()
            if hasattr(self, '_zc') and hasattr(self, '_info') and self._zc is not None and self._info is not None:
                try:
                    self._zc.unregister_service(self._info)
                    self._zc.close()
                except Exception: pass
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setDesktopFileName("monitorize")
    app.setStyleSheet(DARK_QSS)

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
