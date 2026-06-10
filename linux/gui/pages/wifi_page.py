"""
Monitorize GUI — Wi-Fi mode configuration page.
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QCheckBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon

from gui.utils import hr, get_local_ip, LINUX_DIR
from gui.widgets import NonScrollComboBox
from gui.settings import (
    load_wifi_settings, save_wifi_settings, load_general_settings, save_general_settings
)


class WifiPage(QWidget):
    RESOLUTIONS = [
        "1280x720", "1280x800", "1920x1080", "1920x1200",
        "2560x1440", "2560x1600", "3840x2160", "Custom\u2026"
    ]
    FPS_OPTIONS = ["30", "60", "90", "120", "Custom\u2026"]

    def __init__(self, on_back, on_start, detected_de="kde", parent=None):
        super().__init__(parent)
        self._on_start_cb = on_start
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 40, 60, 40)

        step = QLabel("Wi-Fi Mode")
        step.setObjectName("stepLabel")
        root.addWidget(step)
        root.addSpacing(12)
        root.addWidget(hr())
        root.addSpacing(24)

        ip = get_local_ip()
        wifi_svg = os.path.join(LINUX_DIR, "assets", "svg", "wifi-logo.svg")

        wifi_header = QWidget()
        wifi_header_layout = QHBoxLayout(wifi_header)
        wifi_header_layout.setContentsMargins(0, 0, 0, 0)
        wifi_header_layout.setSpacing(8)

        wifi_icon_lbl = QLabel()
        if os.path.exists(wifi_svg):
            wifi_icon_lbl.setPixmap(QIcon(wifi_svg).pixmap(24, 24))

        wifi_text_lbl = QLabel(f"Your Local IP Address is: {ip}")
        wifi_text_lbl.setObjectName("instruction")
        wifi_text_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")

        wifi_header_layout.addStretch()
        wifi_header_layout.addWidget(wifi_icon_lbl)
        wifi_header_layout.addWidget(wifi_text_lbl)
        wifi_header_layout.addStretch()
        root.addWidget(wifi_header)

        msg = QLabel("Enter this IP in the Monitorize Android app and tap Receive.")
        msg.setObjectName("instruction")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 14px; color: #8a8cc0;")
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

        
        checkbox_row = QHBoxLayout()
        checkbox_row.setSpacing(24)

        self.tray_checkbox = QCheckBox("Minimize to tray on close")
        self.tray_checkbox.setObjectName("trayCheck")

        self.touch_checkbox = QCheckBox("Enable Touch Input")
        self.touch_checkbox.setObjectName("touchCheck")

        checkbox_row.addStretch()
        checkbox_row.addWidget(self.tray_checkbox)
        checkbox_row.addWidget(self.touch_checkbox)
        checkbox_row.addStretch()

        root.addLayout(checkbox_row)
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

        step2_lbl = QLabel(
            'Then click Start Streaming below. When the screen-sharing '
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
        """Load previously saved Wi-Fi settings and apply them to widgets."""
        saved = load_wifi_settings()
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

        
        self.reload_general_settings()

        self.tray_checkbox.toggled.connect(
            lambda checked: save_general_settings(minimize_to_tray=checked)
        )
        self.touch_checkbox.toggled.connect(
            lambda checked: save_general_settings(enable_touch=checked)
        )

    def reload_general_settings(self):
        """Reload general settings to stay in sync."""
        self.tray_checkbox.blockSignals(True)
        self.touch_checkbox.blockSignals(True)
        try:
            gen = load_general_settings()
            self.tray_checkbox.setChecked(gen.get("minimize_to_tray", False))
            self.touch_checkbox.setChecked(gen.get("enable_touch", True))
        finally:
            self.tray_checkbox.blockSignals(False)
            self.touch_checkbox.blockSignals(False)

    def save_settings(self):
        """Persist current Wi-Fi settings to disk."""
        save_wifi_settings(
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
            except ValueError: return
        if self._fps_combo.currentText() == "Custom\u2026":
            try: fps = int(self._custom_fps_edit.text())
            except ValueError: return
        try: bitrate = int(self._bitrate_edit.text())
        except ValueError: return

        self.save_settings()
        self._on_start_cb()

    def selected_resolution(self) -> tuple[int, int]:
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
