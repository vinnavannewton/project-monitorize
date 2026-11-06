"""Persist editable Sunshine options without starting or restarting an engine."""

import math
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from monitorize.platform.sunshine_service import DEFAULT_SUNSHINE_CONFIG, get_sunshine_config_path


# Monitorize owns the launch fields, endpoint, and identity paths.
_MANAGED_OPTIONS = {
    "sunshine_name", "native_pen_touch", "stream_audio", "adapter_name",
    "output_name", "encoder", "hevc_mode", "av1_mode", "capture", "port",
    "origin_web_ui_allowed", "file_apps", "credentials_file", "log_path",
    "pkey", "cert", "file_state",
}
DEFAULT_OPTIONS = {key: value for key, value in DEFAULT_SUNSHINE_CONFIG.items()
                   if key not in _MANAGED_OPTIONS}
INSTANCES = ("1", "2")
_MAX_FILE_BYTES = 1024 * 1024
_LOG_LEVELS = {"verbose", "debug", "info", "warning", "error", "fatal", "none"}
_INTEGER_RANGES = {
    "min_log_level": (0, 6),
    "nvenc_preset": (1, 7),
    "nvenc_vbv_increase": (0, 400),
    "fec_percentage": (0, 100),
    "lan_encryption_mode": (0, 2),
    "wan_encryption_mode": (0, 2),
}
_DECIMAL_RANGES = {"minimum_fps_target": 1000, "key_repeat_frequency": float("inf")}
_BOOLEAN_VALUES = {
    "true", "yes", "enable", "enabled", "on", "1",
    "false", "no", "disable", "disabled", "off", "0",
}


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("The Sunshine configuration must be a regular file.")
        data = source.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError("The Sunshine configuration exceeds 1 MiB.")
    return data


def _option(line):
    key, _, value = line.partition("#")[0].partition("=")
    return key.strip(), value.strip()


def snapshot():
    result = {}
    for instance in INSTANCES:
        path = Path(get_sunshine_config_path(int(instance)))
        text = (_read(path) or b"").decode("utf-8")
        saved = dict(_option(line) for line in text.splitlines())
        result[instance] = {key: saved.get(key, default) for key, default in DEFAULT_OPTIONS.items()}
    return result


def validate_option(value, key, default, path):
    if type(value) is int and re.fullmatch(r"-?[0-9]+", default):
        value = str(value)
    if key in _DECIMAL_RANGES and type(value) in (int, float):
        value = str(value)
    if (type(value) is not str or len(value) > 4096 or "#" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{path} must be a single-line Sunshine value.")
    value = value.strip()
    if default in ("enabled", "disabled") and value.lower() not in _BOOLEAN_VALUES:
        raise ValueError(f"{path} must be enabled or disabled.")
    if key == "min_log_level" and value in _LOG_LEVELS:
        return value
    if key in _DECIMAL_RANGES:
        try:
            number = float(value)
        except ValueError:
            number = math.nan
        limit = _DECIMAL_RANGES[key]
        if not math.isfinite(number) or not 0 <= number <= limit:
            raise ValueError(f"{path} must be a finite, nonnegative number within Sunshine's range.")
    elif re.fullmatch(r"-?[0-9]+", default):
        low, high = _INTEGER_RANGES.get(key, (-2147483648, 2147483647))
        if not re.fullmatch(r"-?[0-9]{1,10}", value) or not low <= int(value) <= high:
            raise ValueError(f"{path} must be an integer from {low} to {high}.")
    return value


def _write(path, content):
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sunshine-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def publish(current, candidate):
    """Stage changes to disk and restore them if accepting the profile fails."""
    changed = []
    try:
        for instance in INSTANCES:
            updates = {key: value for key, value in candidate[instance].items()
                       if current[instance][key] != value}
            if not updates:
                continue
            path = Path(get_sunshine_config_path(int(instance)))
            previous = _read(path)
            lines = (previous or b"").decode("utf-8").splitlines()
            existing = dict(_option(line) for line in lines)
            if any(existing.get(key, DEFAULT_OPTIONS[key]) != current[instance][key]
                   for key in updates):
                raise ValueError(f"Sunshine instance {instance} changed while saving. Reload again.")
            retained = [line for line in lines if _option(line)[0] not in updates]
            retained.extend(f"{key} = {value}" for key, value in updates.items())
            written = ("\n".join(retained) + "\n").encode("utf-8")
            _write(path, written)
            changed.append((path, previous, written))
        yield
    except (OSError, ValueError):
        for path, previous, written in reversed(changed):
            if _read(path) == written:
                _write(path, previous)
        raise
