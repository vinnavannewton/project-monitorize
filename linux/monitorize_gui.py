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
    QComboBox, QCheckBox, QSystemTrayIcon, QMenu,
    QDialog, QMessageBox, QLineEdit,
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt6.QtGui import QColor, QPalette, QFont, QTextCursor, QIcon, QPixmap, QPainter

# ---------------------------------------------------------------------------
# Dark stylesheet
# ---------------------------------------------------------------------------

DARK_QSS = """
/* ── Base ─────────────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #0c0d14;
    color: #d4d6f0;
    font-family: 'Inter', 'SF Pro Display', 'Segoe UI', sans-serif;
    font-size: 14px;
}

/* ── Generic Button ───────────────────────────────────────────────── */
QPushButton {
    background-color: #16182a;
    color: #b8bad8;
    border: 1px solid #252845;
    border-radius: 10px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #1e2040;
    border-color: #4a4faa;
    color: #e8e9ff;
}
QPushButton:pressed { background-color: #121428; }
QPushButton:disabled {
    background-color: #101220;
    color: #3e3f5a;
    border-color: #1a1c30;
}

/* ── Mode Buttons (USB / Wi-Fi) ───────────────────────────────────── */
QPushButton#modeBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #141630, stop:1 #1c1e3a);
    border: 1px solid #2a2d55;
    border-radius: 16px;
    font-size: 18px;
    font-weight: 700;
    color: #c0c2ee;
    min-width: 200px;
    min-height: 110px;
}
QPushButton#modeBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #1c1e42, stop:1 #24264e);
    border-color: #5458b8;
    color: #ffffff;
}

/* ── Primary Action Button ────────────────────────────────────────── */
QPushButton#primaryBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #3538b0, stop:1 #4c4fd0);
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    min-height: 50px;
    min-width: 240px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #4042c8, stop:1 #5c5ee8);
}
QPushButton#primaryBtn:pressed { background-color: #2a2c98; }

/* ── Stop Button ──────────────────────────────────────────────────── */
QPushButton#stopBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #7a1520, stop:1 #a82028);
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    min-height: 50px;
    min-width: 240px;
}
QPushButton#stopBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #941a24, stop:1 #c42830);
}
QPushButton#stopBtn:pressed { background-color: #5a1010; }

/* ── Back Button ──────────────────────────────────────────────────── */
QPushButton#backBtn {
    background-color: transparent;
    border: 1px solid #252845;
    border-radius: 10px;
    font-size: 13px;
    color: #6a6c90;
    padding: 8px 18px;
    min-width: 90px;
}
QPushButton#backBtn:hover { border-color: #4a4faa; color: #b0b2d8; }

/* ── Labels ───────────────────────────────────────────────────────── */
QLabel#titleLabel {
    font-size: 30px;
    font-weight: 800;
    color: #e0e2ff;
    letter-spacing: 2px;
}
QLabel#subLabel    { font-size: 14px; color: #6a6c96; font-weight: 400; }
QLabel#stepLabel   { font-size: 12px; color: #5a5c82; font-weight: 500; letter-spacing: 1px; }
QLabel#instruction { font-size: 15px; color: #b0b2d0; }
QLabel#wip         { font-size: 17px; color: #7878aa; font-style: italic; }
QLabel#streaming   { font-size: 20px; font-weight: 700; color: #4cd68d; }
QLabel#statusLbl   { font-size: 12px; color: #5a5c82; }

/* ── Log Box ──────────────────────────────────────────────────────── */
QPlainTextEdit#logBox {
    background-color: #080910;
    color: #7cc87c;
    border: 1px solid #1a1c30;
    border-radius: 8px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
    padding: 8px;
    selection-background-color: #2a2d55;
}

/* ── Separator ────────────────────────────────────────────────────── */
QFrame#sep {
    background-color: #1a1c30;
    max-height: 1px;
}

/* ── Combo Box ────────────────────────────────────────────────────── */
QComboBox {
    background-color: #12142a;
    color: #c0c2ee;
    border: 1px solid #2a2d55;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    min-width: 140px;
}
QComboBox:hover { border-color: #5458b8; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #12142a;
    color: #c0c2ee;
    selection-background-color: #3538b0;
    selection-color: #ffffff;
    border: 1px solid #2a2d55;
    border-radius: 6px;
    padding: 4px;
}

/* ── Custom Input ─────────────────────────────────────────────────── */
QLineEdit#customInput {
    background-color: #12142a;
    color: #c0c2ee;
    border: 1px solid #2a2d55;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 600;
    min-width: 80px;
    max-width: 90px;
}
QLineEdit#customInput:focus { border-color: #5458b8; }
QLineEdit#customInput[invalid="true"] {
    border-color: #a82028;
    color: #ff6060;
}

QLabel#xSep {
    font-size: 18px;
    font-weight: 700;
    color: #6a6c96;
    padding: 0 4px;
}
QLabel#customHint {
    font-size: 11px;
    color: #4a4c70;
    font-style: italic;
}

/* ── Warning Label ────────────────────────────────────────────────── */
QLabel#warningLabel {
    font-size: 12px;
    font-weight: 600;
    color: #e8a840;
    padding: 8px 14px;
    background-color: rgba(232, 168, 64, 0.06);
    border: 1px solid rgba(232, 168, 64, 0.18);
    border-radius: 8px;
}

/* ── DE Badge ─────────────────────────────────────────────────────── */
QLabel#deBadge {
    font-size: 12px;
    font-weight: 600;
    color: #6a6cbb;
    padding: 4px 14px;
    background-color: rgba(76, 79, 208, 0.08);
    border: 1px solid rgba(76, 79, 208, 0.16);
    border-radius: 14px;
}

/* ── Portal Hint ──────────────────────────────────────────────────── */
QLabel#portalHint {
    font-size: 14px;
    font-weight: 500;
    color: #8a8cc0;
}

/* ── Tray Checkbox ────────────────────────────────────────────────── */
QCheckBox#trayCheck {
    font-size: 12px;
    color: #5a5c82;
    spacing: 8px;
}
QCheckBox#trayCheck:hover { color: #9a9cc0; }
QCheckBox#trayCheck::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #2a2d55;
    border-radius: 4px;
    background-color: #12142a;
}
QCheckBox#trayCheck::indicator:checked {
    background-color: #4c4fd0;
    border-color: #4c4fd0;
}

/* ── Scrollbar ────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a2d55;
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #3a3d6a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ── Message Boxes / Dialogs ──────────────────────────────────────── */
QDialog {
    background-color: #0c0d14;
}
QMessageBox {
    background-color: #0c0d14;
}
QMessageBox QLabel {
    color: #d4d6f0;
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

def _make_tray_icon() -> QIcon:
    """Generate a simple coloured square icon for the system tray."""
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#4c4fd0"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------------
# Desktop-environment detection
# ---------------------------------------------------------------------------

def detect_desktop_environment() -> str:
    """
    Return "kde", "gnome", "hyprland", "sway", or "" (unknown) based on
    environment variables.  Checks XDG_CURRENT_DESKTOP, DESKTOP_SESSION,
    and the Hyprland/Sway-specific vars; case-insensitive.
    """
    xdg   = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION",      "").lower()
    # Hyprland sets HYPRLAND_INSTANCE_SIGNATURE; Sway sets SWAYSOCK
    hypr  = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    sway  = os.environ.get("SWAYSOCK", "")
    combined = xdg + " " + dsess

    if hypr or "hyprland" in combined:
        return "hyprland"
    if sway or "sway" in combined:
        return "sway"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    return ""


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class MainMenuPage(QWidget):
    def __init__(self, on_usb, on_wifi, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(50, 50, 50, 40)

        title = QLabel("Monitorize")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Linux → Android Display Bridge")
        sub.setObjectName("subLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- Desktop environment badge ----
        self._de_badge = QLabel("")
        self._de_badge.setObjectName("deBadge")
        self._de_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root.addWidget(title)
        root.addSpacing(6)
        root.addWidget(sub)
        root.addSpacing(10)
        root.addWidget(self._de_badge)
        root.addSpacing(24)
        root.addWidget(hr())
        root.addSpacing(44)

        row = QHBoxLayout()
        row.setSpacing(28)

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
        root.addSpacing(14)

        # ---- Tray option ----
        tray_row = QHBoxLayout()
        self.tray_checkbox = QCheckBox("Minimize to tray on close")
        self.tray_checkbox.setObjectName("trayCheck")
        self.tray_checkbox.setChecked(False)
        tray_row.addStretch()
        tray_row.addWidget(self.tray_checkbox)
        tray_row.addStretch()
        root.addLayout(tray_row)
        root.addSpacing(6)

    def update_de_badge(self, de: str):
        """Show the detected/selected desktop environment in the badge."""
        _icons = {
            "kde":      "🐉",
            "gnome":    "🦶",
            "hyprland": "🌊",
            "sway":     "🌿",
        }
        _labels = {
            "kde":      "KDE Plasma",
            "gnome":    "GNOME",
            "hyprland": "Hyprland",
            "sway":     "Sway",
        }
        icon  = _icons.get(de, "❓")
        label = _labels.get(de, de.upper() if de else "Unknown")
        self._de_badge.setText(f"{icon}  Desktop: {label}")


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
        "2560x1440", "2560x1600", "3840x2160", "Custom…",
    ]
    FPS_OPTIONS = ["30", "60", "90", "120", "Custom…"]

    def __init__(self, on_back, on_start, parent=None):
        super().__init__(parent)
        self._on_start_cb = on_start
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

        # ---- Resolution row ----
        res_row = QHBoxLayout()
        res_row.setSpacing(12)
        res_label = QLabel("Resolution:")
        res_label.setObjectName("instruction")
        self._res_combo = QComboBox()
        self._res_combo.addItems(self.RESOLUTIONS)
        self._res_combo.setCurrentText("2560x1600")
        self._res_combo.currentTextChanged.connect(self._on_res_changed)
        res_row.addStretch()
        res_row.addWidget(res_label)
        res_row.addWidget(self._res_combo)
        res_row.addStretch()
        root.addLayout(res_row)

        # Custom resolution input (hidden by default)
        self._custom_res_widget = QWidget()
        custom_res_inner = QHBoxLayout(self._custom_res_widget)
        custom_res_inner.setContentsMargins(0, 4, 0, 0)
        custom_res_inner.setSpacing(6)
        self._custom_w = QLineEdit()
        self._custom_w.setObjectName("customInput")
        self._custom_w.setPlaceholderText("Width")
        self._custom_w.setMaxLength(4)
        x_sep = QLabel("×")
        x_sep.setObjectName("xSep")
        self._custom_h = QLineEdit()
        self._custom_h.setObjectName("customInput")
        self._custom_h.setPlaceholderText("Height")
        self._custom_h.setMaxLength(4)
        res_hint = QLabel("(500 – 4000 each)")
        res_hint.setObjectName("customHint")
        custom_res_inner.addStretch()
        custom_res_inner.addWidget(self._custom_w)
        custom_res_inner.addWidget(x_sep)
        custom_res_inner.addWidget(self._custom_h)
        custom_res_inner.addWidget(res_hint)
        custom_res_inner.addStretch()
        self._custom_res_widget.setVisible(False)
        root.addWidget(self._custom_res_widget)
        root.addSpacing(10)

        # ---- FPS row ----
        fps_row = QHBoxLayout()
        fps_row.setSpacing(12)
        fps_label = QLabel("FPS:")
        fps_label.setObjectName("instruction")
        self._fps_combo = QComboBox()
        self._fps_combo.addItems(self.FPS_OPTIONS)
        self._fps_combo.setCurrentText("60")
        self._fps_combo.currentTextChanged.connect(self._on_fps_changed)
        fps_row.addStretch()
        fps_row.addWidget(fps_label)
        fps_row.addWidget(self._fps_combo)
        fps_row.addStretch()
        root.addLayout(fps_row)

        # Custom FPS input (hidden by default)
        self._custom_fps_widget = QWidget()
        custom_fps_inner = QHBoxLayout(self._custom_fps_widget)
        custom_fps_inner.setContentsMargins(0, 4, 0, 0)
        custom_fps_inner.setSpacing(6)
        self._custom_fps_edit = QLineEdit()
        self._custom_fps_edit.setObjectName("customInput")
        self._custom_fps_edit.setPlaceholderText("FPS")
        self._custom_fps_edit.setMaxLength(3)
        fps_hint = QLabel("(24 – 240)")
        fps_hint.setObjectName("customHint")
        custom_fps_inner.addStretch()
        custom_fps_inner.addWidget(self._custom_fps_edit)
        custom_fps_inner.addWidget(fps_hint)
        custom_fps_inner.addStretch()
        self._custom_fps_widget.setVisible(False)
        root.addWidget(self._custom_fps_widget)
        root.addSpacing(10)

        # ---- Bitrate row ----
        bitrate_row = QHBoxLayout()
        bitrate_row.setSpacing(12)
        bitrate_label = QLabel("Video Bitrate (kbps):")
        bitrate_label.setObjectName("instruction")
        self._bitrate_edit = QLineEdit()
        self._bitrate_edit.setObjectName("customInput")
        self._bitrate_edit.setText("8000")
        self._bitrate_edit.setMaxLength(5)
        bitrate_row.addStretch()
        bitrate_row.addWidget(bitrate_label)
        bitrate_row.addWidget(self._bitrate_edit)
        bitrate_row.addStretch()
        root.addLayout(bitrate_row)
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

        self._start_btn = QPushButton("▶  Start Streaming")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.clicked.connect(self._validate_and_start)

        row = QHBoxLayout()
        row.setSpacing(20)
        row.addStretch()
        row.addWidget(back)
        row.addWidget(self._start_btn)
        row.addStretch()
        root.addLayout(row)
        root.addSpacing(20)

    # -- Slot: show/hide custom resolution inputs --

    def _on_res_changed(self, text: str):
        self._custom_res_widget.setVisible(text == "Custom…")

    def _on_fps_changed(self, text: str):
        self._custom_fps_widget.setVisible(text == "Custom…")

    # -- Validation + start --

    def _validate_and_start(self):
        # Validate custom resolution if selected
        if self._res_combo.currentText() == "Custom…":
            try:
                w = int(self._custom_w.text())
                h = int(self._custom_h.text())
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Resolution",
                    "Please enter numeric values for width and height."
                )
                return
            if not (500 <= w <= 4000):
                QMessageBox.warning(
                    self, "Invalid Resolution",
                    f"Width must be between 500 and 4000.\nYou entered: {w}"
                )
                return
            if not (500 <= h <= 4000):
                QMessageBox.warning(
                    self, "Invalid Resolution",
                    f"Height must be between 500 and 4000.\nYou entered: {h}"
                )
                return

        # Validate custom FPS if selected
        if self._fps_combo.currentText() == "Custom…":
            try:
                fps = int(self._custom_fps_edit.text())
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid FPS",
                    "Please enter a numeric value for FPS."
                )
                return
            if not (24 <= fps <= 240):
                QMessageBox.warning(
                    self, "Invalid FPS",
                    f"FPS must be between 24 and 240.\nYou entered: {fps}"
                )
                return

        # Validate bitrate
        try:
            bitrate = int(self._bitrate_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Bitrate", "Please enter a numeric value for Bitrate.")
            return
        if not (1000 <= bitrate <= 100000):
            QMessageBox.warning(self, "Invalid Bitrate", "Bitrate must be between 1000 and 100000 kbps.")
            return

        self._on_start_cb()

    # -- Public getters used by MonitorizeWindow --

    def selected_resolution(self) -> tuple[int, int]:
        """Return (width, height) — handles Custom… selection."""
        if self._res_combo.currentText() == "Custom…":
            return int(self._custom_w.text()), int(self._custom_h.text())
        text = self._res_combo.currentText()   # e.g. "2560x1600"
        w, h = text.split("x")
        return int(w), int(h)

    def selected_fps(self) -> int:
        if self._fps_combo.currentText() == "Custom…":
            return int(self._custom_fps_edit.text())
        return int(self._fps_combo.currentText())

    def selected_bitrate(self) -> int:
        return int(self._bitrate_edit.text())


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

        # ------------------------------------------------------------------
        # Desktop-environment detection (runs once at startup)
        # ------------------------------------------------------------------
        detected = detect_desktop_environment()
        if detected:
            self.detected_de = detected
        else:
            self.detected_de = self._ask_desktop_environment()
        # Badge is updated after pages are added to the stack (see below)

        # Persistent QProcess objects for streaming + input forwarding
        self.process_krfb:          QProcess | None = None
        self.process_streamer:      QProcess | None = None
        self.process_input_bridge:  QProcess | None = None

        # Transient QProcess objects for ADB (step 1)
        self._proc_adb_dev:  QProcess | None = None
        self._proc_adb_fwd:  QProcess | None = None
        self._proc_adb_fwd2:  QProcess | None = None   # adb forward for port 7111

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

        # Show detected DE in the main menu badge
        self._page_main.update_de_badge(self.detected_de)

        # ------------------------------------------------------------------
        # System tray icon
        # ------------------------------------------------------------------
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
        # Tray icon is shown only when the window is hidden to tray
        self._tray.hide()

    # ------------------------------------------------------------------
    # DE selection dialog (shown only when auto-detection fails)
    # ------------------------------------------------------------------

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

        # Row 1: KDE / GNOME
        row1 = QHBoxLayout()
        row1.setSpacing(14)
        kde_btn   = QPushButton("🐉  KDE")
        gnome_btn = QPushButton("🦶  GNOME")
        row1.addWidget(kde_btn)
        row1.addWidget(gnome_btn)
        layout.addLayout(row1)

        # Row 2: Hyprland / Sway
        row2 = QHBoxLayout()
        row2.setSpacing(14)
        hypr_btn  = QPushButton("🌊  Hyprland")
        sway_btn  = QPushButton("🌿  Sway")
        row2.addWidget(hypr_btn)
        row2.addWidget(sway_btn)
        layout.addLayout(row2)

        # Row 3: Other
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

        return chosen[0]   # "kde" | "gnome" | "hyprland" | "sway"

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_main(self):
        self._stack.setCurrentIndex(PAGE_MAIN)

    def _go_wifi(self):
        """Navigate to Wi-Fi page (KDE only) or show not-supported for other DEs."""
        if self.detected_de == "kde":
            # KDE has a Wi-Fi streamer (work in progress)
            self._stack.setCurrentIndex(PAGE_WIFI)
            return

        # All other DEs: Wi-Fi not yet supported
        _de_labels = {
            "gnome":    "GNOME",
            "hyprland": "Hyprland",
            "sway":     "Sway",
        }
        de_label = _de_labels.get(self.detected_de, self.detected_de.upper())
        msg = QMessageBox(self)
        msg.setWindowTitle(f"Wi-Fi Mode — {de_label}")
        msg.setText(
            f"Wi-Fi mode is not yet supported on {de_label}.\n"
            "Please use USB mode."
        )
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

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

        # Touch daemon acts as a Server on Linux port 7111.
        # We need adb reverse so Android can connect to localhost:7111 and reach Linux.
        self._page_usb1.set_status("⏳  Setting up reverse proxy tcp:7111 (touch)…")
        self._proc_adb_fwd2 = QProcess(self)
        self._proc_adb_fwd2.finished.connect(self._adb_forward2_done)
        self._proc_adb_fwd2.start("adb", ["reverse", "tcp:7111", "tcp:7111"])

    def _adb_forward2_done(self, exit_code, _status):
        # Non-fatal: if 7111 fails, video still works, touch just won't forward
        if exit_code != 0:
            self._page_usb1.set_status("⚠️  tcp:7111 reverse failed — touch disabled")
        else:
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
        self._stack.setCurrentIndex(PAGE_STREAMING)

        # Read user-chosen resolution / FPS / Bitrate
        width, height = self._page_usb2.selected_resolution()
        fps = self._page_usb2.selected_fps()
        bitrate = self._page_usb2.selected_bitrate()
        self._stream_width  = width
        self._stream_height = height
        self._stream_fps    = fps
        self._stream_bitrate = bitrate

        # ---- KDE: start krfb-virtualmonitor first, wait 5 seconds, then launch streamer ----
        if self.detected_de == "kde":
            self._page_streaming.set_status("⏳  Starting virtual monitor…  5")
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
            import subprocess
            subprocess.run(["killall", "krfb-virtualmonitor"], capture_output=True)

            self.process_krfb.start(
                "krfb-virtualmonitor",
                [
                    "--resolution", f"{width}x{height}",
                    "--name",       "TabletDisplay",
                    "--password",   "test123",
                    "--port",       "5900",
                ],
            )
            # Begin 5-second countdown before starting the streamer
            self._countdown = 5
            self._countdown_timer.start()
        else:
            # GNOME / Hyprland / Sway handle virtual monitors internally
            self._page_streaming.set_status("⏳  Launching streamer…")
            self._launch_streamer()

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
        self.process_streamer.finished.connect(
            lambda code, _: self._page_streaming.append_log(
                "STREAMER", f"Process exited (code {code})"
            )
        )
        # Choose the correct streamer script based on the detected DE
        _streamer_map = {
            "kde":      "Streamer_kde_usb.py",
            "gnome":    "Streamer_gnome_usb.py",
            "hyprland": "Streamer_hyprland_usb.py",
            "sway":     "Streamer_sway_usb.py",
        }
        streamer_script = _streamer_map.get(self.detected_de, "Streamer_kde_usb.py")

        self.process_streamer.start("python3", [
            streamer_script,
            str(self._stream_width),
            str(self._stream_height),
            str(self._stream_fps),
            str(self._stream_bitrate),
        ])

        # ── Launch input bridge AFTER streamer (compositor-agnostic, separate port 7111) ──
        # The streamer triggers the screen-share display selector popup;
        # the input bridge triggers the input permission popup.
        # We want display selector first, input permission second, back-to-back.
        if self.detected_de == "kde":
            QTimer.singleShot(400, self._launch_input_bridge)

        self._page_streaming.set_status("⬤  Status: Streaming…")
        self._page_streaming.set_stop_enabled(True)

    def _launch_input_bridge(self):
        """Spawn input_bridge.py — listens on port 7111 for Android touch/pen events."""
        self.process_input_bridge = QProcess(self)
        self.process_input_bridge.setWorkingDirectory(self._script_dir)
        self.process_input_bridge.setProcessEnvironment(self._env)
        self.process_input_bridge.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process_input_bridge.readyReadStandardOutput.connect(self._read_input_bridge)
        self.process_input_bridge.finished.connect(
            lambda code, _: self._page_streaming.append_log(
                "INPUT", f"Bridge exited (code {code})"
            )
        )
        self.process_input_bridge.start("python3", [
            os.path.join(self._script_dir, "touch_daemon.py"),
            str(self._stream_width),
            str(self._stream_height),
        ])

        self._page_streaming.set_status(
            "🖐  Touch service starting… Watch for 'Allow Remote Control' popup and click Allow"
        )



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

    def _read_input_bridge(self):
        if self.process_input_bridge is None:
            return
        raw = bytes(self.process_input_bridge.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._page_streaming.append_log("INPUT", raw)

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

        for proc in (self.process_krfb, self.process_streamer, self.process_input_bridge):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self.process_krfb          = None
        self.process_streamer      = None
        self.process_input_bridge  = None

    # ------------------------------------------------------------------
    # Tray helpers
    # ------------------------------------------------------------------

    def _tray_activated(self, reason):
        """Restore the window when the tray icon is double-clicked."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self._tray.hide()
        self.showNormal()
        self.activateWindow()

    def _quit_app(self):
        """Hard quit from the tray menu — always terminates processes."""
        self._kill_stream_procs()
        QApplication.quit()

    # ------------------------------------------------------------------
    # Close event — hide to tray or quit depending on checkbox
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._page_main.tray_checkbox.isChecked():
            # Hide to tray instead of closing
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
            event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
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
