"""Headless virtual display manager for external capture backends (e.g. Sunshine).

Creates and holds a virtual monitor active in the compositor (KWin/Hyprland)
without starting any GStreamer, encoding, or RTP streaming processes.
"""

import ctypes
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
from monitorize.platform.kde_helper import find_helper, read_helper_event, stop_helper

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


def _find_kwin_screencast_node(output_name, timeout=5.0):
    """Find the PipeWire node ID for KWin's screencast of the given output.

    KWin creates PipeWire screencast nodes named ``kwin-screencast-<output>``
    for each active output. This function polls ``pw-dump`` until the node
    appears or *timeout* seconds elapse.

    Returns the integer node ID, or 0 if not found.
    """
    exact_media_name = f"kwin-screencast-{output_name}".lower()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["pw-dump"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                nodes = json.loads(result.stdout)
                # First pass: look for exact media.name match with Stream/Output/Video
                for obj in nodes:
                    if obj.get("type") != "PipeWire:Interface:Node":
                        continue
                    props = obj.get("info", {}).get("props", {})
                    media_name = str(props.get("media.name") or "").lower()
                    media_class = str(props.get("media.class") or "")
                    if media_name == exact_media_name and media_class == "Stream/Output/Video":
                        return int(obj.get("id", 0))

                # Second pass: look for partial match in media.name or node.description
                for obj in nodes:
                    if obj.get("type") != "PipeWire:Interface:Node":
                        continue
                    props = obj.get("info", {}).get("props", {})
                    media_name = str(props.get("media.name") or "").lower()
                    media_class = str(props.get("media.class") or "")
                    node_desc = str(props.get("node.description") or "").lower()
                    if media_class == "Stream/Output/Video":
                        if output_name.lower() in media_name or output_name.lower() in node_desc:
                            return int(obj.get("id", 0))
        except Exception:
            pass
        time.sleep(0.3)

    return 0


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
        stop_helper(helper)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        owner = read_helper_event(helper, "owner_ready")
        actual_name = owner.get("name") or output_name

        ok, actual, message = configure_native_virtual_output(
            actual_name, width, height, fps
        )
        if not ok:
            raise RuntimeError(message)

        print(f"[Headless] {message}", flush=True)
        node_id = _find_kwin_screencast_node(actual_name)
        if node_id:
            print(f"[Headless] Found PipeWire screencast node {node_id} for {actual_name}", flush=True)
        else:
            print(f"[Headless] Warning: no PipeWire screencast node found for {actual_name}", flush=True)
        _emit_event({
            "type": "headless_ready",
            "name": actual_name,
            "node_id": node_id,
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


def run_hyprland_headless(slot, width, height, fps):
    from monitorize.platform.display_controller import DisplayController

    controller = DisplayController("hyprland")
    output, error = controller.prepare_hyprland(width, height, fps, slot=slot)
    if error or not output:
        print(f"[ERROR] Hyprland headless output creation failed: {error}", flush=True)
        return 1

    print(
        f"[Headless] Hyprland Virtual display {output} ({width}x{height}@{fps}Hz) is active. "
        "Ready for Sunshine / Moonlight.",
        flush=True,
    )
    _emit_event({
        "type": "headless_ready",
        "name": output,
        "width": width,
        "height": height,
        "fps": fps,
        "backend": "Sunshine",
    })

    def cleanup(*_args):
        controller.remove_hyprland_output(slot=slot)

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
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        from monitorize.platform import gnome_virtual_monitor

        DBusGMainLoop(set_as_default=True)
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
        stream_path = session.RecordVirtual({
            "modes": modes,
            "cursor-mode": dbus.UInt32(1),
            "is-platform": dbus.Boolean(True),
        })

        node_id_holder = [0]

        def on_pipewire_stream_added(node_id):
            try:
                node_id_holder[0] = int(node_id)
            except Exception:
                pass

        stream_obj = bus.get_object("org.gnome.Mutter.ScreenCast", stream_path)
        stream_obj.connect_to_signal(
            "PipeWireStreamAdded", on_pipewire_stream_added,
            dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
        )

        session.Start()

        connector = ""
        context = GLib.main_context_default()
        for _ in range(30):
            try:
                while context.pending():
                    context.iteration(False)
            except Exception:
                pass
            try:
                state = display_config.GetCurrentState()
                found = gnome_virtual_monitor.new_virtual_connector(
                    state, before, width, height
                )
                if found:
                    connector = found
                    if node_id_holder[0] != 0:
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

        offset_x, offset_y = 0, 0
        try:
            state = display_config.GetCurrentState()
            gnome_virtual_monitor.map_sunshine_gnome_peripherals(state, connector)
            logical_monitors = state[2]
            for lm in logical_monitors:
                lm_connectors = [str(c[0]) for c in lm[5] if c]
                if connector in lm_connectors:
                    offset_x = int(float(lm[0]))
                    offset_y = int(float(lm[1]))
                    break
        except Exception as exc:
            print(f"[Headless] Could not determine offset/mapping for {connector}: {exc}", flush=True)

        _emit_event({
            "type": "headless_ready",
            "name": connector,
            "node_id": node_id_holder[0],
            "offset_x": offset_x,
            "offset_y": offset_y,
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
        sys.exit(run_hyprland_headless(slot, width, height, fps))
    else:
        sys.exit(run_kde_headless(slot, width, height, fps))


if __name__ == "__main__":
    main()
