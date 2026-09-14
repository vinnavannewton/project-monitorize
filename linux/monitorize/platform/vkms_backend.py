"""Source-only lifecycle for one mainline VKMS configfs display."""

from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from enum import Enum


VKMS_HELPER = Path("/usr/libexec/monitorize/monitorize-source-vkms-helper")
OUTPUT_TIMEOUT = 10.0
DRM_DISCOVERY_TIMEOUT = 5.0
POLL_INTERVAL = 0.1
APPROXIMATE_REFRESH = 60.0


MONITORIZE_VKMS_INSTALL_URL = "https://github.com/vinnavannewton/monitorize-vkms"



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


class VkmsError(RuntimeError):
    pass


class VkmsCustomEdidUnsupported(VkmsError):
    pass


class CustomEdidCapability(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CHECK_FAILED = "check_failed"


_DRM_CONNECTOR_NAME = re.compile(r"^card\d+-.+")
_DRM_MODE_NAME = re.compile(r"^(\d+)x(\d+)$")


def monitorize_drm_connectors(drm_root: Path | None = None) -> dict[str, dict]:
    """Return DRM connectors owned by the persistent Monitorize card.

    A newly registered connector can briefly lack its status or modes files.
    Keep it visible to the readiness state machine as ``unknown`` rather than
    mistaking that short sysfs settling window for a missing connector.
    """
    result = {}
    try:
        entries = sorted((drm_root or Path("/sys/class/drm")).iterdir())
    except OSError:
        return result
    for connector in entries:
        if not _DRM_CONNECTOR_NAME.fullmatch(connector.name):
            continue
        try:
            device = (connector / "device").resolve()
            if "/faux/monitorize" not in str(device):
                continue
        except OSError:
            continue
        try:
            status = (connector / "status").read_text().strip()
        except OSError:
            status = "unknown"
        try:
            modes = (connector / "modes").read_text().splitlines()
        except OSError:
            modes = []
        result[connector.name.split("-", 1)[1]] = {
            "name": connector.name.split("-", 1)[1],
            "path": str(connector),
            "card": connector.name.split("-", 1)[0],
            "status": status,
            "modes": [mode.strip() for mode in modes if mode.strip()],
        }
    return result


def _wait_for_new_drm_connector(
    before: set[str],
    width: int,
    height: int,
    timeout=DRM_DISCOVERY_TIMEOUT,
    interval=POLL_INTERVAL,
    report=None,
):
    """Wait for connector appearance, connected status, then the requested mode."""
    started = time.monotonic()
    deadline = started + timeout
    requested = f"{int(width)}x{int(height)}"
    appeared = None
    appeared_at = None
    last_status = None
    became_connected = False
    waiting_for_mode_logged = False

    def log(message):
        if report:
            report(message)

    while time.monotonic() < deadline:
        current = monitorize_drm_connectors()
        candidates = [entry for name, entry in current.items() if name not in before]
        if len(candidates) == 1:
            connector = candidates[0]
            if appeared is None:
                appeared = connector
                appeared_at = time.monotonic()
                log(f"DRM connector appeared: {connector['card']}-{connector['name']}")

            status = connector["status"].strip().lower()
            if status != last_status:
                if last_status is None:
                    log(f"Initial DRM status: {status or 'unknown'}")
                else:
                    log(f"DRM connector status changed: {last_status} -> {status or 'unknown'}")
                last_status = status

            if status == "connected":
                if not became_connected:
                    elapsed_ms = int((time.monotonic() - appeared_at) * 1000)
                    log(f"DRM connector became connected after {elapsed_ms} ms")
                    became_connected = True
                if requested in connector["modes"]:
                    log(f"DRM modes ready: {', '.join(connector['modes'])}")
                    return connector
                if not waiting_for_mode_logged:
                    log(f"DRM connector is connected; waiting for requested mode {requested}")
                    waiting_for_mode_logged = True
        if len(candidates) > 1:
            raise VkmsError(
                "FAIL_STAGE=DRM_CONNECTOR_APPEAR: kernel registered multiple new "
                "Monitorize VKMS connectors: " + ", ".join(item["name"] for item in candidates)
            )
        time.sleep(interval)

    if appeared is None:
        raise VkmsError(
            "FAIL_STAGE=DRM_CONNECTOR_APPEAR: no new Monitorize Virtual-* connector "
            f"appeared within {timeout:g} seconds"
        )
    if not became_connected:
        raise VkmsError(
            "FAIL_STAGE=DRM_CONNECTOR_STATUS: "
            f"{appeared['card']}-{appeared['name']} appeared but did not become connected "
            f"within {timeout:g} seconds (last status: {last_status or 'unknown'})"
        )
    raise VkmsError(
        "FAIL_STAGE=DRM_MODE_READY: "
        f"{appeared['card']}-{appeared['name']} became connected but requested mode "
        f"{requested} did not appear within {timeout:g} seconds"
    )


def resolution_options(drm_root: Path | None = None) -> list[str]:
    """Return live normal modes, or normal VKMS modes before a device exists."""
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


def custom_edid_capability_from_response(response: dict) -> CustomEdidCapability:
    """Validate the helper's successful custom-EDID capability response."""
    value = str(response.get("capability") or "").lower()
    if value == CustomEdidCapability.SUPPORTED.value:
        return CustomEdidCapability.SUPPORTED
    if value == CustomEdidCapability.UNSUPPORTED.value:
        return CustomEdidCapability.UNSUPPORTED
    raise VkmsError("VKMS capability helper returned an invalid capability result.")


def check_custom_edid_support() -> CustomEdidCapability:
    """Synchronously check configfs EDID capability for non-UI callers."""
    return custom_edid_capability_from_response(_helper_response("capability"))


def open_monitorize_vkms_install_page() -> bool:
    """Open the centralized future monitorize-vkms installation page URL."""
    try:
        return webbrowser.open(MONITORIZE_VKMS_INSTALL_URL)
    except Exception:
        return False


def sanitize_vkms_resolution(width: int, height: int) -> tuple[int, int]:
    requested = (int(width), int(height))
    return requested if requested in VKMS_RESOLUTIONS else (1920, 1080)


def _helper_response(
    operation: str,
    timeout: float = 60.0,
    edid: bytes | None = None,
    *,
    slot: str = "primary",
) -> dict:
    if os.path.isfile("/.flatpak-info"):
        raise VkmsError("VKMS display creation is available only in the native source installation.")
    if operation not in ("create", "create-custom", "destroy", "status", "capability"):
        raise ValueError(f"Unsupported VKMS helper operation: {operation}")
    if operation == "create-custom" and edid is None:
        raise ValueError("Custom VKMS creation requires an EDID payload.")
    if slot not in VKMS_SLOTS:
        raise ValueError(f"Unsupported VKMS slot: {slot}")
    if not VKMS_HELPER.is_file() or not os.access(VKMS_HELPER, os.X_OK):
        raise VkmsError("The VKMS helper is not installed. Re-run the Monitorize source installer.")
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise VkmsError("Polkit (pkexec) is required to create a VKMS virtual display.")
    try:
        command = [pkexec, str(VKMS_HELPER), operation, "--slot", slot]
        if edid is not None:
            command.append(base64.b64encode(edid).decode("ascii"))
        result = subprocess.run(
            command,
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
            if response.get("error_kind") == "custom_edid_unsupported":
                raise VkmsCustomEdidUnsupported(detail or "Custom VKMS EDID support is unavailable.")
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


def configure_compositor_output(
    desktop: str,
    output_name: str,
    width: int,
    height: int,
    refresh: float = APPROXIMATE_REFRESH,
):
    modes = available_modes(desktop, output_name)
    print(f"[VKMS] Available modes for {output_name}: {_mode_summary(modes)}", flush=True)
    if desktop == "kde":
        from monitorize.platform.kde_virtual_monitor import configure_existing_output_mode

        return configure_existing_output_mode(output_name, width, height, refresh)
    if desktop == "gnome":
        from monitorize.platform.gnome_virtual_monitor import configure_existing_output_mode

        return configure_existing_output_mode(output_name, width, height, refresh)
    from monitorize.platform.display_controller import DisplayController

    return DisplayController(desktop).configure_existing_output_mode(
        output_name, width, height, refresh
    )


def run_vkms_headless(
    slot: str,
    width: int,
    height: int,
    fps: int,
    desktop: str,
    *,
    custom_mode: bool = False,
) -> int:
    if slot not in VKMS_SLOTS:
        print(f"[ERROR] Unsupported VKMS display slot: {slot}", flush=True)
        return 1
    requested_size = (int(width), int(height))
    edid = None
    if custom_mode:
        from monitorize.platform.vkms_edid import EdidError, generate_edid

        try:
            print(f"[VKMS] Custom mode requested: {width}x{height}@{fps}", flush=True)
            edid = generate_edid(width, height, fps)
            print("[VKMS] Custom EDID generated successfully", flush=True)
        except EdidError as exc:
            print(f"[ERROR] Custom VKMS mode is invalid: {exc}", flush=True)
            return 1
    else:
        width, height = sanitize_vkms_resolution(width, height)
        if (width, height) != requested_size:
            print(
                f"[VKMS] Requested {requested_size[0]}x{requested_size[1]} is not a "
                f"standard VKMS size; using {width}x{height}",
                flush=True,
            )
    stopping = [False]
    created = [False]
    gnome_before_identities = None

    def cleanup(*_args):
        if stopping[0]:
            return
        stopping[0] = True
        if created[0]:
            gnome_cleanup = None
            if desktop == "gnome" and gnome_before_identities is not None:
                from monitorize.platform import gnome_virtual_monitor

                gnome_cleanup = gnome_virtual_monitor.remove_new_vkms_monitors_from_layout
                if gnome_cleanup(gnome_before_identities, attempts=5):
                    print("[VKMS] Removed temporary GNOME VKMS layout", flush=True)
            try:
                _helper_response("destroy", timeout=30, slot=slot)
            except VkmsError as exc:
                print(f"[ERROR] VKMS cleanup failed: {exc}", flush=True)
            if gnome_cleanup and gnome_cleanup(gnome_before_identities, attempts=30):
                print("[VKMS] Removed late GNOME VKMS layout", flush=True)

    def stop_from_signal(*_args):
        cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_from_signal)
    signal.signal(signal.SIGTERM, stop_from_signal)

    try:
        original = set(active_compositor_outputs(desktop))
        stale = _helper_response("destroy", slot=slot)
        if stale.get("changed"):
            print("[VKMS] Recovered stale Monitorize VKMS state", flush=True)
            _wait_for_removed_stale_output(desktop, original)
        before = _wait_for_inventory_settle(desktop)
        print(f"[VKMS] Active outputs before creation: {', '.join(sorted(before)) or 'none'}", flush=True)
        drm_before = set(monitorize_drm_connectors())

        if desktop == "gnome":
            from monitorize.platform import gnome_virtual_monitor

            try:
                gnome_before_state = gnome_virtual_monitor._mutter_state()
                gnome_before_identities = gnome_virtual_monitor.physical_monitor_identities(
                    gnome_before_state
                )
                print(
                    "[VKMS] GNOME physical connectors before creation: "
                    f"{', '.join(sorted(gnome_before_identities)) or 'none'}",
                    flush=True,
                )
            except Exception as exc:
                raise VkmsError(f"Could not read GNOME DisplayConfig before VKMS creation: {exc}") from exc

        created[0] = True
        _helper_response(
            "create-custom" if custom_mode else "create", edid=edid, slot=slot
        )
        if custom_mode:
            print("[VKMS] Custom connector EDID written and enabled", flush=True)
        if desktop == "gnome":
            print("[VKMS] Dynamic connector enabled; waiting for DRM readiness...", flush=True)
            drm_connector = _wait_for_new_drm_connector(
                drm_before,
                width,
                height,
                report=lambda message: print(f"[VKMS] {message}", flush=True),
            )
            print(
                "[VKMS] DRM connector discovered: "
                f"{drm_connector['name']} on {drm_connector['card']} "
                f"({drm_connector['path']}; status={drm_connector['status']}; "
                f"modes={', '.join(drm_connector['modes'])})",
                flush=True,
            )
            print("[VKMS] Waiting for Mutter physical discovery of the new VKMS connector...", flush=True)
            physical_connector, _state, discovery_error = gnome_virtual_monitor.wait_for_new_vkms_connector(
                gnome_before_identities, width, height
            )
            if not physical_connector:
                raise VkmsError(
                    "FAIL_STAGE=MUTTER_DISCOVERY: kernel dynamic hotplug succeeded, but "
                    + discovery_error
                )
            print(f"[VKMS] Mutter physical monitor appeared: {physical_connector}", flush=True)
            print("[VKMS] Applying Mutter logical config after physical discovery...", flush=True)
            ok, actual, message = gnome_virtual_monitor.activate_discovered_vkms_monitor(
                gnome_before_identities,
                width,
                height,
                float(fps) if custom_mode else APPROXIMATE_REFRESH,
            )
            output_name = actual.get("name", physical_connector)
        else:
            output_name, _output = _wait_for_new_output(desktop, set(before))
            print(f"[VKMS] Detected compositor output {output_name}", flush=True)
            ok, actual, message = configure_compositor_output(
                desktop, output_name, width, height,
                float(fps) if custom_mode else APPROXIMATE_REFRESH,
            )
        if not ok:
            if desktop == "gnome" and not message.startswith("FAIL_STAGE="):
                message = "FAIL_STAGE=MUTTER_ACTIVATION: " + message
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
    except VkmsCustomEdidUnsupported as exc:
        print(
            "MONITORIZE_EVENT " + json.dumps(
                {"type": "vkms_custom_edid_unsupported"}, separators=(",", ":")
            ),
            flush=True,
        )
        print(f"[ERROR] Custom VKMS resolution unavailable: {exc}", flush=True)
        return 1
    except (OSError, subprocess.SubprocessError, VkmsError) as exc:
        print(f"[ERROR] VKMS virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()
