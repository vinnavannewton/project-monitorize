"""Persistent settings for Monitorize's Sunshine display sessions."""

import json
import os

from PyQt6.QtCore import QSettings

from monitorize.config.validation import (
    DEFAULT_PRIMARY_RESOLUTION,
    DEFAULT_SECONDARY_RESOLUTION,
    sanitize_display_type,
    sanitize_fps,
    sanitize_resolution,
)


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "monitorize")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.ini")
MAX_PRESETS = 4
PRESET_VERSION = 2


def _get_settings() -> QSettings:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    settings = QSettings(CONFIG_FILE, QSettings.Format.IniFormat)
    if any(key.startswith("General/") for key in settings.allKeys()):
        minimize = settings.value("General/minimize_to_tray", False, type=bool)
        settings.remove("General")
        settings.setValue("general/minimize_to_tray", minimize)
        settings.sync()
    return settings


def _save_group(group: str, values: dict) -> None:
    settings = _get_settings()
    settings.beginGroup(group)
    for key, value in values.items():
        if value is not None:
            settings.setValue(key, value)
    settings.endGroup()
    settings.sync()
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def _load_group(group: str, defaults: dict, bool_keys=()) -> dict:
    settings = _get_settings()
    settings.beginGroup(group)
    values = {
        key: settings.value(key, default, type=bool)
        if key in bool_keys
        else settings.value(key, default)
        for key, default in defaults.items()
    }
    settings.endGroup()
    return values


def save_general_settings(*, minimize_to_tray: bool = False):
    _save_group("general", {"minimize_to_tray": bool(minimize_to_tray)})


def load_general_settings() -> dict:
    return _load_group(
        "general", {"minimize_to_tray": False}, ("minimize_to_tray",)
    )


DISPLAY_DEFAULTS = {
    "resolution": "1920x1080",
    "custom_w": "",
    "custom_h": "",
    "fps": "60",
    "custom_fps": "",
    "display_type": "Extend",
    "sunshine_encoder": "Auto",
    "sunshine_codec": "Auto",
    "sunshine_native_pen_touch": True,
    "enable_audio": False,
}


def _normalize_display_settings(data, fallback=DEFAULT_PRIMARY_RESOLUTION):
    data = dict(data)
    data["display_type"] = sanitize_display_type(data.get("display_type"))
    data["fps"] = str(sanitize_fps(data.get("fps")))
    data["custom_fps"] = (
        str(sanitize_fps(data["custom_fps"])) if data.get("custom_fps") else ""
    )
    if data.get("resolution") == "Custom...":
        width, height = sanitize_resolution(
            f"{data.get('custom_w', '')}x{data.get('custom_h', '')}", fallback
        )
        data["custom_w"], data["custom_h"] = str(width), str(height)
    else:
        width, height = sanitize_resolution(data.get("resolution"), fallback)
        data["resolution"] = f"{width}x{height}"
        data["custom_w"] = data["custom_h"] = ""
    data["sunshine_encoder"] = str(data.get("sunshine_encoder") or "Auto")
    data["sunshine_codec"] = str(data.get("sunshine_codec") or "Auto")
    data["sunshine_native_pen_touch"] = bool(
        data.get("sunshine_native_pen_touch", True)
    )
    data["enable_audio"] = bool(data.get("enable_audio", False))
    return data


def save_display_settings(
    *,
    resolution,
    custom_w="",
    custom_h="",
    fps="60",
    custom_fps="",
    display_type="Extend",
    sunshine_encoder="Auto",
    sunshine_codec="Auto",
    sunshine_native_pen_touch=True,
    enable_audio=False,
):
    values = _normalize_display_settings(locals())
    _save_group("display", values)


def load_display_settings() -> dict:
    settings = _get_settings()
    group = "display"
    if not any(key.startswith("display/") for key in settings.allKeys()):
        legacy = _load_group(
            "wifi",
            DISPLAY_DEFAULTS,
            ("sunshine_native_pen_touch", "enable_audio"),
        )
        values = _normalize_display_settings(legacy)
        _save_group("display", values)
        group = "display"
    return _normalize_display_settings(
        _load_group(
            group,
            DISPLAY_DEFAULTS,
            ("sunshine_native_pen_touch", "enable_audio"),
        )
    )


SECOND_DISPLAY_DEFAULTS = {
    **DISPLAY_DEFAULTS,
    "resolution": "1920x1080",
}


def save_second_display_settings(**values):
    normalized = _normalize_display_settings(values, DEFAULT_SECONDARY_RESOLUTION)
    normalized.pop("display_type", None)
    _save_group("second_display", normalized)


def load_second_display_settings() -> dict:
    values = _load_group(
        "second_display",
        SECOND_DISPLAY_DEFAULTS,
        ("sunshine_native_pen_touch", "enable_audio"),
    )
    return _normalize_display_settings(values, DEFAULT_SECONDARY_RESOLUTION)


def _normalize_session(raw: dict, fallback=DEFAULT_PRIMARY_RESOLUTION):
    if not isinstance(raw, dict):
        return None
    width, height = sanitize_resolution(raw.get("resolution", ""), fallback)
    return {
        "resolution": f"{width}x{height}",
        "fps": str(sanitize_fps(raw.get("fps", 60))),
        "display_type": sanitize_display_type(raw.get("display_type", "Extend")),
        "sunshine_encoder": str(raw.get("sunshine_encoder") or "Auto"),
        "sunshine_codec": str(raw.get("sunshine_codec") or "Auto"),
        "sunshine_native_pen_touch": bool(
            raw.get("sunshine_native_pen_touch", True)
        ),
        "enable_audio": bool(raw.get("enable_audio", False)),
    }


def _normalize_preset(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()[:32]
    if not name:
        return None

    if raw.get("version") == 1:
        if raw.get("mode") != "wifi":
            return None
        current = load_display_settings()
        primary_raw = dict(raw.get("primary") or {})
        primary_raw.update(
            sunshine_encoder=current["sunshine_encoder"],
            sunshine_codec=current["sunshine_codec"],
            sunshine_native_pen_touch=current["sunshine_native_pen_touch"],
        )
        old_second = raw.get("third") or {}
        second_raw = dict(old_second)
        second_raw.update(
            sunshine_encoder=current["sunshine_encoder"],
            sunshine_codec=current["sunshine_codec"],
            sunshine_native_pen_touch=current["sunshine_native_pen_touch"],
        )
    elif raw.get("version") == PRESET_VERSION:
        primary_raw = raw.get("primary") or {}
        second_raw = raw.get("second") or {}
    else:
        return None

    primary = _normalize_session(primary_raw)
    if primary is None:
        return None
    second = {"enabled": bool(second_raw.get("enabled", False))}
    if second["enabled"]:
        normalized = _normalize_session(second_raw, DEFAULT_SECONDARY_RESOLUTION)
        normalized.pop("display_type", None)
        second.update(normalized)
    return {
        "version": PRESET_VERSION,
        "name": name,
        "primary": primary,
        "second": second,
    }


def load_presets() -> list[dict]:
    raw = _get_settings().value("presets/items", "[]")
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    presets = []
    migrated = False
    for value in values:
        preset = _normalize_preset(value)
        if preset is not None:
            presets.append(preset)
            migrated = migrated or value.get("version") != PRESET_VERSION
        if len(presets) == MAX_PRESETS:
            break
    if migrated or len(presets) != len(values):
        save_presets(presets)
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


def _gnome_virtual_group(_slot: str = "primary") -> str:
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
    _save_group(
        _gnome_virtual_group(),
        {"layout": json.dumps(stored, separators=(",", ":"))},
    )
