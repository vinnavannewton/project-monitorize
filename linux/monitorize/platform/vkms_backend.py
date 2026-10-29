"""Session-owned virtual displays provided by the host VKMS service."""

from __future__ import annotations

import json
import os
import select
import signal
import shutil
import subprocess
import sys
import webbrowser

from monitorize.platform.monitorize_vkms_dbus import (
    MonitorizeVkmsClient,
    MonitorizeVkmsError,
    VkmsCommandError, VkmsServiceUnavailableError, VkmsProtocolError, VkmsCliNotFoundError,
)

VKMS_SLOTS = {"primary": "mon1", "additional": "mon2"}
MONITORIZE_VKMS_INSTALL_URL = "https://github.com/vinnavannewton/monitorize-vkms"


def _recovery_required(error: MonitorizeVkmsError) -> bool:
    """Recognize unavailable service, stale protocols or broken kernel integration."""
    if isinstance(error,(VkmsServiceUnavailableError,VkmsProtocolError,VkmsCliNotFoundError)):return True
    if str(getattr(error,'error_type','')).lower() != "helper_error":
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


def _report_recovery_required():
    print('MONITORIZE_EVENT {"type":"vkms_recovery_required"}', flush=True)

def open_monitorize_vkms_install_page() -> bool:
    """Open the monitorize-vkms installation page URL."""
    try:
        return webbrowser.open(MONITORIZE_VKMS_INSTALL_URL)
    except Exception:
        return False


def recover_disabled_boot_reservation():
    """Explicit normal Start may recover only this user's disabled boot mode."""
    from monitorize.remote_login.settings import read_settings
    try:settings=read_settings()
    except ValueError:return False
    if settings.enabled or settings.uid!=os.getuid():return False
    command=shutil.which('monitorize-vkms')
    if not command:return False
    print('[VKMS] Releasing disabled boot reservation; administrator authorization may be requested.',flush=True)
    result=subprocess.run([command,'remove','mon1','--json'],capture_output=True,text=True,timeout=120,check=False)
    try:values=json.loads(result.stdout)
    except ValueError:raise MonitorizeVkmsError('Could not confirm boot reservation release. Use Settings → Remove virtual display.') from None
    if result.returncode or not values.get('success'):
        details=[str(item['error']) for item in values.get('results',{}).values()
                 if isinstance(item,dict) and item.get('error')]
        raise MonitorizeVkmsError(' '.join([str(values.get('message') or 'Boot reservation release failed.'),*details]))
    return True


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
        if _recovery_required(exc):_report_recovery_required()
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

        if desktop.lower() in ('hyprland', 'x11'):
            vkms_client.require_managed_backend(desktop.lower())
        if desktop.lower() == 'hyprland':
            from monitorize.platform.hyprland_layout import HyprlandLayout
            HyprlandLayout().require_vkms_lifecycle()

        options = {'display': VKMS_SLOTS[slot]}
        if os.environ.get('MONITORIZE_VKMS_UNATTENDED') == '1':
            options['unattended'] = True
        try:
            result = vkms_client.create_display(width, height, fps, **options)
        except MonitorizeVkmsError as error:
            if ('boot remote-login output is reserved' not in str(error).lower()
                    or VKMS_SLOTS[slot]!='mon1' or not recover_disabled_boot_reservation()):raise
            result = vkms_client.create_display(width, height, fps, **options)
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
        gnome_wayland = desktop.lower() == 'gnome' and os.environ.get('XDG_SESSION_TYPE', '').lower() != 'x11'
        if gnome_wayland and os.environ.get(
            "MONITORIZE_VKMS_CAPTURE", "pipewire_node" if os.path.isfile("/.flatpak-info") else "kms"
        ) == "pipewire_node":
            from monitorize.platform.gnome_monitor_capture import GnomeMonitorCapture
            capture = GnomeMonitorCapture()
            event.update(capture.start(output_name))
        elif gnome_wayland:
            from monitorize.platform.gnome_virtual_monitor import display_config_interface
            state = display_config_interface().GetCurrentState()
            for logical in state[2]:
                if output_name in [str(spec[0]) for spec in logical[5]]:
                    event.update(offset_x=int(logical[0]), offset_y=int(logical[1]))
                    break
            else:
                raise RuntimeError(f"GNOME output {output_name} disappeared before capture was ready")
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
        if _recovery_required(exc):
            _report_recovery_required()
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
