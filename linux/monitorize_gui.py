#!/usr/bin/env python3
"""
Monitorize GUI — PyQt6 control panel
Run from the linux/ directory:  python3 monitorize_gui.py
"""

import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame, QPlainTextEdit,
    QComboBox,
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt6.QtGui import QColor, QPalette, QFont, QTextCursor

# ---------------------------------------------------------------------------
# Dark stylesheet
# ---------------------------------------------------------------------------

DARK_QSS = """
QMainWindow, QWidget {
    background-color: #12131a;
    color: #e0e0f0;
    font-family: 'Segoe UI', 'Inter', 'Roboto', sans-serif;
}

QPushButton {
    background-color: #1e1f2e;
    color: #c8c9e8;
    border: 1px solid #2e3050;
    border-radius: 12px;
    padding: 12px 28px;
    font-size: 15px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #272840;
    border-color: #5a5fbb;
    color: #ffffff;
}
QPushButton:pressed { background-color: #1a1b2a; }
QPushButton:disabled {
    background-color: #181924;
    color: #555570;
    border-color: #23243a;
}

QPushButton#modeBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #1e2040, stop:1 #252747);
    border: 2px solid #3a3e72;
    border-radius: 18px;
    font-size: 20px;
    font-weight: 700;
    color: #d0d2ff;
    min-width: 220px;
    min-height: 120px;
}
QPushButton#modeBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #252850, stop:1 #2e3260);
    border-color: #6e72cc;
    color: #ffffff;
}

QPushButton#primaryBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #3b40c0, stop:1 #5254d8);
    border: none;
    border-radius: 14px;
    font-size: 17px;
    font-weight: 700;
    color: #ffffff;
    min-height: 58px;
    min-width: 260px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #4549d8, stop:1 #6264f0);
}
QPushButton#primaryBtn:pressed { background-color: #2e32a8; }

QPushButton#stopBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #8b1a1a, stop:1 #c0282e);
    border: none;
    border-radius: 14px;
    font-size: 17px;
    font-weight: 700;
    color: #ffffff;
    min-height: 58px;
    min-width: 260px;
}
QPushButton#stopBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #a81e1e, stop:1 #d83030);
}
QPushButton#stopBtn:pressed { background-color: #6e1212; }

QPushButton#backBtn {
    background-color: #1a1b2a;
    border: 1px solid #2e3050;
    border-radius: 10px;
    font-size: 13px;
    color: #8888aa;
    padding: 8px 20px;
    min-width: 100px;
}
QPushButton#backBtn:hover { border-color: #5a5fbb; color: #c0c2e8; }

QLabel#titleLabel {
    font-size: 28px;
    font-weight: 800;
    color: #d8daff;
    letter-spacing: 1px;
}
QLabel#subLabel    { font-size: 15px; color: #8888bb; }
QLabel#stepLabel   { font-size: 13px; color: #7070aa; }
QLabel#instruction { font-size: 16px; color: #c0c2e0; }
QLabel#wip         { font-size: 18px; color: #9090cc; font-style: italic; }
QLabel#streaming   { font-size: 22px; font-weight: 700; color: #58d68d; }
QLabel#statusLbl   { font-size: 13px; color: #7070aa; }

QPlainTextEdit#logBox {
    background-color: #0d0e18;
    color: #a0d4a0;
    border: 1px solid #2a2b42;
    border-radius: 8px;
    font-family: 'Consolas', 'Fira Mono', monospace;
    font-size: 12px;
    padding: 6px;
}

QFrame#sep {
    background-color: #2a2b42;
    max-height: 1px;
}

QComboBox {
    background-color: #1a1b2e;
    color: #d0d2ff;
    border: 1px solid #3a3e72;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: 600;
    min-width: 140px;
}
QComboBox:hover { border-color: #6e72cc; }
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #1a1b2e;
    color: #d0d2ff;
    selection-background-color: #3b40c0;
    selection-color: #ffffff;
    border: 1px solid #3a3e72;
    border-radius: 6px;
    padding: 4px;
}

QLabel#warningLabel {
    font-size: 13px;
    font-weight: 700;
    color: #f0ad4e;
    padding: 8px 14px;
    background-color: rgba(240, 173, 78, 0.08);
    border: 1px solid rgba(240, 173, 78, 0.25);
    border-radius: 8px;
}

QLabel#portalHint {
    font-size: 15px;
    font-weight: 600;
    color: #9fa1dd;
}
"""

# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def hr() -> QFrame:
    line = QFrame()
    line.setObjectName("sep")
    line.setFrameShape(QFrame.Shape.HLine)
    return line

def vspace(n: int) -> int:
    return n  # used as argument to addSpacing


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class MainMenuPage(QWidget):
    def __init__(self, on_usb, on_wifi, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)

        title = QLabel("Monitorize")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Linux → Android Display Bridge")
        sub.setObjectName("subLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root.addWidget(title)
        root.addSpacing(6)
        root.addWidget(sub)
        root.addSpacing(30)
        root.addWidget(hr())
        root.addSpacing(48)

        row = QHBoxLayout()
        row.setSpacing(36)

        usb_btn = QPushButton("🔌  USB Mode")
        usb_btn.setObjectName("modeBtn")
        usb_btn.clicked.connect(on_usb)

        wifi_btn = QPushButton("📶  Wi-Fi Mode")
        wifi_btn.setObjectName("modeBtn")
        wifi_btn.clicked.connect(on_wifi)

        row.addStretch()
        row.addWidget(usb_btn)
        row.addWidget(wifi_btn)
        row.addStretch()
        root.addLayout(row)
        root.addStretch()

        footer = QLabel("Select a connection mode to begin")
        footer.setObjectName("statusLbl")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)


class WifiPage(QWidget):
    def __init__(self, on_back, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.addStretch()

        icon = QLabel("📶")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI", 48))
        root.addWidget(icon)
        root.addSpacing(20)

        msg = QLabel("Wi-Fi Mode is a Work in Progress")
        msg.setObjectName("wip")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(msg)
        root.addStretch()

        back = QPushButton("← Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        row = QHBoxLayout()
        row.addStretch(); row.addWidget(back); row.addStretch()
        root.addLayout(row)
        root.addSpacing(20)


class UsbStep1Page(QWidget):
    """Step 1 — connect tablet and run ADB."""

    def __init__(self, on_back, on_connected, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)

        step = QLabel("USB Mode  ·  Step 1 of 2")
        step.setObjectName("stepLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addStretch()

        icon = QLabel("🔌")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI", 48))
        root.addWidget(icon)
        root.addSpacing(24)

        msg = QLabel(
            "Please connect your tablet via USB\n"
            "with ADB debugging enabled."
        )
        msg.setObjectName("instruction")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)
        root.addStretch()

        self._status = QLabel("")
        self._status.setObjectName("statusLbl")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status)
        root.addSpacing(16)

        self._next_btn = QPushButton("✔  I have connected it")
        self._next_btn.setObjectName("primaryBtn")
        self._next_btn.clicked.connect(on_connected)

        back = QPushButton("← Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        row = QHBoxLayout()
        row.setSpacing(20)
        row.addStretch()
        row.addWidget(back)
        row.addWidget(self._next_btn)
        row.addStretch()
        root.addLayout(row)
        root.addSpacing(24)

    def set_status(self, text: str):
        self._status.setText(text)

    def set_busy(self, busy: bool):
        self._next_btn.setEnabled(not busy)


class UsbStep2Page(QWidget):
    """Step 2 — configure resolution/FPS, open tablet app, start streaming."""

    RESOLUTIONS = [
        "1280x720", "1280x800", "1920x1080", "1920x1200",
        "2560x1440", "2560x1600", "3840x2160",
    ]
    FPS_OPTIONS = ["30", "60", "90", "120"]

    def __init__(self, on_back, on_start, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 40, 60, 40)

        step = QLabel("USB Mode  ·  Step 2 of 2")
        step.setObjectName("stepLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addSpacing(24)

        # ---- Open app instruction ----
        msg = QLabel("📱  Please open the Monitorize app on your tablet.")
        msg.setObjectName("instruction")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)
        root.addSpacing(20)

        # ---- Resolution dropdown ----
        res_row = QHBoxLayout()
        res_row.setSpacing(12)
        res_label = QLabel("Resolution:")
        res_label.setObjectName("instruction")
        self._res_combo = QComboBox()
        self._res_combo.addItems(self.RESOLUTIONS)
        self._res_combo.setCurrentText("2560x1600")  # default
        res_row.addStretch()
        res_row.addWidget(res_label)
        res_row.addWidget(self._res_combo)
        res_row.addStretch()
        root.addLayout(res_row)
        root.addSpacing(10)

        # ---- FPS dropdown ----
        fps_row = QHBoxLayout()
        fps_row.setSpacing(12)
        fps_label = QLabel("FPS:")
        fps_label.setObjectName("instruction")
        self._fps_combo = QComboBox()
        self._fps_combo.addItems(self.FPS_OPTIONS)
        self._fps_combo.setCurrentText("60")  # default
        fps_row.addStretch()
        fps_row.addWidget(fps_label)
        fps_row.addWidget(self._fps_combo)
        fps_row.addStretch()
        root.addLayout(fps_row)
        root.addSpacing(16)

        # ---- Warning label ----
        warning = QLabel(
            "⚠️ WARNING: The Resolution and FPS set here MUST EXACTLY "
            "MATCH the settings in the Android tablet app, or the stream "
            "will corrupt!"
        )
        warning.setObjectName("warningLabel")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warning.setWordWrap(True)
        root.addWidget(warning)
        root.addSpacing(20)

        root.addWidget(hr())
        root.addSpacing(16)

        # ---- Before-you-click checklist ----
        step1_lbl = QLabel(
            "1️⃣  Tap the  Receive  button on the Android app first."
        )
        step1_lbl.setObjectName("portalHint")
        step1_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step1_lbl.setWordWrap(True)
        root.addWidget(step1_lbl)
        root.addSpacing(10)

        step2_lbl = QLabel(
            '2️⃣  Then click Start Streaming below. When the screen-sharing '
            'popup appears, select "TabletDisplay" and click Share.'
        )
        step2_lbl.setObjectName("portalHint")
        step2_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step2_lbl.setWordWrap(True)
        root.addWidget(step2_lbl)
        root.addSpacing(20)

        root.addStretch()

        # ---- Buttons ----
        back = QPushButton("← Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        start_btn = QPushButton("▶  Start Streaming")
        start_btn.setObjectName("primaryBtn")
        start_btn.clicked.connect(on_start)

        row = QHBoxLayout()
        row.setSpacing(20)
        row.addStretch()
        row.addWidget(back)
        row.addWidget(start_btn)
        row.addStretch()
        root.addLayout(row)
        root.addSpacing(20)

    # -- Public getters used by MonitorizeWindow --

    def selected_resolution(self) -> tuple[int, int]:
        """Return (width, height) from the resolution dropdown."""
        text = self._res_combo.currentText()   # e.g. "2560x1600"
        w, h = text.split("x")
        return int(w), int(h)

    def selected_fps(self) -> int:
        return int(self._fps_combo.currentText())


class StreamingPage(QWidget):
    """
    Active streaming view.
    Shows a live log box fed from both QProcess stdout streams.
    """

    def __init__(self, on_stop, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 36)
        root.setSpacing(0)

        # ---- Status label (updated by countdown / streaming state) ----
        self._status_lbl = QLabel("⏳  Starting virtual monitor…")
        self._status_lbl.setObjectName("streaming")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_lbl)
        root.addSpacing(6)

        hint = QLabel(
            "Live output from both processes is shown below."
        )
        hint.setObjectName("statusLbl")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addSpacing(16)

        # ---- Log box ----
        self.log = QPlainTextEdit()
        self.log.setObjectName("logBox")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        root.addWidget(self.log, stretch=1)
        root.addSpacing(20)

        # ---- Stop button (disabled until streamer actually starts) ----
        self._stop_btn = QPushButton("⏹  Stop Streaming")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(on_stop)

        row = QHBoxLayout()
        row.addStretch(); row.addWidget(self._stop_btn); row.addStretch()
        root.addLayout(row)
        root.addSpacing(16)

    # -- Public helpers called by MonitorizeWindow --

    def set_status(self, text: str):
        self._status_lbl.setText(text)

    def set_stop_enabled(self, enabled: bool):
        self._stop_btn.setEnabled(enabled)

    def append_log(self, prefix: str, text: str):
        """Append a labelled line to the log and auto-scroll."""
        for line in text.splitlines():
            if line.strip():
                self.log.appendPlainText(f"[{prefix}] {line}")
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def clear_log(self):
        self.log.clear()


# ---------------------------------------------------------------------------
# Page indices
# ---------------------------------------------------------------------------
PAGE_MAIN      = 0
PAGE_WIFI      = 1
PAGE_USB1      = 2
PAGE_USB2      = 3
PAGE_STREAMING = 4


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MonitorizeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)

        # Two persistent QProcess objects for streaming
        self.process_krfb:     QProcess | None = None
        self.process_streamer: QProcess | None = None

        # Transient QProcess objects for ADB (step 1)
        self._proc_adb_dev: QProcess | None = None
        self._proc_adb_fwd: QProcess | None = None

        # Countdown timer (used between krfb start and streamer start)
        self._countdown: int = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)   # 1 second ticks
        self._countdown_timer.timeout.connect(self._countdown_tick)

        # Stack
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._page_main      = MainMenuPage(self._go_usb1, self._go_wifi)
        self._page_wifi      = WifiPage(self._go_main)
        self._page_usb1      = UsbStep1Page(self._go_main, self._on_connected)
        self._page_usb2      = UsbStep2Page(self._go_usb1, self._on_start_streaming)
        self._page_streaming = StreamingPage(self._on_stop_streaming)

        self._stack.addWidget(self._page_main)       # 0
        self._stack.addWidget(self._page_wifi)       # 1
        self._stack.addWidget(self._page_usb1)       # 2
        self._stack.addWidget(self._page_usb2)       # 3
        self._stack.addWidget(self._page_streaming)  # 4

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_main(self):
        self._stack.setCurrentIndex(PAGE_MAIN)

    def _go_wifi(self):
        self._stack.setCurrentIndex(PAGE_WIFI)

    def _go_usb1(self):
        self._page_usb1.set_status("")
        self._page_usb1.set_busy(False)
        self._stack.setCurrentIndex(PAGE_USB1)

    # ------------------------------------------------------------------
    # Step 1 — ADB setup
    # ------------------------------------------------------------------

    def _on_connected(self):
        self._page_usb1.set_busy(True)
        self._page_usb1.set_status("⏳  Running adb devices…")

        self._proc_adb_dev = QProcess(self)
        self._proc_adb_dev.finished.connect(self._adb_devices_done)
        self._proc_adb_dev.start("adb", ["devices"])

    def _adb_devices_done(self, exit_code, _status):
        if exit_code != 0:
            self._page_usb1.set_status("❌  adb devices failed. Is ADB installed?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("⏳  Forwarding port tcp:7110…")
        self._proc_adb_fwd = QProcess(self)
        self._proc_adb_fwd.finished.connect(self._adb_forward_done)
        self._proc_adb_fwd.start("adb", ["forward", "tcp:7110", "tcp:7110"])

    def _adb_forward_done(self, exit_code, _status):
        if exit_code != 0:
            self._page_usb1.set_status("❌  Port forward failed. Is a device connected?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("✅  Device ready!")
        self._page_usb1.set_busy(False)
        QTimer.singleShot(600, lambda: self._stack.setCurrentIndex(PAGE_USB2))

    # ------------------------------------------------------------------
    # Step 2 — Start both processes SIMULTANEOUSLY
    # ------------------------------------------------------------------

    def _on_start_streaming(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self._page_streaming.clear_log()

        # Inherit the full system environment (WAYLAND_DISPLAY, XDG_SESSION_TYPE,
        # DBUS_SESSION_BUS_ADDRESS, etc.) so both processes behave exactly like
        # commands launched from a native terminal inside KDE Plasma.
        env = QProcessEnvironment.systemEnvironment()
        self._script_dir = script_dir
        self._env        = env

        # ---- Switch to streaming page immediately ----
        self._page_streaming.set_stop_enabled(False)
        self._page_streaming.set_status("⏳  Starting virtual monitor…  3")
        self._stack.setCurrentIndex(PAGE_STREAMING)

        # ---- Process A: krfb-virtualmonitor — starts NOW ----
        self.process_krfb = QProcess(self)
        self.process_krfb.setWorkingDirectory(script_dir)
        self.process_krfb.setProcessEnvironment(env)
        self.process_krfb.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process_krfb.readyReadStandardOutput.connect(self._read_krfb)
        self.process_krfb.finished.connect(
            lambda code, _: self._page_streaming.append_log(
                "KRFB", f"Process exited (code {code})"
            )
        )
        # Read user-chosen resolution for krfb
        width, height = self._page_usb2.selected_resolution()
        fps = self._page_usb2.selected_fps()
        self._stream_width  = width
        self._stream_height = height
        self._stream_fps    = fps

        self.process_krfb.start(
            "krfb-virtualmonitor",
            [
                "--resolution", f"{width}x{height}",
                "--name",       "TabletDisplay",
                "--password",   "test123",
                "--port",       "5900",
            ],
        )

        # ---- Begin 3-second countdown before starting the streamer ----
        self._countdown = 3
        self._countdown_timer.start()

    def _countdown_tick(self):
        """Called every 1 s by _countdown_timer. Starts the streamer at 0."""
        self._countdown -= 1

        if self._countdown > 0:
            self._page_streaming.set_status(
                f"⏳  Starting virtual monitor…  {self._countdown}"
            )
            return

        # Countdown finished — stop timer, launch streamer
        self._countdown_timer.stop()

        # ---- Process B: python3 Streamer_usb.py — starts after delay ----
        self.process_streamer = QProcess(self)
        self.process_streamer.setWorkingDirectory(self._script_dir)
        self.process_streamer.setProcessEnvironment(self._env)
        self.process_streamer.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process_streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.process_streamer.finished.connect(
            lambda code, _: self._page_streaming.append_log(
                "STREAMER", f"Process exited (code {code})"
            )
        )
        self.process_streamer.start("python3", [
            "Streamer_usb.py",
            str(self._stream_width),
            str(self._stream_height),
            str(self._stream_fps),
        ])

        self._page_streaming.set_status("⬤  Status: Streaming…")
        self._page_streaming.set_stop_enabled(True)

    # ------------------------------------------------------------------
    # Log readers — called by readyReadStandardOutput signals
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Stop streaming — terminate BOTH processes
    # ------------------------------------------------------------------

    def _on_stop_streaming(self):
        self._kill_stream_procs()
        self._go_main()

    def _kill_stream_procs(self):
        # Stop the countdown so it can't fire and start the streamer after cleanup
        self._countdown_timer.stop()
        self._countdown = 0

        for proc in (self.process_krfb, self.process_streamer):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self.process_krfb     = None
        self.process_streamer = None

    # ------------------------------------------------------------------
    # Always clean up on close
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._kill_stream_procs()
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setStyleSheet(DARK_QSS)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#12131a"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#e0e0f0"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#1a1b2a"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1e1f2e"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#1e1f2e"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#e0e0f0"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#3b40c0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    win = MonitorizeWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
