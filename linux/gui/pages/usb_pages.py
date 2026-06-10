"""
Monitorize GUI — USB mode pages (Step 1 and Step 2).
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon

from gui.utils import hr, LINUX_DIR
from gui.widgets import NonScrollComboBox
from gui.settings import load_usb_settings, save_usb_settings


class UsbStep1Page(QWidget):
    """Step 1 -- connect tablet and run ADB."""

    def __init__(self, on_back, on_connected, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 60, 60, 60)

        step = QLabel("USB Mode  \u00b7  Step 1 of 2")
        step.setObjectName("stepLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addStretch()

        usb_svg = os.path.join(LINUX_DIR, "assets", "svg", "usb-logo.svg")

        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if os.path.exists(usb_svg):
            icon.setPixmap(QIcon(usb_svg).pixmap(96, 96))
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

        self._next_btn = QPushButton("I have connected it")
        self._next_btn.setObjectName("primaryBtn")
        self._next_btn.clicked.connect(on_connected)

        back = QPushButton("\u2190 Back")
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
    """Step 2 -- configure resolution/FPS, open tablet app, start streaming."""

    RESOLUTIONS = [
        "1280x720", "1280x800", "1920x1080", "1920x1200",
        "2560x1440", "2560x1600", "3840x2160", "Custom\u2026",
    ]
    FPS_OPTIONS = ["30", "60", "90", "120", "Custom\u2026"]

    def __init__(self, on_back, on_start, detected_de="kde", parent=None):
        super().__init__(parent)
        self._on_start_cb = on_start
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 40, 60, 40)

        step = QLabel("USB Mode  \u00b7  Step 2 of 2")
        step.setObjectName("stepLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addSpacing(24)

        msg = QLabel("Please open the Monitorize app on your tablet.")
        msg.setObjectName("instruction")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)
        root.addSpacing(20)

        res_row = QHBoxLayout()
        res_row.setSpacing(12)
        res_label = QLabel("Resolution:")
        res_label.setObjectName("instruction")
        self._res_combo = NonScrollComboBox()
        self._res_combo.addItems(self.RESOLUTIONS)
        self._res_combo.setCurrentText("2560x1600")
        self._res_combo.currentTextChanged.connect(self._on_res_changed)
        res_row.addStretch()
        res_row.addWidget(res_label)
        res_row.addWidget(self._res_combo)
        res_row.addStretch()
        root.addLayout(res_row)

        self._custom_res_widget = QWidget()
        custom_res_inner = QHBoxLayout(self._custom_res_widget)
        custom_res_inner.setContentsMargins(0, 4, 0, 0)
        custom_res_inner.setSpacing(6)
        self._custom_w = QLineEdit()
        self._custom_w.setObjectName("customInput")
        self._custom_w.setPlaceholderText("Width")
        self._custom_w.setMaxLength(4)
        x_sep = QLabel("\u00d7")
        x_sep.setObjectName("xSep")
        self._custom_h = QLineEdit()
        self._custom_h.setObjectName("customInput")
        self._custom_h.setPlaceholderText("Height")
        self._custom_h.setMaxLength(4)
        res_hint = QLabel("(500 \u2013 4000 each)")
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

        fps_row = QHBoxLayout()
        fps_row.setSpacing(12)
        fps_label = QLabel("FPS:")
        fps_label.setObjectName("instruction")
        self._fps_combo = NonScrollComboBox()
        self._fps_combo.addItems(self.FPS_OPTIONS)
        self._fps_combo.setCurrentText("60")
        self._fps_combo.currentTextChanged.connect(self._on_fps_changed)
        fps_row.addStretch()
        fps_row.addWidget(fps_label)
        fps_row.addWidget(self._fps_combo)
        fps_row.addStretch()
        root.addLayout(fps_row)

        self._custom_fps_widget = QWidget()
        custom_fps_inner = QHBoxLayout(self._custom_fps_widget)
        custom_fps_inner.setContentsMargins(0, 4, 0, 0)
        custom_fps_inner.setSpacing(6)
        self._custom_fps_edit = QLineEdit()
        self._custom_fps_edit.setObjectName("customInput")
        self._custom_fps_edit.setPlaceholderText("FPS")
        self._custom_fps_edit.setMaxLength(3)
        fps_hint = QLabel("(24 \u2013 240)")
        fps_hint.setObjectName("customHint")
        custom_fps_inner.addStretch()
        custom_fps_inner.addWidget(self._custom_fps_edit)
        custom_fps_inner.addWidget(fps_hint)
        custom_fps_inner.addStretch()
        self._custom_fps_widget.setVisible(False)
        root.addWidget(self._custom_fps_widget)
        root.addSpacing(10)

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

        self._show_display_type = (detected_de in ("gnome", "kde", "hyprland"))
        if self._show_display_type:
            type_row = QHBoxLayout()
            type_row.setSpacing(20)

            type_lbl = QLabel("Display Type:")
            type_lbl.setObjectName("instruction")
            self._display_type_combo = NonScrollComboBox()
            self._display_type_combo.addItems(["Extend Right", "Mirror"])
            self._display_type_combo.setCurrentText("Extend Right")

            type_row.addStretch()
            type_row.addWidget(type_lbl)
            type_row.addWidget(self._display_type_combo)
            type_row.addStretch()

            root.addLayout(type_row)
            root.addSpacing(16)

        
        encoder_row = QHBoxLayout()
        encoder_row.setSpacing(20)

        encoder_lbl = QLabel("Encoder:")
        encoder_lbl.setObjectName("instruction")
        self._encoder_combo = NonScrollComboBox()
        self._encoder_combo.addItems([
            "Auto-detect (Recommended)",
            "NVIDIA NVENC (nvh264enc)",
            "Intel/AMD VA-API (vah264enc)",
            "Software (CPU / x264enc)"
        ])
        self._encoder_combo.setCurrentText("Auto-detect (Recommended)")

        encoder_row.addStretch()
        encoder_row.addWidget(encoder_lbl)
        encoder_row.addWidget(self._encoder_combo)
        encoder_row.addStretch()

        root.addLayout(encoder_row)
        root.addSpacing(16)

        warning = QLabel(
            "WARNING: The Resolution and FPS set here MUST EXACTLY "
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

        step1_lbl = QLabel(
            "1.  Tap the  Receive  button on the Android app first."
        )
        step1_lbl.setObjectName("portalHint")
        step1_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step1_lbl.setWordWrap(True)
        root.addWidget(step1_lbl)
        root.addSpacing(10)

        step2_lbl = QLabel(
            '2.  Then click Start Streaming below. When the screen-sharing '
            'popup appears, select "TabletDisplay" and click Share.'
        )
        step2_lbl.setObjectName("portalHint")
        step2_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step2_lbl.setWordWrap(True)
        root.addWidget(step2_lbl)
        root.addSpacing(20)

        root.addStretch()

        back = QPushButton("\u2190 Back")
        back.setObjectName("backBtn")
        back.clicked.connect(on_back)

        self._start_btn = QPushButton("\u25b6  Start Streaming")
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

        
        self._restore_settings()

    def _restore_settings(self):
        """Load previously saved USB settings and apply them to widgets."""
        saved = load_usb_settings()
        self._res_combo.setCurrentText(saved["resolution"])
        if saved["resolution"] == "Custom\u2026":
            self._custom_w.setText(saved["custom_w"])
            self._custom_h.setText(saved["custom_h"])
            self._custom_res_widget.setVisible(True)
        self._fps_combo.setCurrentText(saved["fps"])
        if saved["fps"] == "Custom\u2026":
            self._custom_fps_edit.setText(saved["custom_fps"])
            self._custom_fps_widget.setVisible(True)
        self._bitrate_edit.setText(saved["bitrate"])
        if hasattr(self, "_display_type_combo"):
            self._display_type_combo.setCurrentText(saved["display_type"])
        self._encoder_combo.setCurrentText(saved.get("encoder", "Auto-detect (Recommended)"))

    def save_settings(self):
        """Persist current USB settings to disk."""
        save_usb_settings(
            resolution=self._res_combo.currentText(),
            custom_w=self._custom_w.text(),
            custom_h=self._custom_h.text(),
            fps=self._fps_combo.currentText(),
            custom_fps=self._custom_fps_edit.text(),
            bitrate=self._bitrate_edit.text(),
            display_type=self._display_type_combo.currentText()
                         if hasattr(self, "_display_type_combo") else "Extend Right",
            encoder=self._encoder_combo.currentText(),
        )

    def _on_res_changed(self, text: str):
        self._custom_res_widget.setVisible(text == "Custom\u2026")

    def _on_fps_changed(self, text: str):
        self._custom_fps_widget.setVisible(text == "Custom\u2026")

    def _validate_and_start(self):
        if self._res_combo.currentText() == "Custom\u2026":
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

        if self._fps_combo.currentText() == "Custom\u2026":
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

        try:
            bitrate = int(self._bitrate_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Bitrate", "Please enter a numeric value for Bitrate.")
            return
        if not (1000 <= bitrate <= 100000):
            QMessageBox.warning(self, "Invalid Bitrate", "Bitrate must be between 1000 and 100000 kbps.")
            return

        self.save_settings()
        self._on_start_cb()

    def selected_resolution(self) -> tuple[int, int]:
        """Return (width, height) -- handles Custom... selection."""
        if self._res_combo.currentText() == "Custom\u2026":
            return int(self._custom_w.text()), int(self._custom_h.text())
        text = self._res_combo.currentText()
        w, h = text.split("x")
        return int(w), int(h)

    def selected_fps(self) -> int:
        if self._fps_combo.currentText() == "Custom\u2026":
            return int(self._custom_fps_edit.text())
        return int(self._fps_combo.currentText())

    def selected_bitrate(self) -> int:
        return int(self._bitrate_edit.text())

    def selected_encoder(self) -> str:
        return self._encoder_combo.currentText()

    def gnome_scale(self) -> str:
        if hasattr(self, "_gnome_scale_combo"):
            return self._gnome_scale_combo.currentText()
        return "1.0"

    def gnome_type(self) -> str:
        if hasattr(self, "_display_type_combo"):
            return self._display_type_combo.currentText()
        return "Extend Right"
