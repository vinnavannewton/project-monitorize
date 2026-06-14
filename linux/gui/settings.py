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
                       display_type: str, encoder: str, stream_type: str):
    s = _get_settings()
    s.beginGroup("wifi")
    s.setValue("resolution", resolution)
    s.setValue("custom_w", custom_w)
    s.setValue("custom_h", custom_h)
    s.setValue("fps", fps)
    s.setValue("custom_fps", custom_fps)
    s.setValue("bitrate", bitrate)
    s.setValue("display_type", display_type)
    s.setValue("encoder", encoder)
    s.setValue("stream_type", stream_type)
    s.endGroup()
    s.sync()


def save_usb_settings(*, resolution: str, custom_w: str, custom_h: str,
                      fps: str, custom_fps: str, bitrate: str,
                      display_type: str, encoder: str):
    s = _get_settings()
    s.beginGroup("usb")
    s.setValue("resolution", resolution)
    s.setValue("custom_w", custom_w)
    s.setValue("custom_h", custom_h)
    s.setValue("fps", fps)
    s.setValue("custom_fps", custom_fps)
    s.setValue("bitrate", bitrate)
    s.setValue("display_type", display_type)
    s.setValue("encoder", encoder)
    s.endGroup()
    s.sync()


def save_general_settings(*, minimize_to_tray: bool = None, enable_touch: bool = None):
    s = _get_settings()
    s.beginGroup("general")
    if minimize_to_tray is not None:
        s.setValue("minimize_to_tray", minimize_to_tray)
    if enable_touch is not None:
        s.setValue("enable_touch", enable_touch)
    s.endGroup()
    s.sync()




def load_wifi_settings() -> dict:
    s = _get_settings()
    s.beginGroup("wifi")
    display_type = s.value("display_type", "Extend")
    if display_type == "Extend Right":
        display_type = "Extend"
    encoder = s.value("encoder", "Software (CPU / x264enc)")
    if encoder in ("Auto-detect", "Auto-detect (Recommended)"):
        encoder = "Software (CPU / x264enc)"
    data = {
        "resolution":   s.value("resolution",   "2560x1600"),
        "custom_w":     s.value("custom_w",     ""),
        "custom_h":     s.value("custom_h",     ""),
        "fps":          s.value("fps",          "60"),
        "custom_fps":   s.value("custom_fps",   ""),
        "bitrate":      s.value("bitrate",      "8000"),
        "display_type": display_type,
        "encoder":      encoder,
        "stream_type":  s.value("stream_type",  "Speed"),
    }
    s.endGroup()
    return data


def load_usb_settings() -> dict:
    s = _get_settings()
    s.beginGroup("usb")
    display_type = s.value("display_type", "Extend")
    if display_type == "Extend Right":
        display_type = "Extend"
    encoder = s.value("encoder", "Software (CPU / x264enc)")
    if encoder in ("Auto-detect", "Auto-detect (Recommended)"):
        encoder = "Software (CPU / x264enc)"
    data = {
        "resolution":   s.value("resolution",   "2560x1600"),
        "custom_w":     s.value("custom_w",     ""),
        "custom_h":     s.value("custom_h",     ""),
        "fps":          s.value("fps",          "60"),
        "custom_fps":   s.value("custom_fps",   ""),
        "bitrate":      s.value("bitrate",      "8000"),
        "display_type": display_type,
        "encoder":      encoder,
    }
    s.endGroup()
    return data


def load_general_settings() -> dict:
    s = _get_settings()
    s.beginGroup("general")
    data = {
        "minimize_to_tray": s.value("minimize_to_tray", False, type=bool),
        "enable_touch": s.value("enable_touch", True, type=bool),
    }
    s.endGroup()
    return data
