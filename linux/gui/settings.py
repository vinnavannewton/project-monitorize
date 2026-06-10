"""
Monitorize GUI — Persistent settings stored in ~/.config/monitorize/settings.ini
"""

import os
from PyQt6.QtCore import QSettings

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "monitorize")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.ini")


def _get_settings() -> QSettings:
    """Return a QSettings object backed by the INI file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    return QSettings(CONFIG_FILE, QSettings.Format.IniFormat)




def save_wifi_settings(*, resolution: str, custom_w: str, custom_h: str,
                       fps: str, custom_fps: str, bitrate: str,
                       display_type: str):
    s = _get_settings()
    s.beginGroup("wifi")
    s.setValue("resolution", resolution)
    s.setValue("custom_w", custom_w)
    s.setValue("custom_h", custom_h)
    s.setValue("fps", fps)
    s.setValue("custom_fps", custom_fps)
    s.setValue("bitrate", bitrate)
    s.setValue("display_type", display_type)
    s.endGroup()
    s.sync()


def save_usb_settings(*, resolution: str, custom_w: str, custom_h: str,
                      fps: str, custom_fps: str, bitrate: str,
                      display_type: str):
    s = _get_settings()
    s.beginGroup("usb")
    s.setValue("resolution", resolution)
    s.setValue("custom_w", custom_w)
    s.setValue("custom_h", custom_h)
    s.setValue("fps", fps)
    s.setValue("custom_fps", custom_fps)
    s.setValue("bitrate", bitrate)
    s.setValue("display_type", display_type)
    s.endGroup()
    s.sync()


def save_general_settings(*, minimize_to_tray: bool):
    s = _get_settings()
    s.beginGroup("general")
    s.setValue("minimize_to_tray", minimize_to_tray)
    s.endGroup()
    s.sync()




def load_wifi_settings() -> dict:
    s = _get_settings()
    s.beginGroup("wifi")
    data = {
        "resolution":   s.value("resolution",   "2560x1600"),
        "custom_w":     s.value("custom_w",     ""),
        "custom_h":     s.value("custom_h",     ""),
        "fps":          s.value("fps",          "60"),
        "custom_fps":   s.value("custom_fps",   ""),
        "bitrate":      s.value("bitrate",      "8000"),
        "display_type": s.value("display_type", "Extend Right"),
    }
    s.endGroup()
    return data


def load_usb_settings() -> dict:
    s = _get_settings()
    s.beginGroup("usb")
    data = {
        "resolution":   s.value("resolution",   "2560x1600"),
        "custom_w":     s.value("custom_w",     ""),
        "custom_h":     s.value("custom_h",     ""),
        "fps":          s.value("fps",          "60"),
        "custom_fps":   s.value("custom_fps",   ""),
        "bitrate":      s.value("bitrate",      "8000"),
        "display_type": s.value("display_type", "Extend Right"),
    }
    s.endGroup()
    return data


def load_general_settings() -> dict:
    s = _get_settings()
    s.beginGroup("general")
    data = {
        "minimize_to_tray": s.value("minimize_to_tray", False, type=bool),
    }
    s.endGroup()
    return data
