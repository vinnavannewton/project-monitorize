"""VKMS virtual display lifecycle provider using the standalone monitorize-vkms CLI."""

from __future__ import annotations

from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import select
import signal
import sys
import webbrowser

from monitorize.platform.monitorize_vkms_cli import (
    MonitorizeVkmsClient,
    MonitorizeVkmsError,
    VkmsCommandError,
)

log = logging.getLogger(__name__)

VKMS_RESOLUTIONS = (
    (4096, 2160),
    (2560, 1600),
    (2048, 1152),
    (1920, 1440),
    (1920, 1200),
    (1920, 1080),
    (1856, 1392),
    (1792, 1344),
    (1680, 1050),
    (1600, 1200),
    (1600, 900),
    (1440, 900),
    (1400, 1050),
    (1366, 768),
    (1360, 768),
    (1280, 1024),
    (1280, 960),
    (1280, 800),
    (1280, 768),
    (1280, 720),
    (1024, 768),
    (848, 480),
    (800, 600),
    (640, 480),
)
VKMS_SLOTS = ("primary",)
MONITORIZE_VKMS_INSTALL_URL = "https://github.com/vinnavannewton/monitorize-vkms"

_DRM_CONNECTOR_NAME = re.compile(r"^card\d+-.+")
_DRM_MODE_NAME = re.compile(r"^(\d+)x(\d+)$")


class VkmsError(MonitorizeVkmsError):
    """Base error for VKMS operations in Monitorize."""


class VkmsCustomEdidUnsupported(VkmsError):
    """The installed VKMS driver does not support custom EDID generation."""


class CustomEdidCapability(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CHECK_FAILED = "check_failed"


def custom_edid_capability_from_response(response: dict) -> CustomEdidCapability:
    """Validate a capability response dictionary."""
    value = str(response.get("capability") or "").lower()
    if value == CustomEdidCapability.SUPPORTED.value:
        return CustomEdidCapability.SUPPORTED
    if value == CustomEdidCapability.UNSUPPORTED.value:
        return CustomEdidCapability.UNSUPPORTED
    raise VkmsError("VKMS capability helper returned an invalid capability result.")


def check_custom_edid_support(client: MonitorizeVkmsClient | None = None) -> CustomEdidCapability:
    """Check whether the standalone monitorize-vkms backend is available and supported."""
    c = client or MonitorizeVkmsClient()
    cap = c.check_capability()
    if cap == "supported":
        return CustomEdidCapability.SUPPORTED
    if cap == "unsupported":
        return CustomEdidCapability.UNSUPPORTED
    return CustomEdidCapability.CHECK_FAILED


def open_monitorize_vkms_install_page() -> bool:
    """Open the monitorize-vkms installation page URL."""
    try:
        return webbrowser.open(MONITORIZE_VKMS_INSTALL_URL)
    except Exception:
        return False


def sanitize_vkms_resolution(width: int, height: int) -> tuple[int, int]:
    """Sanitize preset sizes if requested."""
    requested = (int(width), int(height))
    return requested if requested in VKMS_RESOLUTIONS else (1920, 1080)


def resolution_options(drm_root: Path | None = None) -> list[str]:
    """Return live normal modes, or common preset modes before a device exists."""
    modes: set[tuple[int, int]] = set()
    try:
        connectors = sorted((drm_root or Path("/sys/class/drm")).iterdir())
    except OSError:
        connectors = []

    for connector in connectors:
        if not _DRM_CONNECTOR_NAME.fullmatch(connector.name):
            continue
        try:
            device = (connector / "device").resolve()
            if "/faux/monitorize" not in str(device):
                continue
            raw_modes = (connector / "modes").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_mode in raw_modes:
            match = _DRM_MODE_NAME.fullmatch(raw_mode.strip())
            if match:
                modes.add((int(match.group(1)), int(match.group(2))))

    if not modes:
        modes.update(VKMS_RESOLUTIONS)
    ordered = sorted(modes, key=lambda mode: (mode[0] * mode[1], mode), reverse=True)
    return [*(f"{width}x{height}" for width, height in ordered), "Custom..."]


def run_vkms_headless(
    slot: str,
    width: int,
    height: int,
    fps: int | float,
    desktop: str = "",
    *,
    custom_mode: bool = False,
    client: MonitorizeVkmsClient | None = None,
) -> int:
    """Execute VKMS virtual display creation through the standalone monitorize-vkms CLI.

    This function owns the session lifetime:
    1. Creates the display via `monitorize-vkms create`
    2. Emits MONITORIZE_EVENT headless_ready
    3. Remains alive waiting for stdin EOF or termination signals
    4. Automatically removes the display via `monitorize-vkms remove` on exit.
    """
    if slot not in VKMS_SLOTS:
        print(f"[ERROR] Unsupported VKMS display slot: {slot}", flush=True)
        return 1

    vkms_client = client or MonitorizeVkmsClient()
    if not vkms_client.is_available():
        print(
            "[ERROR] VKMS Experimental requires the standalone monitorize-vkms package. "
            "Install it from https://github.com/vinnavannewton/monitorize-vkms.",
            flush=True,
        )
        return 1

    capture = None
    stopping = [False]
    created = [False]
    created_connector = [None]

    def cleanup(*_args):
        if stopping[0]:
            return
        stopping[0] = True
        if capture is not None:
            capture.close()
        if created[0]:
            conn_str = f" {created_connector[0]}" if created_connector[0] else ""
            print(f"[VKMS] Removing{conn_str} through monitorize-vkms", flush=True)
            res = vkms_client.remove_display(created_connector[0])
            if not res.get("success", True):
                print(
                    f"[ERROR] VKMS cleanup failed: {res.get('message', 'unknown error')}",
                    flush=True,
                )
            else:
                print(f"[VKMS] Virtual display{conn_str} removed", flush=True)

    def stop_from_signal(*_args):
        cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_from_signal)
    signal.signal(signal.SIGTERM, stop_from_signal)

    try:
        print(f"[VKMS] Using standalone monitorize-vkms backend", flush=True)
        print(f"[VKMS] Requesting display: {width}x{height}@{fps}", flush=True)

        result = vkms_client.create_display(width, height, fps)
        output_name = result["name"]
        created_connector[0] = output_name
        created[0] = True
        actual_width = result["width"]
        actual_height = result["height"]
        actual_fps = result["fps"]

        event = {
            "type": "headless_ready",
            "name": output_name,
            "width": actual_width,
            "height": actual_height,
            "fps": actual_fps,
            "backend": "Sunshine",
            "vkms": True,
        }
        if desktop.lower() == "gnome":
            from monitorize.platform.gnome_monitor_capture import GnomeMonitorCapture
            capture = GnomeMonitorCapture()
            event.update(capture.start(output_name))
        print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)
        print(
            f"[VKMS] {output_name} is active at {actual_width}x{actual_height}@{actual_fps:g}Hz.",
            flush=True,
        )

        while not stopping[0]:
            if capture is not None:
                capture.dispatch()
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    except VkmsCommandError as exc:
        if exc.error_type == "edid_error" or "custom edid" in str(exc).lower():
            print(
                "MONITORIZE_EVENT "
                + json.dumps(
                    {"type": "vkms_custom_edid_unsupported"}, separators=(",", ":")
                ),
                flush=True,
            )
        print(f"[ERROR] VKMS virtual display failed: {exc}", flush=True)
        return 1
    except MonitorizeVkmsError as exc:
        print(f"[ERROR] VKMS virtual display failed: {exc}", flush=True)
        return 1
    except Exception as exc:
        print(f"[ERROR] VKMS virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()
