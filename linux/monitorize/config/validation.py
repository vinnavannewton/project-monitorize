"""Shared validation helpers for Linux GUI/controller inputs."""

import re


DEFAULT_PRIMARY_RESOLUTION = (1920, 1080)
DEFAULT_SECONDARY_RESOLUTION = (1920, 1080)
DEFAULT_FPS = 60
DEFAULT_BITRATE = 8000

MIN_WIDTH = 320
MIN_HEIGHT = 240
MAX_WIDTH = 7680
MAX_HEIGHT = 4320
MIN_FPS = 24
MAX_FPS = 240
MIN_BITRATE = 250
MAX_BITRATE = 100000

VALID_DECODERS = {"Software", "Hardware"}
VALID_DISPLAY_TYPES = {"Extend", "Mirror"}
VALID_ENCODER_PROFILES = {"Low Latency", "Balanced", "Quality"}
VALID_FEC_MODES = {"Off", "RS-FEC 10%", "ULPFEC 10%"}
VALID_ENCODERS = {
    "NVIDIA NVENC (nvh264enc)",
    "Intel/AMD VA-API (vah264enc)",
    "Software (CPU / x264enc)",
}
VALID_VIDEO_CODECS = {"H.264 (AVC)", "H.265 (HEVC)", "h264", "h265", "H.264", "H.265"}


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


def sanitize_encoder_profile(value):
    return value if value in VALID_ENCODER_PROFILES else "Low Latency"


def sanitize_encoder(value):
    return value if value in VALID_ENCODERS else "Software (CPU / x264enc)"


def sanitize_fec_mode(value):
    return value if value in VALID_FEC_MODES else "Off"


def sanitize_video_codec(value):
    val_str = str(value or "").strip()
    return "H.265 (HEVC)" if val_str in {"H.265 (HEVC)", "h265", "H.265"} else "H.264 (AVC)"
