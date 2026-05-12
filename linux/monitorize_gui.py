#!/usr/bin/env python3
"""
Monitorize GUI — PyQt6 control panel
Run from the linux/ directory:  python3 monitorize_gui.py
"""

import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QSizePolicy, QFrame,
    QSpacerItem,
)
from PyQt6.QtCore import Qt, QProcess, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

# ---------------------------------------------------------------------------
# Dark stylesheet
# ---------------------------------------------------------------------------

DARK_QSS = """
QMainWindow, QWidget {
    background-color: #12131a;
    color: #e0e0f0;
    font-family: 'Segoe UI', 'Inter', 'Roboto', sans-serif;
}

/* ---------- Generic button base ---------- */
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
QPushButton:pressed {
    background-color: #1a1b2a;
}
QPushButton:disabled {
    background-color: #181924;
    color: #555570;
    border-color: #23243a;
}

/* ---------- Big mode buttons (main menu) ---------- */
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
QPushButton#modeBtn:pressed {
    background-color: #181a38;
}

/* ---------- Primary action button (Start Streaming, etc.) ---------- */
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
QPushButton#primaryBtn:pressed {
    background-color: #2e32a8;
}

/* ---------- Stop / danger button ---------- */
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
QPushButton#stopBtn:pressed {
    background-color: #6e1212;
}

/* ---------- Back button ---------- */
QPushButton#backBtn {
    background-color: #1a1b2a;
    border: 1px solid #2e3050;
    border-radius: 10px;
    font-size: 13px;
    color: #8888aa;
    padding: 8px 20px;
    min-width: 100px;
}
QPushButton#backBtn:hover {
    border-color: #5a5fbb;
    color: #c0c2e8;
}

/* ---------- Labels ---------- */
QLabel#titleLabel {
    font-size: 28px;
    font-weight: 800;
    color: #d8daff;
    letter-spacing: 1px;
}
QLabel#subLabel {
    font-size: 15px;
    color: #8888bb;
}
QLabel#instructionLabel {
    font-size: 16px;
    color: #c0c2e0;
    line-height: 1.6;
}
QLabel#statusLabel {
    font-size: 14px;
    color: #7070aa;
}
QLabel#wip {
    font-size: 18px;
    color: #9090cc;
    font-style: italic;
}
QLabel#streamingStatus {
    font-size: 22px;
    font-weight: 700;
    color: #58d68d;
}

/* ---------- Separator ---------- */
QFrame#separator {
    background-color: #2a2b42;
    max-height: 1px;
}
"""

# ---------------------------------------------------------------------------
# Helper: styled horizontal rule
# ---------------------------------------------------------------------------

def hr():
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.HLine)
    return line


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class MainMenuPage(QWidget):
    """Landing page — two big mode buttons."""

    def __init__(self, on_usb, on_wifi):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

        # Header
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

        # Mode buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(36)

        usb_btn = QPushButton("🔌  USB Mode")
        usb_btn.setObjectName("modeBtn")
        usb_btn.clicked.connect(on_usb)

        wifi_btn = QPushButton("📶  Wi-Fi Mode")
        wifi_btn.setObjectName("modeBtn")
        wifi_btn.clicked.connect(on_wifi)

        btn_row.addStretch()
        btn_row.addWidget(usb_btn)
        btn_row.addWidget(wifi_btn)
        btn_row.addStretch()

        root.addLayout(btn_row)
        root.addStretch()

        footer = QLabel("Select a connection mode to begin")
        footer.setObjectName("statusLabel")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)


class WifiPage(QWidget):
    """Wi-Fi placeholder."""

    def __init__(self, on_back):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

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
        back_row = QHBoxLayout()
        back_row.addStretch()
        back_row.addWidget(back)
        back_row.addStretch()
        root.addLayout(back_row)
        root.addSpacing(20)


class UsbStep1Page(QWidget):
    """Step 1 — connect the tablet."""

    def __init__(self, on_back, on_connected):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

        # Step indicator
        step = QLabel("USB Mode  ·  Step 1 of 2")
        step.setObjectName("statusLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addSpacing(48)

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
        msg.setObjectName("instructionLabel")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)

        root.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        self.next_btn = QPushButton("✔  I have connected it")
        self.next_btn.setObjectName("primaryBtn")
        self.next_btn.clicked.connect(on_connected)

        back = QPushButton("← Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        btn_row.addStretch()
        btn_row.addWidget(back)
        btn_row.addWidget(self.next_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addSpacing(20)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("statusLabel")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status_lbl)
        root.addSpacing(10)

    def set_status(self, text: str):
        self.status_lbl.setText(text)

    def set_busy(self, busy: bool):
        self.next_btn.setEnabled(not busy)


class UsbStep2Page(QWidget):
    """Step 2 — open app and start streaming."""

    def __init__(self, on_back, on_start):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

        step = QLabel("USB Mode  ·  Step 2 of 2")
        step.setObjectName("statusLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addSpacing(48)

        root.addStretch()

        icon = QLabel("📱")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI", 48))
        root.addWidget(icon)
        root.addSpacing(24)

        msg = QLabel("Please open the Monitorize app on your tablet.")
        msg.setObjectName("instructionLabel")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)

        root.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        self.start_btn = QPushButton("▶  Start Streaming")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(on_start)

        back = QPushButton("← Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        btn_row.addStretch()
        btn_row.addWidget(back)
        btn_row.addWidget(self.start_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addSpacing(30)


class StreamingPage(QWidget):
    """Step 3 — streaming active."""

    def __init__(self, on_stop):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

        root.addStretch()

        dot = QLabel("⬤")
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setStyleSheet("color: #58d68d; font-size: 28px;")
        root.addWidget(dot)
        root.addSpacing(16)

        status = QLabel("Status: Streaming…")
        status.setObjectName("streamingStatus")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(status)

        root.addSpacing(10)

        hint = QLabel("Your virtual display is being streamed to the tablet over USB.")
        hint.setObjectName("statusLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addStretch()

        stop = QPushButton("⏹  Stop Streaming")
        stop.setObjectName("stopBtn")
        stop.clicked.connect(on_stop)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(stop)
        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addSpacing(40)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

PAGE_MAIN     = 0
PAGE_WIFI     = 1
PAGE_USB1     = 2
PAGE_USB2     = 3
PAGE_STREAMING = 4


class MonitorizeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(700, 500)
        self.resize(820, 560)

        self._proc_krfb:    QProcess | None = None
        self._proc_stream:  QProcess | None = None

        # Shared QProcess objects for blocking adb commands (step 1)
        self._proc_adb_devices: QProcess | None = None
        self._proc_adb_forward: QProcess | None = None

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # --- build pages ---
        self._page_main      = MainMenuPage(self._go_usb1, self._go_wifi)
        self._page_wifi      = WifiPage(self._go_main)
        self._page_usb1      = UsbStep1Page(self._go_main, self._on_connected)
        self._page_usb2      = UsbStep2Page(self._go_usb1_direct, self._on_start_streaming)
        self._page_streaming = StreamingPage(self._on_stop_streaming)

        self._stack.addWidget(self._page_main)       # 0
        self._stack.addWidget(self._page_wifi)       # 1
        self._stack.addWidget(self._page_usb1)       # 2
        self._stack.addWidget(self._page_usb2)       # 3
        self._stack.addWidget(self._page_streaming)  # 4

        self._stack.setCurrentIndex(PAGE_MAIN)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _go_main(self):
        self._stack.setCurrentIndex(PAGE_MAIN)

    def _go_wifi(self):
        self._stack.setCurrentIndex(PAGE_WIFI)

    def _go_usb1(self):
        self._page_usb1.set_status("")
        self._page_usb1.set_busy(False)
        self._stack.setCurrentIndex(PAGE_USB1)

    def _go_usb1_direct(self):
        """Back from step 2 → step 1 without resetting status."""
        self._stack.setCurrentIndex(PAGE_USB1)

    # ------------------------------------------------------------------
    # Step 1: ADB setup
    # ------------------------------------------------------------------

    def _on_connected(self):
        self._page_usb1.set_busy(True)
        self._page_usb1.set_status("⏳  Running adb devices…")

        self._proc_adb_devices = QProcess(self)
        self._proc_adb_devices.finished.connect(self._adb_devices_done)
        self._proc_adb_devices.start("adb", ["devices"])

    def _adb_devices_done(self, exit_code, _exit_status):
        if exit_code != 0:
            self._page_usb1.set_status("❌  adb devices failed. Is ADB installed?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("⏳  Forwarding port tcp:7110…")
        self._proc_adb_forward = QProcess(self)
        self._proc_adb_forward.finished.connect(self._adb_forward_done)
        self._proc_adb_forward.start("adb", ["forward", "tcp:7110", "tcp:7110"])

    def _adb_forward_done(self, exit_code, _exit_status):
        if exit_code != 0:
            self._page_usb1.set_status("❌  Port forward failed. Is a device connected?")
            self._page_usb1.set_busy(False)
            return

        self._page_usb1.set_status("✅  Device ready!")
        self._page_usb1.set_busy(False)
        # Short delay so the user can see the success message
        QTimer.singleShot(600, lambda: self._stack.setCurrentIndex(PAGE_USB2))

    # ------------------------------------------------------------------
    # Step 2: Start streaming
    # ------------------------------------------------------------------

    def _on_start_streaming(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # --- Command A: krfb-virtualmonitor ---
        self._proc_krfb = QProcess(self)
        self._proc_krfb.setWorkingDirectory(script_dir)
        self._proc_krfb.start(
            "krfb-virtualmonitor",
            [
                "--resolution", "2560x1600",
                "--name",       "TabletDisplay",
                "--password",   "test123",
                "--port",       "5900",
            ],
        )

        # --- Command B: python3 Streamer_usb.py ---
        self._proc_stream = QProcess(self)
        self._proc_stream.setWorkingDirectory(script_dir)
        self._proc_stream.start("python3", ["Streamer_usb.py"])

        self._stack.setCurrentIndex(PAGE_STREAMING)

    # ------------------------------------------------------------------
    # Stop streaming
    # ------------------------------------------------------------------

    def _on_stop_streaming(self):
        self._kill_stream_procs()
        self._go_main()

    def _kill_stream_procs(self):
        for proc in (self._proc_krfb, self._proc_stream):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self._proc_krfb   = None
        self._proc_stream = None

    # ------------------------------------------------------------------
    # Window close — always clean up
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

    # Set dark palette so native widgets (scrollbars etc.) also look dark
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
