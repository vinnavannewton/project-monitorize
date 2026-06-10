"""
Monitorize GUI — Main menu page (USB / Wi-Fi selection).
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon

from gui.utils import hr, LINUX_DIR
from gui.settings import load_general_settings, save_general_settings


class MainMenuPage(QWidget):
    def __init__(self, on_usb, on_wifi, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(50, 50, 50, 40)

        title = QLabel("Monitorize")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Linux \u2192 Android Display Bridge")
        sub.setObjectName("subLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._de_badge = QFrame()
        self._de_badge.setObjectName("deBadge")
        badge_layout = QHBoxLayout(self._de_badge)
        badge_layout.setContentsMargins(14, 4, 14, 4)
        badge_layout.setSpacing(6)

        self._de_badge_icon = QLabel()
        self._de_badge_text = QLabel("")
        badge_layout.addStretch()
        badge_layout.addWidget(self._de_badge_icon)
        badge_layout.addWidget(self._de_badge_text)
        badge_layout.addStretch()

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

        usb_svg = os.path.join(LINUX_DIR, "assets", "svg", "usb-logo.svg")
        wifi_svg = os.path.join(LINUX_DIR, "assets", "svg", "wifi-logo.svg")

        usb_btn = QPushButton("  USB Mode")
        usb_btn.setObjectName("modeBtn")
        if os.path.exists(usb_svg):
            usb_btn.setIcon(QIcon(usb_svg))
            usb_btn.setIconSize(QSize(20, 20))
        usb_btn.clicked.connect(on_usb)

        wifi_btn = QPushButton("  Wi-Fi Mode")
        wifi_btn.setObjectName("modeBtn")
        if os.path.exists(wifi_svg):
            wifi_btn.setIcon(QIcon(wifi_svg))
            wifi_btn.setIconSize(QSize(20, 20))
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



    def update_de_badge(self, de: str):
        """Show the detected/selected desktop environment in the badge."""
        _icons = {
            "kde":      os.path.join(LINUX_DIR, "assets", "svg", "kde-logo.svg"),
            "gnome":    os.path.join(LINUX_DIR, "assets", "svg", "gnome-logo.svg"),
            "hyprland": os.path.join(LINUX_DIR, "assets", "svg", "hyprland-logo.svg"),
        }
        _labels = {
            "kde":      "KDE Plasma",
            "gnome":    "GNOME",
            "hyprland": "Hyprland",
            "sway":     "Sway",
        }
        icon_path = _icons.get(de)
        if icon_path and os.path.exists(icon_path):
            self._de_badge_icon.setPixmap(QIcon(icon_path).pixmap(16, 16))
            self._de_badge_icon.setVisible(True)
        else:
            self._de_badge_icon.setVisible(False)

        label = _labels.get(de, de.upper() if de else "Unknown")
        self._de_badge_text.setText(f"Desktop: {label}")
