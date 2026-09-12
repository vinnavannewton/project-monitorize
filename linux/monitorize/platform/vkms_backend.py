"""Source-only lifecycle for one mainline VKMS configfs display."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import time


VKMS_HELPER = Path("/usr/libexec/monitorize/monitorize-source-vkms-helper")
OUTPUT_TIMEOUT = 10.0
POLL_INTERVAL = 0.1
APPROXIMATE_REFRESH = 60.0





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


class VkmsError(RuntimeError):
    pass


def resolution_options() -> list[str]:
    return [f"{width}x{height}" for width, height in VKMS_RESOLUTIONS]


def sanitize_vkms_resolution(width: int, height: int) -> tuple[int, int]:
    requested = (int(width), int(height))
    return requested if requested in VKMS_RESOLUTIONS else (1920, 1080)


def _helper_response(operation: str, timeout: float = 60.0) -> dict:
    if os.path.isfile("/.flatpak-info"):
        raise VkmsError("VKMS display creation is available only in the native source installation.")
    if operation not in ("create", "destroy", "status"):
        raise ValueError(f"Unsupported VKMS helper operation: {operation}")
    if not VKMS_HELPER.is_file() or not os.access(VKMS_HELPER, os.X_OK):
        raise VkmsError("The VKMS helper is not installed. Re-run the Monitorize source installer.")
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise VkmsError("Polkit (pkexec) is required to create a VKMS virtual display.")
    try:
        result = subprocess.run(
            [pkexec, str(VKMS_HELPER), operation],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VkmsError(f"VKMS {operation} timed out while waiting for administrator authorization.") from exc
    except OSError as exc:
        raise VkmsError(f"Could not start the VKMS helper: {exc}") from exc

    response = None
    for line in reversed(result.stdout.splitlines()):
        try:
            response = json.loads(line)
            break
        except (TypeError, json.JSONDecodeError):
            continue
    if result.returncode or not isinstance(response, dict) or not response.get("success"):
        detail = ""
        if isinstance(response, dict):
            detail = str(response.get("message") or "")
        detail = detail or result.stderr.strip()
        if result.returncode in (126, 127) or "not authorized" in detail.lower():
            detail = "Administrator authorization is required to create a VKMS virtual display."
        raise VkmsError(detail or f"VKMS {operation} failed.")
    for message in response.get("logs", []):
        print(f"[VKMS] {message}", flush=True)
    return response


def _kde_outputs() -> dict[str, dict]:
    from monitorize.platform.kde_virtual_monitor import kde_outputs

    result = {}
    for output in kde_outputs():
        name = str(output.get("name") or "")
        if name and output.get("connected", True) and output.get("enabled", True):
            result[name] = output
    return result


def _gnome_outputs() -> dict[str, dict]:
    from monitorize.platform import gnome_virtual_monitor

    state = gnome_virtual_monitor._mutter_state()
    modes = gnome_virtual_monitor.active_output_modes(state)
    return {name: dict(mode) for name, mode in modes.items()}


def _wlroots_outputs(desktop: str) -> dict[str, dict]:
    from monitorize.platform.display_controller import DisplayController

    controller = DisplayController(desktop)
    return {
        name: dict(mode)
        for name, mode in controller.active_output_modes().items()
    }


def active_compositor_outputs(desktop: str) -> dict[str, dict]:
    desktop = str(desktop or "").lower()
    if desktop == "kde":
        return _kde_outputs()
    if desktop == "gnome":
        return _gnome_outputs()
    if desktop in ("hyprland", "sway"):
        return _wlroots_outputs(desktop)
    raise VkmsError(
        "VKMS output discovery currently requires KDE, GNOME, Hyprland, or Sway."
    )


def _wait_for_inventory_settle(desktop: str, timeout: float = 3.0) -> dict[str, dict]:
    deadline = time.monotonic() + timeout
    last_names = None
    stable_reads = 0
    latest = {}
    while time.monotonic() < deadline:
        latest = active_compositor_outputs(desktop)
        names = frozenset(latest)
        stable_reads = stable_reads + 1 if names == last_names else 1
        if latest and stable_reads >= 2:
            return latest
        last_names = names
        time.sleep(POLL_INTERVAL)
    return latest


def _wait_for_removed_stale_output(desktop: str, original: set[str], timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while original and time.monotonic() < deadline:
        current = set(active_compositor_outputs(desktop))
        if not original.issubset(current):
            return
        time.sleep(POLL_INTERVAL)


def _wait_for_new_output(desktop: str, before: set[str], timeout: float = OUTPUT_TIMEOUT):
    deadline = time.monotonic() + timeout
    last_added: list[str] = []
    while time.monotonic() < deadline:
        outputs = active_compositor_outputs(desktop)
        added = sorted(set(outputs) - before)
        last_added = added
        if len(added) == 1:
            return added[0], outputs[added[0]]
        if len(added) > 1:
            raise VkmsError(
                "The compositor exposed multiple outputs during VKMS creation; "
                f"Monitorize will not guess between {', '.join(added)}."
            )
        time.sleep(POLL_INTERVAL)
    detail = f" Last candidates: {', '.join(last_added)}." if last_added else ""
    raise VkmsError(
        f"The compositor did not detect the new VKMS output within {timeout:g} seconds.{detail}"
    )


def _kde_available_modes(output_name: str) -> list[dict]:
    output = _kde_outputs().get(output_name) or {}
    modes = []
    for mode in output.get("modes", []):
        size = mode.get("size") or {}
        width, height = int(size.get("width") or 0), int(size.get("height") or 0)
        refresh = float(mode.get("refreshRate") or 0)
        if width > 0 and height > 0 and refresh > 0:
            modes.append({"id": str(mode.get("id") or ""), "width": width,
                          "height": height, "refresh_rate": refresh})
    return modes


def _gnome_available_modes(output_name: str) -> list[dict]:
    from monitorize.platform import gnome_virtual_monitor

    state = gnome_virtual_monitor._mutter_state()
    for monitor in state[1]:
        if gnome_virtual_monitor._connector_name(monitor) != output_name:
            continue
        result = []
        for mode in monitor[1]:
            try:
                result.append({"id": str(mode[0]), "width": int(mode[1]),
                               "height": int(mode[2]), "refresh_rate": float(mode[3])})
            except (TypeError, ValueError, IndexError):
                continue
        return result
    return []


_WL_MODE = re.compile(r"^(\d+)x(\d+)@(\d+(?:\.\d+)?)Hz$")


def _wlroots_available_modes(desktop: str, output_name: str) -> list[dict]:
    from monitorize.platform.display_controller import DisplayController

    controller = DisplayController(desktop)
    raw_modes = []
    if desktop == "hyprland":
        output = next(
            (item for item in (controller._monitor_json() or []) if item.get("name") == output_name),
            {},
        )
        raw_modes = output.get("availableModes") or []
        result = []
        for value in raw_modes:
            match = _WL_MODE.fullmatch(str(value))
            if match:
                result.append({"id": str(value), "width": int(match.group(1)),
                               "height": int(match.group(2)), "refresh_rate": float(match.group(3))})
        return result
    output = next(
        (item for item in controller.sway_outputs() if item.get("name") == output_name),
        {},
    )
    result = []
    for mode in output.get("modes") or []:
        try:
            result.append({"id": "", "width": int(mode["width"]),
                           "height": int(mode["height"]),
                           "refresh_rate": float(mode["refresh"]) / 1000})
        except (KeyError, TypeError, ValueError):
            continue
    return result


def available_modes(desktop: str, output_name: str) -> list[dict]:
    if desktop == "kde":
        return _kde_available_modes(output_name)
    if desktop == "gnome":
        return _gnome_available_modes(output_name)
    return _wlroots_available_modes(desktop, output_name)


def _mode_summary(modes: list[dict]) -> str:
    unique = sorted({(mode["width"], mode["height"]) for mode in modes}, reverse=True)
    return ", ".join(f"{width}x{height}" for width, height in unique) or "none"


def configure_compositor_output(desktop: str, output_name: str, width: int, height: int):
    modes = available_modes(desktop, output_name)
    print(f"[VKMS] Available modes for {output_name}: {_mode_summary(modes)}", flush=True)
    if desktop == "kde":
        from monitorize.platform.kde_virtual_monitor import configure_existing_output_mode

        return configure_existing_output_mode(output_name, width, height, APPROXIMATE_REFRESH)
    if desktop == "gnome":
        from monitorize.platform.gnome_virtual_monitor import configure_existing_output_mode

        return configure_existing_output_mode(output_name, width, height, APPROXIMATE_REFRESH)
    from monitorize.platform.display_controller import DisplayController

    return DisplayController(desktop).configure_existing_output_mode(
        output_name, width, height, APPROXIMATE_REFRESH
    )


def run_vkms_headless(slot: str, width: int, height: int, _fps: int, desktop: str) -> int:
    if slot != "primary":
        print("[ERROR] VKMS v1 supports exactly one virtual display", flush=True)
        return 1
    requested_size = (int(width), int(height))
    width, height = sanitize_vkms_resolution(width, height)
    if (width, height) != requested_size:
        print(
            f"[VKMS] Requested {requested_size[0]}x{requested_size[1]} is not a "
            f"standard VKMS size; using {width}x{height}",
            flush=True,
        )
    stopping = [False]
    created = [False]

    def cleanup(*_args):
        if stopping[0]:
            return
        stopping[0] = True
        if created[0]:
            try:
                _helper_response("destroy", timeout=30)
            except VkmsError as exc:
                print(f"[ERROR] VKMS cleanup failed: {exc}", flush=True)

    def stop_from_signal(*_args):
        cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_from_signal)
    signal.signal(signal.SIGTERM, stop_from_signal)

    try:
        original = set(active_compositor_outputs(desktop))
        stale = _helper_response("destroy")
        if stale.get("changed"):
            print("[VKMS] Recovered stale Monitorize VKMS state", flush=True)
            _wait_for_removed_stale_output(desktop, original)
        before = _wait_for_inventory_settle(desktop)
        print(f"[VKMS] Active outputs before creation: {', '.join(sorted(before)) or 'none'}", flush=True)

        created[0] = True
        _helper_response("create")
        output_name, _output = _wait_for_new_output(desktop, set(before))
        print(f"[VKMS] Detected compositor output {output_name}", flush=True)

        ok, actual, message = configure_compositor_output(
            desktop, output_name, width, height
        )
        if not ok:
            raise VkmsError(message)
        print(f"[VKMS] Selected mode: {message}", flush=True)
        event = {
            "type": "headless_ready",
            "name": output_name,
            "width": int(actual["width"]),
            "height": int(actual["height"]),
            "fps": float(actual["refresh_rate"]),
            "backend": "Sunshine",
            "vkms": True,
        }
        print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)
        print(
            f"[VKMS] {output_name} is active; layout and positioning are managed by {desktop.capitalize()}.",
            flush=True,
        )

        while not stopping[0]:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    except (OSError, subprocess.SubprocessError, VkmsError) as exc:
        print(f"[ERROR] VKMS virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()
