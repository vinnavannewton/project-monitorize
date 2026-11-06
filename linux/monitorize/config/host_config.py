"""Editable host profile backed by the application's accepted settings."""

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import QSettings

from monitorize.config import autostart, settings, sunshine_options, validation
from monitorize.desktop.configuration_state import ConfigurationState
from monitorize.remote_login.settings import read_settings, validate_idle_timeout


_SCHEMA_VERSION = 1
_MAX_CONFIG_BYTES = 256 * 1024
_EDITOR_BASE = "host_config/editor_base"
_CHOICES = {
    "streaming_backend": ("sunshine", "none"),
    "display_behavior": ("extend", "primary", "virtual_only"),
    "theme_mode": ("system", "light", "dark"),
    "display_type": ("Extend", "Mirror"),
    "virtual_display_creator": ("native", "vkms"),
    "sunshine_encoder": ("Auto", "NVIDIA", "VA-API", "Vulkan", "Software"),
    "sunshine_codec": ("Auto", "H.264", "HEVC", "AV1"),
    "sunshine_capture": settings.CAPTURE_MODES,
}


def config_path() -> Path:
    return Path(settings.CONFIG_DIR) / "monitorize.json"


@contextmanager
def _profile_lock():
    directory = config_path().parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / ".host-config.lock",
                 os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _parse_profile(text):
    def invalid_constant(token):
        raise ValueError(f"Invalid JSON value {token!r}.")

    try:
        return json.loads(text.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("The profile must be a bounded UTF-8 JSON document.") from exc


def _read_profile():
    fd = os.open(config_path(), os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as profile:
        if not stat.S_ISREG(os.fstat(profile.fileno()).st_mode):
            raise ValueError("monitorize.json must be a regular file.")
        data = profile.read(_MAX_CONFIG_BYTES + 1)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("The profile exceeds 256 KiB.")
    return _parse_profile(data)


def _write_profile(path, document):
    fd, temporary = tempfile.mkstemp(prefix=".monitorize-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(document, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _revision(document):
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _boot_state():
    try:
        enrolled = read_settings()
    except ValueError:
        return {"enabled": False, "create_login_virtual_display": False}
    if enrolled.uid != os.getuid():
        return {"enabled": False, "create_login_virtual_display": False}
    return {"enabled": enrolled.enabled,
            "create_login_virtual_display": enrolled.create_login_virtual_display}


def _snapshot():
    general = settings.load_general_settings()
    # First-run bookkeeping is changed by the authorization flow.
    general.pop("system_setup_decided")
    general["display_idle_timeout_seconds"] = settings.load_display_idle_timeout()
    second = settings.load_second_display_settings()
    second.pop("display_type", None)
    second.pop("virtual_display_creator", None)
    return {
        "schema_version": _SCHEMA_VERSION,
        "general": general,
        "primary": settings.load_display_settings(),
        "second": second,
        "presets": settings.load_presets(),
        "sunshine": sunshine_options.snapshot(),
        "startup": {"desktop": autostart.is_enabled()},
        "boot": _boot_state(),
    }


def _editor_base(store):
    value = store.value(_EDITOR_BASE)
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION else None


def _publish(store, values):
    previous = {key: store.value(key) for key in values}
    for key, value in values.items():
        store.setValue(key, value)
    store.sync()
    if store.status() == QSettings.Status.NoError:
        return
    for key, value in previous.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    raise OSError("Could not save Monitorize settings.")


def initialize() -> Path:
    """Create a profile, or refresh its contents if there is no pending edit."""
    path = config_path()
    with _profile_lock():
        current = _snapshot()
        store = settings._get_settings()
        base = _editor_base(store)
        if path.exists():
            try:
                document = _read_profile()
            except (UnicodeError, ValueError):
                return path  # Preserve the editor's work even if malformed.
            if base is None or document != base or current == base:
                return path
        _write_profile(path, current)
        _publish(store, {_EDITOR_BASE: json.dumps(current)})
    return path


def saved_revision() -> str:
    """Identify the accepted settings rather than the editable file."""
    with _profile_lock():
        return _revision(_snapshot())


def _streaming_revision(document):
    options = {key: document[key] for key in ("primary", "second", "sunshine")}
    options.update({key: document["general"][key]
                    for key in ("display_behavior", "streaming_backend")})
    return _revision(options)


def streaming_revision() -> str:
    """Identify accepted launch settings separately from UI-only preferences."""
    with _profile_lock():
        return _streaming_revision(_snapshot())


def sunshine_revision() -> str:
    """Identify the offline options read by the desktop Sunshine engines."""
    with _profile_lock():
        return _revision(sunshine_options.snapshot())


def _section(value, expected, path):
    if type(value) is not dict:
        raise ValueError(f"{path} must be an object.")
    unexpected = set(value) - set(expected)
    missing = set(expected) - set(value)
    if unexpected:
        raise ValueError(f"{path}.{min(unexpected)} is unknown.")
    if missing:
        raise ValueError(f"{path}.{min(missing)} is required.")


def _scalar(value, expected, key, path):
    if key in ("fps", "custom_fps", "custom_w", "custom_h") and type(value) is int:
        value = str(value)
    if type(value) is not type(expected):
        raise ValueError(f"{path} must be {type(expected).__name__}.")
    if isinstance(value, str) and (len(value) > 256 or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{path} must be a single line of up to 256 characters.")
    if key in _CHOICES and value not in _CHOICES[key]:
        raise ValueError(f"{path} must be one of {', '.join(_CHOICES[key])}.")
    if key == "sunshine_gpu" and value and not settings._normalize_gpu_id(value):
        raise ValueError(f"{path} must be a PCI address such as 0000:03:00.0.")
    return value


def _validate_group(value, expected, path):
    _section(value, expected, path)
    return {key: _scalar(value[key], default, key, f"{path}.{key}")
            for key, default in expected.items()}


def _integer(value, low, high, path):
    if not re.fullmatch(r"[0-9]{1,5}", value) or not low <= int(value) <= high:
        raise ValueError(f"{path} must be an integer from {low} to {high}.")
    return int(value)


def _validate_display(value, expected, path):
    result = _validate_group(value, expected, path)
    resolution = result["resolution"]
    if resolution == "Custom..." and "custom_w" in result:
        width, height = result["custom_w"], result["custom_h"]
    else:
        match = re.fullmatch(r"([0-9]{1,5})[xX]([0-9]{1,5})", resolution)
        if not match:
            raise ValueError(f"{path}.resolution must be WIDTHxHEIGHT.")
        width, height = match.groups()
    width = _integer(width, validation.MIN_WIDTH, validation.MAX_WIDTH, f"{path}.width")
    height = _integer(height, validation.MIN_HEIGHT, validation.MAX_HEIGHT, f"{path}.height")
    if width % 2 or height % 2:
        raise ValueError(f"{path}.resolution must use even dimensions.")
    if resolution == "Custom...":
        result["custom_w"], result["custom_h"] = str(width), str(height)
    else:
        result["resolution"] = f"{width}x{height}"
        if "custom_w" in result:
            result["custom_w"] = result["custom_h"] = ""
    if result.get("custom_fps"):
        result["custom_fps"] = str(_integer(
            result["custom_fps"], validation.MIN_FPS, validation.MAX_FPS, f"{path}.custom_fps"))
    fps = result["fps"]
    if fps == "Custom..." and "custom_fps" in result:
        fps = result["custom_fps"]
    result["fps"] = str(_integer(fps, validation.MIN_FPS, validation.MAX_FPS, f"{path}.fps"))
    result["sunshine_gpu"] = settings._normalize_gpu_id(result["sunshine_gpu"])
    return result


def _validate_presets(value):
    if not isinstance(value, list) or len(value) > settings.MAX_PRESETS:
        raise ValueError(f"presets must contain up to {settings.MAX_PRESETS} entries.")
    primary_defaults = settings._normalize_session(settings.DISPLAY_DEFAULTS)
    second_defaults = {**primary_defaults, "enabled": True}
    second_defaults.pop("display_type")
    second_defaults.pop("virtual_display_creator")
    result = []
    for index, preset in enumerate(value):
        path = f"presets[{index}]"
        _section(preset, ("version", "name", "display_behavior", "primary", "second"), path)
        if type(preset["version"]) is not int or preset["version"] != settings.PRESET_VERSION:
            raise ValueError(f"{path}.version must be {settings.PRESET_VERSION}.")
        name = _scalar(preset["name"], "", "name", f"{path}.name").strip()
        if not 1 <= len(name) <= 32:
            raise ValueError(f"{path}.name must contain 1 to 32 characters.")
        primary = _validate_display(preset["primary"], primary_defaults, f"{path}.primary")
        if preset["second"] == {"enabled": False} and type(preset["second"]["enabled"]) is bool:
            second = {"enabled": False}
        else:
            second = _validate_display(preset["second"], second_defaults, f"{path}.second")
            if not second["enabled"]:
                raise ValueError(f"{path}.second must only contain enabled when disabled.")
        behavior = _scalar(preset["display_behavior"], "", "display_behavior", f"{path}.display_behavior")
        result.append(settings._normalize_preset(dict(
            version=settings.PRESET_VERSION, name=name, display_behavior=behavior,
            primary=primary, second=second,
        )))
    return result


def _validate_sunshine(value, reference):
    _section(value, sunshine_options.INSTANCES, "sunshine")
    result = {}
    for instance in sunshine_options.INSTANCES:
        path = f"sunshine.{instance}"
        options = value[instance]
        _section(options, sunshine_options.DEFAULT_OPTIONS, path)
        result[instance] = {
            key: (options[key] if options[key] == reference[instance][key] else
                  sunshine_options.validate_option(options[key], key, default, f"{path}.{key}"))
            for key, default in sunshine_options.DEFAULT_OPTIONS.items()
        }
    return result


def _validate(document, current):
    _section(document, current, "config")
    if type(document["schema_version"]) is not int or document["schema_version"] != _SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {_SCHEMA_VERSION}.")
    general = _validate_group(document["general"], current["general"], "general")
    validate_idle_timeout(general["display_idle_timeout_seconds"])
    return {
        "schema_version": _SCHEMA_VERSION,
        "general": general,
        "primary": _validate_display(document["primary"], current["primary"], "primary"),
        "second": _validate_display(document["second"], current["second"], "second"),
        "presets": _validate_presets(document["presets"]),
        "sunshine": _validate_sunshine(document["sunshine"], current["sunshine"]),
        "startup": _validate_group(document["startup"], current["startup"], "startup"),
        "boot": _validate_group(document["boot"], current["boot"], "boot"),
    }


def _merge(base, current, edited, path="config"):
    if base == current or edited == current:
        return edited
    if base == edited:
        return current
    if isinstance(base, dict) and isinstance(current, dict) and isinstance(edited, dict):
        return {key: _merge(base.get(key), current[key], value, f"{path}.{key}")
                for key, value in edited.items()}
    raise ValueError(f"{path} also changed in Monitorize. Resolve the conflict in the JSON file and reload.")


def _streaming_bundle(document, **context):
    primary = settings._normalize_display_settings(document["primary"])
    second = settings._normalize_display_settings(document["second"])
    # The second output uses the first output's creator and is always Extend.
    state = ConfigurationState(primary, second, document["general"])
    return state.candidate(**context)


def reload_config(*, restart=False, desktop="", remote_login=None) -> dict:
    """Accept an edited profile. The session owner handles any live restart."""
    del restart  # Keep the same interface for stopped and active sessions.
    with _profile_lock():
        current = _snapshot()
        store = settings._get_settings()
        try:
            document = _read_profile()
            edited = _validate(document, current)
        except FileNotFoundError:
            raise ValueError("No monitorize.json exists. Run monitorize setup first.") from None
        base = _editor_base(store)
        candidate = _merge(_validate(base, current), current, edited) if base is not None else edited
        if candidate["boot"] != current["boot"]:
            raise ValueError("Use the authorized boot commands to change boot settings.")
        if (current["boot"]["enabled"] and candidate["general"]["display_idle_timeout_seconds"]
                != current["general"]["display_idle_timeout_seconds"]):
            raise ValueError("Use the authorized boot timeout command to change the disconnect timer.")
        if remote_login is None:
            remote_login = current["boot"]["enabled"]
        if remote_login and candidate["sunshine"] != current["sunshine"]:
            raise ValueError("Advanced Sunshine options are available for desktop-managed hosting.")
        bundle = _streaming_bundle(candidate, desktop=desktop, remote_login=remote_login)
        groups = {
            "general": candidate["general"],
            "display": bundle["primary"],
            "second_display": {key: value for key, value in bundle["second"].items()
                               if key not in ("display_type", "virtual_display_creator")},
            "presets": {"items": json.dumps(candidate["presets"], separators=(",", ":"))},
        }
        # Write only intended changes so QSettings can merge unrelated keys
        # saved concurrently by the GUI or a separate settings process.
        previous = {
            "general": current["general"],
            "display": current["primary"],
            "second_display": current["second"],
            "presets": {"items": json.dumps(current["presets"], separators=(",", ":"))},
        }
        values = {f"{group}/{key}": value for group, entries in groups.items()
                  for key, value in entries.items() if value != previous[group][key]}
        values[_EDITOR_BASE] = json.dumps(document)
        startup_changed = candidate["startup"]["desktop"] != current["startup"]["desktop"]
        if startup_changed:
            error = autostart.set_enabled(candidate["startup"]["desktop"])
            if error:
                raise OSError(error)
        try:
            with sunshine_options.publish(current["sunshine"], candidate["sunshine"]):
                _publish(store, values)
        except (OSError, ValueError):
            if startup_changed:
                rollback_error = autostart.set_enabled(current["startup"]["desktop"])
                if rollback_error:
                    raise OSError(f"Settings save failed. {rollback_error}") from None
            raise
        accepted = _snapshot()
        running_revision = _streaming_revision(accepted)
        return {"saved_revision": _revision(accepted), "streaming_revision": running_revision,
                "sunshine_revision": _revision(accepted["sunshine"]),
                "sunshine_changed": accepted["sunshine"] != current["sunshine"],
                "streaming_changed": running_revision != _streaming_revision(current),
                "bundle": bundle, "changed": accepted != current}
