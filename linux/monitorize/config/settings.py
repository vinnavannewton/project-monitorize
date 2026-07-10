"""
Monitorize GUI — Persistent settings stored in ~/.config/monitorize/settings.ini
"""

import os
import hashlib
import json
from PyQt6.QtCore import QSettings

from monitorize.config.validation import (
    credential_host_key,
    normalize_host,
    sanitize_bitrate,
    sanitize_decoder,
    sanitize_display_type,
    sanitize_encoder,
    sanitize_encoder_profile,
    sanitize_fps,
    sanitize_port,
    sanitize_resolution,
    sanitize_stream_type,
)

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "monitorize")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.ini")
MAX_PRESETS = 4
PRESET_VERSION = 1


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
    data["encoder_profile"] = sanitize_encoder_profile(
        data.get("encoder_profile", "Low Latency")
    )
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
                       display_type: str, encoder: str, encoder_profile: str,
                       stream_type: str, use_encryption: bool):
    values = locals()
    values["display_type"] = sanitize_display_type(display_type)
    values["encoder"] = sanitize_encoder(encoder)
    values["encoder_profile"] = sanitize_encoder_profile(encoder_profile)
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
                      display_type: str, encoder: str, encoder_profile: str):
    values = locals()
    values["display_type"] = sanitize_display_type(display_type)
    values["encoder"] = sanitize_encoder(encoder)
    values["encoder_profile"] = sanitize_encoder_profile(encoder_profile)
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
    "resolution": "1920x1080",
    "custom_w": "",
    "custom_h": "",
    "fps": "60",
    "custom_fps": "",
    "bitrate": "16000",
    "display_type": "Extend",
    "encoder": "Software (CPU / x264enc)",
    "encoder_profile": "Low Latency",
}


def load_wifi_settings() -> dict:
    return _normalize_stream_settings(_load_group(
        "wifi",
        {**STREAM_DEFAULTS, "stream_type": "Speed", "use_encryption": False},
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


def save_second_display_settings(*, resolution: str, fps: str, bitrate: str,
                                 encoder: str, encoder_profile: str):
    _save_group("second_display", {
        "resolution": resolution,
        "fps": str(sanitize_fps(fps)),
        "bitrate": str(sanitize_bitrate(bitrate)),
        "encoder": sanitize_encoder(encoder),
        "encoder_profile": sanitize_encoder_profile(encoder_profile),
    })


def load_second_display_settings() -> dict:
    data = _load_group("second_display", {
        "resolution": "1920x1080 (16:9)",
        "fps": "60",
        "bitrate": "8000",
        "encoder": "Software (CPU / x264enc)",
        "encoder_profile": "Low Latency",
    })
    data["fps"] = str(sanitize_fps(data["fps"]))
    data["bitrate"] = str(sanitize_bitrate(data["bitrate"]))
    data["encoder"] = sanitize_encoder(data["encoder"])
    data["encoder_profile"] = sanitize_encoder_profile(data["encoder_profile"])
    return data


def _normalize_preset(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("version") != PRESET_VERSION:
        return None
    name = str(raw.get("name", "")).strip()[:32]
    mode = raw.get("mode")
    primary = raw.get("primary")
    general = raw.get("general")
    third = raw.get("third", {})
    if not name or mode not in ("wifi", "usb"):
        return None
    if not isinstance(primary, dict) or not isinstance(general, dict):
        return None
    width, height = sanitize_resolution(primary.get("resolution", ""))
    preset = {
        "version": PRESET_VERSION,
        "name": name,
        "mode": mode,
        "primary": {
            "resolution": f"{width}x{height}",
            "fps": str(sanitize_fps(primary.get("fps", 60))),
            "bitrate": str(sanitize_bitrate(primary.get("bitrate", 8000))),
            "display_type": sanitize_display_type(
                primary.get("display_type", "Extend")
            ),
            "encoder": sanitize_encoder(primary.get("encoder", "")),
            "encoder_profile": sanitize_encoder_profile(
                primary.get("encoder_profile", "Low Latency")
            ),
        },
        "general": {
            "minimize_to_tray": bool(general.get("minimize_to_tray", False)),
            "enable_touch": bool(general.get("enable_touch", True)),
            "enable_stylus_features": bool(
                general.get("enable_stylus_features", False)
            ),
        },
        "third": {"enabled": bool(third.get("enabled", False))},
    }
    if mode == "wifi":
        wifi = raw.get("wifi", {})
        preset["wifi"] = {
            "stream_type": sanitize_stream_type(wifi.get("stream_type", "Speed")),
            "use_encryption": bool(wifi.get("use_encryption", True)),
        }
    if preset["third"]["enabled"]:
        width, height = sanitize_resolution(
            third.get("resolution", ""), (1920, 1080)
        )
        preset["third"].update({
            "resolution": f"{width}x{height}",
            "fps": str(sanitize_fps(third.get("fps", 60))),
            "bitrate": str(sanitize_bitrate(third.get("bitrate", 8000))),
            "encoder": sanitize_encoder(third.get("encoder", "")),
            "encoder_profile": sanitize_encoder_profile(
                third.get("encoder_profile", "Low Latency")
            ),
        })
    return preset


def load_presets() -> list[dict]:
    raw = _get_settings().value("presets/items", "[]")
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    presets = []
    for value in values:
        preset = _normalize_preset(value)
        if preset is not None:
            presets.append(preset)
        if len(presets) == MAX_PRESETS:
            break
    return presets


def save_presets(presets: list[dict]) -> None:
    normalized = []
    for value in presets:
        preset = _normalize_preset(value)
        if preset is not None:
            normalized.append(preset)
        if len(normalized) == MAX_PRESETS:
            break
    _save_group("presets", {"items": json.dumps(normalized, separators=(",", ":"))})


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


def _gnome_virtual_group(slot: str = "primary") -> str:
    return "gnome_virtual_primary"


def load_gnome_virtual_layout(slot: str = "primary") -> dict:
    data = _load_group(_gnome_virtual_group(slot), {"layout": ""})
    try:
        layout = json.loads(data["layout"]) if data["layout"] else None
    except (TypeError, ValueError, json.JSONDecodeError):
        layout = None
    if isinstance(layout, list):
        layout = {"version": 2, "topologies": {"primary": layout}}
    if not isinstance(layout, dict) or layout.get("version") != 2:
        layout = {"version": 2, "topologies": {}}
    topologies = layout.get("topologies")
    if not isinstance(topologies, dict):
        topologies = {}
    saved = topologies.get(slot)
    return {"logical_monitors": saved if isinstance(saved, list) else None}


def save_gnome_virtual_layout(slot: str, logical_monitors: list) -> None:
    data = _load_group(_gnome_virtual_group(), {"layout": ""})
    try:
        stored = json.loads(data["layout"]) if data["layout"] else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        stored = {}
    if isinstance(stored, list):
        stored = {"version": 2, "topologies": {"primary": stored}}
    if not isinstance(stored, dict) or stored.get("version") != 2:
        stored = {"version": 2, "topologies": {}}
    topologies = stored.setdefault("topologies", {})
    if not isinstance(topologies, dict):
        topologies = stored["topologies"] = {}
    topologies[slot] = logical_monitors
    _save_group(_gnome_virtual_group(), {
        "layout": json.dumps(stored, separators=(",", ":")),
    })


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




def load_recent_usb_devices() -> list[dict]:
    s = _get_settings()
    raw = s.value("recent/usb_devices", "[]")
    try:
        devices = json.loads(raw)
        return devices if isinstance(devices, list) else []
    except Exception:
        return []


def add_recent_usb_device(device: dict) -> None:
    s = _get_settings()
    devices = load_recent_usb_devices()
    devices = [d for d in devices if isinstance(d, dict) and d.get("serial") != device.get("serial")]
    devices.insert(0, device)
    devices = devices[:5]
    s.setValue("recent/usb_devices", json.dumps(devices))
    s.sync()
    os.chmod(CONFIG_FILE, 0o600)


def load_recent_wifi_devices() -> list[dict]:
    s = _get_settings()
    raw = s.value("recent/wifi_devices", "[]")
    try:
        devices = json.loads(raw)
        return devices if isinstance(devices, list) else []
    except Exception:
        return []


def add_recent_wifi_device(device: dict) -> None:
    s = _get_settings()
    devices = load_recent_wifi_devices()
    devices = [d for d in devices if isinstance(d, dict) and d.get("ip") != device.get("ip")]
    devices.insert(0, device)
    devices = devices[:5]
    s.setValue("recent/wifi_devices", json.dumps(devices))
    s.sync()
    os.chmod(CONFIG_FILE, 0o600)