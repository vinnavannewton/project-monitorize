"""
Monitorize GUI — Persistent settings stored in ~/.config/monitorize/settings.ini
"""

import os
import hashlib
from PyQt6.QtCore import QSettings

from gui.validation import (
    credential_host_key,
    normalize_host,
    sanitize_bitrate,
    sanitize_decoder,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_fps,
    sanitize_port,
    sanitize_resolution,
    sanitize_stream_type,
)

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "monitorize")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.ini")


def _get_settings() -> QSettings:
    """Return a QSettings object backed by the INI file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    settings = QSettings(CONFIG_FILE, QSettings.Format.IniFormat)

    if any(key.startswith("General/") for key in settings.allKeys()):
        values = {
            key: settings.value(f"General/{key}", default, type=bool)
            for key, default in (
                ("minimize_to_tray", False),
                ("enable_touch", True),
                ("enable_stylus_features", False),
            )
        }
        settings.remove("General")
        settings.beginGroup("general")
        for key, value in values.items():
            settings.setValue(key, value)
        settings.endGroup()
        settings.sync()

    return settings


def _save_group(group: str, values: dict) -> None:
    s = _get_settings()
    s.beginGroup(group)
    for key, value in values.items():
        if value is not None:
            s.setValue(key, value)
    s.endGroup()
    s.sync()
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def _load_group(group: str, defaults: dict, bool_keys=()) -> dict:
    s = _get_settings()
    s.beginGroup(group)
    data = {
        key: s.value(key, default, type=bool) if key in bool_keys
        else s.value(key, default)
        for key, default in defaults.items()
    }
    s.endGroup()
    return data


def _normalize_stream_settings(data: dict) -> dict:
    if data["display_type"] == "Extend Right":
        data["display_type"] = "Extend"
    if data["encoder"] in ("Auto-detect", "Auto-detect (Recommended)"):
        data["encoder"] = "Software (CPU / x264enc)"
    data["display_type"] = sanitize_display_type(data["display_type"])
    data["encoder"] = sanitize_encoder(data["encoder"])
    data["stream_type"] = sanitize_stream_type(data.get("stream_type", "Speed"))
    data["fps"] = str(sanitize_fps(data["fps"]))
    data["custom_fps"] = (
        str(sanitize_fps(data["custom_fps"]))
        if data.get("custom_fps") else ""
    )
    data["bitrate"] = str(sanitize_bitrate(data["bitrate"]))
    if data["resolution"] == "Custom...":
        width, height = sanitize_resolution(
            f"{data.get('custom_w', '')}x{data.get('custom_h', '')}"
        )
        data["custom_w"] = str(width)
        data["custom_h"] = str(height)
    return data

def save_wifi_settings(*, resolution: str, custom_w: str, custom_h: str,
                       fps: str, custom_fps: str, bitrate: str,
                       display_type: str, encoder: str, stream_type: str,
                       use_encryption: bool):
    values = locals()
    values["display_type"] = sanitize_display_type(display_type)
    values["encoder"] = sanitize_encoder(encoder)
    values["stream_type"] = sanitize_stream_type(stream_type)
    values["fps"] = str(sanitize_fps(fps))
    values["custom_fps"] = str(sanitize_fps(custom_fps)) if custom_fps else ""
    values["bitrate"] = str(sanitize_bitrate(bitrate))
    if resolution == "Custom...":
        width, height = sanitize_resolution(f"{custom_w}x{custom_h}")
        values["custom_w"] = str(width)
        values["custom_h"] = str(height)
    else:
        values["custom_w"] = ""
        values["custom_h"] = ""
    _save_group("wifi", values)


def save_usb_settings(*, resolution: str, custom_w: str, custom_h: str,
                      fps: str, custom_fps: str, bitrate: str,
                      display_type: str, encoder: str):
    values = locals()
    values["display_type"] = sanitize_display_type(display_type)
    values["encoder"] = sanitize_encoder(encoder)
    values["fps"] = str(sanitize_fps(fps))
    values["custom_fps"] = str(sanitize_fps(custom_fps)) if custom_fps else ""
    values["bitrate"] = str(sanitize_bitrate(bitrate))
    if resolution == "Custom...":
        width, height = sanitize_resolution(f"{custom_w}x{custom_h}")
        values["custom_w"] = str(width)
        values["custom_h"] = str(height)
    else:
        values["custom_w"] = ""
        values["custom_h"] = ""
    _save_group("usb", values)


def save_general_settings(*, minimize_to_tray: bool = None, enable_touch: bool = None,
                          enable_stylus_features: bool = None):
    _save_group("general", locals())
    s = _get_settings()
    s.beginGroup("general")
    s.remove("stylus_only")
    s.endGroup()
    s.sync()




STREAM_DEFAULTS = {
    "resolution": "2560x1600",
    "custom_w": "",
    "custom_h": "",
    "fps": "60",
    "custom_fps": "",
    "bitrate": "8000",
    "display_type": "Extend",
    "encoder": "Software (CPU / x264enc)",
}


def load_wifi_settings() -> dict:
    return _normalize_stream_settings(_load_group(
        "wifi",
        {**STREAM_DEFAULTS, "stream_type": "Speed", "use_encryption": True},
        ("use_encryption",),
    ))


def load_usb_settings() -> dict:
    return _normalize_stream_settings(_load_group("usb", STREAM_DEFAULTS))


def load_general_settings() -> dict:
    data = _load_group(
        "general",
        {
            "minimize_to_tray": False,
            "enable_touch": True,
            "enable_stylus_features": False,
            "stylus_only": False,
        },
        ("minimize_to_tray", "enable_touch", "enable_stylus_features", "stylus_only"),
    )
    enable_stylus = data.pop("enable_stylus_features")
    enable_touch = data.pop("enable_touch")
    if enable_stylus and data.pop("stylus_only"):
        enable_touch = False
    data.update(enable_touch=enable_touch, enable_stylus_features=enable_stylus)
    return data


def save_second_display_settings(*, resolution: str, fps: str, bitrate: str, encoder: str):
    _save_group("second_display", {
        "resolution": resolution,
        "fps": str(sanitize_fps(fps)),
        "bitrate": str(sanitize_bitrate(bitrate)),
        "encoder": sanitize_encoder(encoder),
    })


def load_second_display_settings() -> dict:
    data = _load_group("second_display", {
        "resolution": "1920x1080 (16:9)",
        "fps": "60",
        "bitrate": "8000",
        "encoder": "Software (CPU / x264enc)",
    })
    data["fps"] = str(sanitize_fps(data["fps"]))
    data["bitrate"] = str(sanitize_bitrate(data["bitrate"]))
    data["encoder"] = sanitize_encoder(data["encoder"])
    return data


def save_receiver_settings(*, ip: str, port: str, use_encryption: bool = True,
                           decoder: str = "Software"):
    _save_group("receiver", {
        "manual_ip": normalize_host(ip),
        "manual_port": str(sanitize_port(port)),
        "use_encryption": use_encryption,
        "decoder": sanitize_decoder(decoder),
    })


def load_receiver_settings() -> dict:
    data = _load_group("receiver", {
        "manual_ip": "",
        "manual_port": "7110",
        "use_encryption": True,
        "decoder": "Software",
    }, ("use_encryption",))
    data["manual_ip"] = normalize_host(data["manual_ip"])
    data["manual_port"] = str(sanitize_port(data["manual_port"]))
    data["decoder"] = sanitize_decoder(data["decoder"])
    return data


def load_sway_output() -> str:
    return _get_settings().value("sway/output", "")


def save_sway_output(output: str) -> None:
    _save_group("sway", {"output": output})


def load_receiver_credentials(host: str) -> tuple[str, str]:
    s = _get_settings()
    key = hashlib.sha256(credential_host_key(host).encode()).hexdigest()
    return (
        s.value(f"receiver_trust/{key}/fingerprint", ""),
        s.value(f"receiver_trust/{key}/token", ""),
    )


def save_receiver_credentials(host: str, fingerprint: str, token: str) -> None:
    s = _get_settings()
    key = hashlib.sha256(credential_host_key(host).encode()).hexdigest()
    s.setValue(f"receiver_trust/{key}/fingerprint", str(fingerprint or "").strip())
    s.setValue(f"receiver_trust/{key}/token", str(token or "").strip())
    s.sync()
    os.chmod(CONFIG_FILE, 0o600)


def clear_receiver_credentials(host: str) -> None:
    s = _get_settings()
    key = hashlib.sha256(credential_host_key(host).encode()).hexdigest()
    s.remove(f"receiver_trust/{key}")
    s.sync()
