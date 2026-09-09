"""Own a KDE Flatpak virtual-display portal session independently of Sunshine."""

import os
import select
import signal
import subprocess
import sys
import time
import uuid


def run_portal_virtual_display(slot, width, height, fps):
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    from monitorize.streaming.headless_virtual_display import _emit_event

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    service = "org.freedesktop.portal.Desktop"
    path = "/org/freedesktop/portal/desktop"
    interface = "org.freedesktop.portal.ScreenCast"
    portal = dbus.Interface(bus.get_object(service, path), interface)
    context = GLib.main_context_default()
    session_path = None
    stopping = False
    remote_fd = None
    negotiator = None

    def stop(*_args):
        nonlocal stopping
        stopping = True

    def request(method, *args, **options):
        token = "monitorize_" + uuid.uuid4().hex
        sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        response = []
        match = bus.add_signal_receiver(
            lambda code, values: response.append((int(code), values)),
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=request_path,
        )
        try:
            options["handle_token"] = token
            getattr(portal, method)(*args, dbus.Dictionary(options, signature="sv"))
            deadline = time.monotonic() + 120
            while not response and not stopping and time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                time.sleep(0.02)
            if not response or response[0][0] != 0:
                try:
                    dbus.Interface(bus.get_object(service, request_path),
                                   "org.freedesktop.portal.Request").Close()
                except dbus.DBusException:
                    pass
                raise RuntimeError("Virtual display permission was cancelled or timed out")
            return response[0][1]
        finally:
            match.remove()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        props = dbus.Interface(bus.get_object(service, path), "org.freedesktop.DBus.Properties")
        available = int(props.Get(interface, "AvailableSourceTypes"))
        if not available & 4:
            raise RuntimeError("This desktop portal does not support virtual displays")
        response = request("CreateSession",
                           session_handle_token="monitorize_" + uuid.uuid4().hex)
        session_path = response["session_handle"]
        request("SelectSources", session_path, types=dbus.UInt32(4),
                multiple=dbus.Boolean(False), cursor_mode=dbus.UInt32(2))
        response = request("Start", session_path, "")
        streams = response.get("streams", [])
        if len(streams) != 1:
            raise RuntimeError("Portal did not return exactly one virtual display")
        node, metadata = streams[0]
        size = metadata.get("size")
        if not size or len(size) != 2 or min(size) <= 0:
            raise RuntimeError("Portal did not report a valid virtual display size")
        actual_width, actual_height = size
        offset_x, offset_y = metadata.get("position", (0, 0))
        remote_fd = portal.OpenPipeWireRemote(session_path, dbus.Dictionary({}, signature="sv")).take()
        bus.add_signal_receiver(stop, signal_name="Closed",
                                dbus_interface="org.freedesktop.portal.Session",
                                path=str(session_path))
        negotiator = subprocess.Popen([
            "/app/bin/monitorize-pipewire-resize", str(remote_fd), str(int(node)),
            str(int(width)), str(int(height)), str(min(int(fps), 60)),
            str(metadata.get("pipewire-serial", "")),
        ], pass_fds=(remote_fd,), stdout=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        confirmed = False
        while not stopping and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if negotiator.poll() is not None:
                break
            ready, _, _ = select.select([negotiator.stdout], [], [], 0.05)
            if ready:
                confirmed = negotiator.stdout.readline().strip() == f"READY {width} {height}"
                break
        if not confirmed:
            raise RuntimeError("KWin could not negotiate the requested virtual display size; resizable virtual output support (KDE 6.8+) is required")
        actual_width, actual_height = width, height
        if fps != 60:
            print("[Headless] KWin's resizable virtual-output API uses a 60 Hz output mode; requested capture rate is separate.", flush=True)
        _emit_event({
            "type": "headless_ready",
            "name": str(metadata.get("mapping_id") or ("Monitorize-2" if slot == "additional" else "Monitorize-1")),
            "node_id": int(node), "portal": True,
            "width": int(actual_width), "height": int(actual_height),
            "offset_x": int(offset_x), "offset_y": int(offset_y),
            "fps": 60,
        })
        print(f"[Headless] KWin negotiated virtual display size: {width}x{height}.", flush=True)
        while not stopping:
            while context.pending():
                context.iteration(False)
            if negotiator.poll() is not None:
                raise RuntimeError("Virtual display size negotiation stream stopped")
            ready, _, _ = select.select([sys.stdin], [], [], 0.005)
            if ready and sys.stdin.readline().strip() in ("", "quit"):
                break
        return 0
    except Exception as exc:
        print(f"[ERROR] Virtual display portal failed: {exc}", flush=True)
        return 1
    finally:
        if negotiator is not None:
            if negotiator.poll() is None:
                negotiator.terminate()
                try:
                    negotiator.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    negotiator.kill()
                    negotiator.wait()
            negotiator.stdout.close()
        if session_path:
            try:
                dbus.Interface(bus.get_object(service, session_path),
                               "org.freedesktop.portal.Session").Close()
            except Exception:
                pass
        if remote_fd is not None:
            os.close(remote_fd)
