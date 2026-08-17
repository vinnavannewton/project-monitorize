"""Headless virtual display manager for external capture backends (e.g. Sunshine).

Creates and holds a virtual monitor active in the compositor (KWin/Hyprland)
without starting any GStreamer, encoding, or RTP streaming processes.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time

from monitorize.platform.kde_virtual_monitor import (
    configure_native_virtual_output,
    virtual_slot,
    wait_for_output_absent,
)
import ctypes
from monitorize.streaming.kde_native_streamer import find_helper, _read_helper_event, _stop_helper

PR_SET_PDEATHSIG = 1


def _set_pdeathsig() -> None:
    """Set Linux parent-death signal to guarantee termination if Monitorize dies."""
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except Exception:
            pass


def _emit_event(event: dict):
    print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)


def run_kde_headless(slot, width, height, fps):
    slot_info = virtual_slot(slot)
    output_name = slot_info["output_name"]

    if not wait_for_output_absent(output_name):
        print(f"[ERROR] {output_name} is already active; stop existing session", flush=True)
        return 1

    helper_path = find_helper()
    if not helper_path:
        print("[ERROR] KDE native helper is missing. Re-run the Monitorize installer.", flush=True)
        return 1

    helper = subprocess.Popen(
        [
            helper_path,
            slot_info["base_name"],
            slot_info["description"],
            str(width),
            str(height),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=_set_pdeathsig,
    )

    def cleanup(*_args):
        _stop_helper(helper)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        owner = _read_helper_event(helper, "owner_ready")
        actual_name = owner.get("name") or output_name

        ok, actual, message = configure_native_virtual_output(
            actual_name, width, height, fps
        )
        if not ok:
            raise RuntimeError(message)

        print(f"[Headless] {message}", flush=True)
        _emit_event({
            "type": "headless_ready",
            "name": actual_name,
            "width": actual.get("width", width),
            "height": actual.get("height", height),
            "fps": actual.get("refresh_rate", fps),
            "backend": "Sunshine",
        })

        print(
            f"[Headless] Virtual display {output_name} is active. "
            "Ready for Sunshine / Moonlight streaming.",
            flush=True,
        )

        
        while helper.poll() is None:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    except (BrokenPipeError, OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] KDE headless virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()


def run_hyprland_headless(width, height, fps):
    cmd = ["hyprctl", "output", "create", "headless", f"{width}x{height}@{fps}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Hyprland headless output creation failed: {res.stderr}", flush=True)
        return 1

    output_name = res.stdout.strip() or "HEADLESS-1"
    print(f"[Headless] Created Hyprland output: {output_name}", flush=True)
    _emit_event({
        "type": "headless_ready",
        "name": output_name,
        "width": width,
        "height": height,
        "fps": fps,
        "backend": "Sunshine",
    })

    def cleanup(*_args):
        subprocess.run(["hyprctl", "output", "remove", output_name], capture_output=True)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    finally:
        cleanup()


def run_gnome_headless(slot, width, height, fps, display_type="Extend"):
    try:
        import dbus
        from monitorize.platform import gnome_virtual_monitor

        bus = dbus.SessionBus()
        display_config = gnome_virtual_monitor.display_config_interface(bus, dbus)

        before = set(gnome_virtual_monitor.virtual_connectors_from_state(
            display_config.GetCurrentState()
        ))

        screencast_obj = bus.get_object(
            "org.gnome.Mutter.ScreenCast",
            "/org/gnome/Mutter/ScreenCast",
        )
        screencast = dbus.Interface(screencast_obj, "org.gnome.Mutter.ScreenCast")
        session_path = screencast.CreateSession({})
        session_obj = bus.get_object("org.gnome.Mutter.ScreenCast", session_path)
        session = dbus.Interface(session_obj, "org.gnome.Mutter.ScreenCast.Session")

        preferred_scale = gnome_virtual_monitor.load_saved_virtual_scale(slot)
        mode_val = {
            "size": dbus.Struct([dbus.UInt32(width), dbus.UInt32(height)], signature="uu"),
            "refresh-rate": dbus.Double(float(fps)),
            "is-preferred": dbus.Boolean(True),
        }
        if preferred_scale:
            mode_val["preferred-scale"] = dbus.Double(float(preferred_scale))

        modes = dbus.Array([dbus.Dictionary(mode_val, signature="sv")], signature="a{sv}")
        session.RecordVirtual({
            "modes": modes,
            "cursor-mode": dbus.UInt32(1),
            "is-platform": dbus.Boolean(True),
        })

        session.Start()

        connector = ""
        for _ in range(30):
            try:
                state = display_config.GetCurrentState()
                found = gnome_virtual_monitor.new_virtual_connector(
                    state, before, width, height
                )
                if found:
                    connector = found
                    break
            except Exception:
                pass
            time.sleep(0.1)

        if not connector:
            state = display_config.GetCurrentState()
            virtual_connectors = gnome_virtual_monitor.virtual_connectors_from_state(state)
            remaining = [c for c in virtual_connectors if c not in before]
            connector = remaining[0] if remaining else (virtual_connectors[0] if virtual_connectors else "Virtual-1")

        roles = {slot: connector}
        primary = os.environ.get("MONITORIZE_GNOME_PRIMARY_OUTPUT", "")
        if primary:
            roles["primary"] = primary
        topology = "+".join(role for role in ("primary", "additional") if role in roles)

        try:
            gnome_virtual_monitor.restore_virtual_layout(
                slot=topology,
                display_config=display_config,
                dbus=dbus,
                attempts=1,
                delay=0,
                role_connectors=roles,
            )
        except Exception as exc:
            print(f"[Headless] GNOME layout restore skipped: {exc}", flush=True)

        _emit_event({
            "type": "headless_ready",
            "name": connector,
            "width": width,
            "height": height,
            "fps": fps,
            "backend": "Sunshine",
        })

        print(
            f"[Headless] GNOME Virtual display {connector} ({width}x{height}@{fps}Hz) is active. "
            "Ready for Sunshine / Moonlight.",
            flush=True,
        )

        def cleanup(*_args):
            try:
                session.Stop()
            except Exception:
                pass

        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

        try:
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0.5)
                if ready:
                    line = sys.stdin.readline()
                    if not line or line.strip() == "quit":
                        break
            return 0
        finally:
            cleanup()
    except Exception as exc:
        print(f"[ERROR] GNOME headless virtual display failed: {exc}", flush=True)
        return 1


def main():
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 1080
    fps = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    slot = sys.argv[4] if len(sys.argv) > 4 else "primary"
    de = (sys.argv[5] if len(sys.argv) > 5 else os.environ.get("XDG_CURRENT_DESKTOP", "")).lower()

    if "kde" in de or "plasma" in de:
        sys.exit(run_kde_headless(slot, width, height, fps))
    elif "gnome" in de or "ubuntu" in de:
        sys.exit(run_gnome_headless(slot, width, height, fps))
    elif "hyprland" in de:
        sys.exit(run_hyprland_headless(width, height, fps))
    else:
        sys.exit(run_kde_headless(slot, width, height, fps))


if __name__ == "__main__":
    main()

