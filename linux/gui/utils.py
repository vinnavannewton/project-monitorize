
"""
Monitorize GUI — Utility functions.
"""

import os
from PyQt6.QtWidgets import QFrame
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter



LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hr() -> QFrame:
    line = QFrame()
    line.setObjectName("sep")
    line.setFrameShape(QFrame.Shape.HLine)
    return line

def vspace(n: int) -> int:
    return n  

def _make_tray_icon() -> QIcon:
    """Load the custom Monitorize tray icon (white variant) from assets.

    Falls back to a programmatic icon if the PNG is missing.
    """
    icon_path = os.path.join(LINUX_DIR, "assets", "tray", "icon_tray_white.svg")
    if os.path.exists(icon_path):
        return QIcon(icon_path)

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


def detect_desktop_environment() -> str:
    """
    Return "kde", "gnome", "hyprland", "sway", or "" (unknown) based on
    environment variables.  Checks XDG_CURRENT_DESKTOP, DESKTOP_SESSION,
    and the Hyprland/Sway-specific vars; case-insensitive.
    """
    xdg   = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION",      "").lower()
    
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


def get_local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP
