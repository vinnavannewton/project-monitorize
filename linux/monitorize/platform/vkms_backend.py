"""Session-owned virtual displays provided by the host VKMS service."""

from __future__ import annotations

import json
import select
import signal
import sys
import webbrowser

from monitorize.platform.monitorize_vkms_dbus import (
    MonitorizeVkmsClient,
    MonitorizeVkmsError,
    VkmsCommandError,
)

VKMS_SLOTS = {"primary": "mon1", "additional": "mon2"}
MONITORIZE_VKMS_INSTALL_URL = "https://github.com/vinnavannewton/monitorize-vkms"


def _reinstall_required(error: VkmsCommandError) -> bool:
    """Recognize an installed CLI with a broken helper or kernel integration."""
    if error.error_type.lower() != "helper_error":
        return False
    detail = str(error.raw_response.get("message") or error).lower()
    return any(marker in detail for marker in (
        "no module named 'monitorize_vkms'",
        'no module named "monitorize_vkms"',
        "monitorize-vkms helper is not installed",
        "vkms driver does not expose the required configfs interface",
        "monitorize vkms configfs is not registered",
        "monitorize vkms bootstrap is not initialized",
        "monitorize vkms bootstrap is incomplete",
        "two-display monitorize vkms bootstrap is not initialized",
        "persistent monitorize vkms connector is not registered",
    ))


def _report_reinstall_required():
    print('MONITORIZE_EVENT {"type":"vkms_reinstall_required"}', flush=True)

def open_monitorize_vkms_install_page() -> bool:
    """Open the monitorize-vkms installation page URL."""
    try:
        return webbrowser.open(MONITORIZE_VKMS_INSTALL_URL)
    except Exception:
        return False


def run_vkms_headless(
    slot: str,
    width: int,
    height: int,
    fps: int | float,
    desktop: str = "",
    *,
    client: MonitorizeVkmsClient | None = None,
) -> int:
    """Hold a VKMS display for one Monitorize session.

    Both slots share the host service but retain independent cleanup generations.
    """
    if slot not in VKMS_SLOTS:
        print(f"[ERROR] Unsupported VKMS display slot: {slot}", flush=True)
        return 1

    vkms_client = client or MonitorizeVkmsClient()
    try:
        vkms_client.require_ready()
    except MonitorizeVkmsError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 1

    capture = None
    stopping = [False]
    cleanup_ok = [True]
    created = [False]
    created_connector = [None]
    generation = [None]
    stop_requested = [False]

    def cleanup(*_args):
        if stopping[0]:
            return cleanup_ok[0]
        stopping[0] = True
        if capture is not None:
            try:
                capture.close()
            except Exception as exc:
                cleanup_ok[0] = False
                print(f"[ERROR] Could not stop GNOME capture: {exc}", flush=True)
        if created[0]:
            conn_str = f" {created_connector[0]}" if created_connector[0] else ""
            print(f"[VKMS] Removing{conn_str} through monitorize-vkms", flush=True)
            try:
                res = vkms_client.remove_display(VKMS_SLOTS[slot], generation[0])
                if not res.get("success", True):
                    cleanup_ok[0] = False
                    print(
                        f"[ERROR] VKMS cleanup failed: {res.get('message', 'unknown error')}",
                        flush=True,
                    )
                else:
                    print(f"[VKMS] Virtual display{conn_str} removed", flush=True)
            except Exception as exc:
                cleanup_ok[0] = False
                print(f"[ERROR] VKMS cleanup failed: {exc}", flush=True)
        return cleanup_ok[0]

    def stop_from_signal(*_args):
        stop_requested[0] = True

    signal.signal(signal.SIGINT, stop_from_signal)
    signal.signal(signal.SIGTERM, stop_from_signal)

    try:
        print("[VKMS] Using host monitorize-vkms service", flush=True)
        print(f"[VKMS] Requesting display: {width}x{height}@{fps}", flush=True)

        result = vkms_client.create_display(width, height, fps, display=VKMS_SLOTS[slot])
        if result["created"] is not True:
            raise MonitorizeVkmsError(f"{VKMS_SLOTS[slot]} already belongs to an existing holder. Stop its owning session first.")
        output_name = result["name"]
        created_connector[0] = output_name
        generation[0] = result["generation"]
        created[0] = True
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            line = sys.stdin.readline()
            stop_requested[0] = stop_requested[0] or not line or line.strip() == "quit"
        if stop_requested[0]:
            return 0 if cleanup() else 1
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
            "display": VKMS_SLOTS[slot],
            "generation": generation[0],
            "card": result["card"],
        }
        if desktop.lower() == "gnome":
            from monitorize.platform.gnome_monitor_capture import GnomeMonitorCapture
            capture = GnomeMonitorCapture()
            event.update(capture.start(output_name))
        if stop_requested[0]:
            return 0 if cleanup() else 1
        print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)
        print(
            f"[VKMS] {output_name} is active at {actual_width}x{actual_height}@{actual_fps:g}Hz.",
            flush=True,
        )

        while not stop_requested[0]:
            if capture is not None:
                capture.dispatch()
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0 if cleanup() else 1
    except VkmsCommandError as exc:
        if _reinstall_required(exc):
            _report_reinstall_required()
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
