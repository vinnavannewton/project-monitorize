"""Shared validation helpers for Linux GUI/controller inputs."""

import re


DEFAULT_PRIMARY_RESOLUTION = (1920, 1200)
DEFAULT_SECONDARY_RESOLUTION = (1920, 1080)
DEFAULT_FPS = 60
DEFAULT_BITRATE = 8000

MIN_WIDTH = 320
MIN_HEIGHT = 240
MAX_WIDTH = 7680
MAX_HEIGHT = 4320
MIN_FPS = 24
MAX_FPS = 240
MIN_BITRATE = 500
MAX_BITRATE = 100000

VALID_DECODERS = {"Software", "Hardware"}
VALID_DISPLAY_TYPES = {"Extend", "Mirror"}
VALID_STREAM_TYPES = {"Speed", "Stability"}
VALID_ENCODERS = {
    "NVIDIA NVENC (nvh264enc)",
    "Intel/AMD VA-API (vah264enc)",
    "Software (CPU / x264enc)",
}


def clamp_int(value, default, minimum, maximum):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def even_dimension(value, minimum, maximum):
    value = max(minimum, min(maximum, value))
    if value % 2:
        value -= 1
    return max(minimum, value)


def sanitize_resolution(value, fallback=DEFAULT_PRIMARY_RESOLUTION):
    parts = str(value or "").strip().split()
    if not parts:
        return fallback
    text = parts[0]
    match = re.fullmatch(r"(\d+)[xX](\d+)", text)
    if not match:
        return fallback
    width = even_dimension(int(match.group(1)), MIN_WIDTH, MAX_WIDTH)
    height = even_dimension(int(match.group(2)), MIN_HEIGHT, MAX_HEIGHT)
    return width, height


def sanitize_fps(value, default=DEFAULT_FPS):
    return clamp_int(value, default, MIN_FPS, MAX_FPS)


def sanitize_bitrate(value, default=DEFAULT_BITRATE):
    return clamp_int(value, default, MIN_BITRATE, MAX_BITRATE)


def normalize_host(host):
    return str(host or "").strip()


def credential_host_key(host):
    return normalize_host(host).lower()


def valid_host(host):
    return bool(normalize_host(host))


def sanitize_port(port, default=7110, minimum=1, maximum=65535):
    return clamp_int(port, default, minimum, maximum)


def valid_port(port, minimum=1, maximum=65535):
    try:
        number = int(str(port).strip())
    except (TypeError, ValueError):
        return False
    return minimum <= number <= maximum


def sanitize_decoder(value):
    return value if value in VALID_DECODERS else "Software"


def sanitize_display_type(value):
    return value if value in VALID_DISPLAY_TYPES else "Extend"


def sanitize_stream_type(value):
    return value if value in VALID_STREAM_TYPES else "Speed"


def sanitize_encoder(value):
    return value if value in VALID_ENCODERS else "Software (CPU / x264enc)"
