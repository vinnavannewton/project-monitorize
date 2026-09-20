"""Own a KDE Flatpak virtual-display portal session independently of Sunshine."""

import os
import select
import signal
import subprocess
import sys
import time
import uuid

from monitorize.platform.kde_virtual_monitor import (
    kde_output_query_error,
    repair_kde_extended_topology,
    wait_for_kde_output_absent,
    wait_for_kde_outputs,
    wait_for_new_kde_output,
)


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

    def close_session(current_path):
        dbus.Interface(
            bus.get_object(service, current_path),
            "org.freedesktop.portal.Session",
        ).Close()

    def close_negotiator():
        nonlocal negotiator
        if negotiator is None:
            return
        if negotiator.poll() is None:
            negotiator.terminate()
            try:
                negotiator.wait(timeout=2)
            except subprocess.TimeoutExpired:
                negotiator.kill()
                negotiator.wait()
        negotiator.stdout.close()
        negotiator = None

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
        baseline_outputs = wait_for_kde_outputs()
        if not baseline_outputs:
            raise RuntimeError(
                "Could not query KDE outputs through the Flatpak host: "
                f"{kde_output_query_error()}"
            )

        node = None
        metadata = None
        portal_output_name = ""
        offset_x = 0
        offset_y = 0
        for creation_attempt in range(2):
            response = request(
                "CreateSession",
                session_handle_token="monitorize_" + uuid.uuid4().hex,
            )
            session_path = response["session_handle"]
            request(
                "SelectSources",
                session_path,
                types=dbus.UInt32(4),
                multiple=dbus.Boolean(False),
                cursor_mode=dbus.UInt32(2),
            )
            response = request("Start", session_path, "")
            streams = response.get("streams", [])
            if len(streams) != 1:
                raise RuntimeError("Portal did not return exactly one virtual display")
            node, metadata = streams[0]
            discovered = wait_for_new_kde_output(
                baseline_outputs, metadata.get("mapping_id", "")
            )
            if not discovered:
                raise RuntimeError("Could not identify the KDE portal virtual output")
            portal_output, observed_outputs = discovered
            ok, topology, message = repair_kde_extended_topology(
                portal_output, observed_outputs
            )
            if not ok:
                raise RuntimeError(message)
            portal_output_name = topology["name"]
            offset_x, offset_y = topology["position"]

            if not topology["was_mirrored"]:
                break
            if creation_attempt:
                raise RuntimeError(
                    "KWin repeatedly restored mirroring on the portal virtual output"
                )

            print(
                "[Headless] Recreating the portal stream after clearing "
                "restored KWin mirroring.",
                flush=True,
            )
            close_session(session_path)
            session_path = None
            if not wait_for_kde_output_absent(portal_output):
                raise RuntimeError(
                    "The provisional KDE portal output did not close after repair"
                )
            baseline_outputs = wait_for_kde_outputs()
            if not baseline_outputs:
                raise RuntimeError(
                    "Could not refresh KDE outputs after portal repair: "
                    f"{kde_output_query_error()}"
                )
        else:
            raise RuntimeError("Could not create an unmirrored KDE portal stream")

        size = metadata.get("size")
        if not size or len(size) != 2 or min(size) <= 0:
            raise RuntimeError("Portal did not report a valid virtual display size")
        actual_width, actual_height = size
        remote_fd = portal.OpenPipeWireRemote(session_path, dbus.Dictionary({}, signature="sv")).take()
        bus.add_signal_receiver(stop, signal_name="Closed",
                                dbus_interface="org.freedesktop.portal.Session",
                                path=str(session_path))
        requested_size = (int(width), int(height))
        actual_size = (int(actual_width), int(actual_height))
        if actual_size != requested_size:
            resize_fd = portal.OpenPipeWireRemote(
                session_path, dbus.Dictionary({}, signature="sv")
            ).take()
            try:
                negotiator = subprocess.Popen([
                    "/app/bin/monitorize-pipewire-resize", str(resize_fd),
                    str(int(node)), str(int(width)), str(int(height)),
                    str(min(int(fps), 60)),
                    str(metadata.get("pipewire-serial", "")),
                ], pass_fds=(resize_fd,), stdout=subprocess.PIPE, text=True)
            finally:
                os.close(resize_fd)
            deadline = time.monotonic() + 20
            confirmed = False
            while not stopping and time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if negotiator.poll() is not None:
                    break
                ready, _, _ = select.select([negotiator.stdout], [], [], 0.05)
                if ready:
                    confirmed = (
                        negotiator.stdout.readline().strip()
                        == f"READY {width} {height}"
                    )
                    break
            if confirmed:
                actual_width, actual_height = requested_size
            else:
                close_negotiator()
                print(
                    "[Headless] KWin kept the portal virtual display at "
                    f"{actual_width}x{actual_height}; requested {width}x{height} "
                    "requires Plasma 6.8+. Continuing with the compositor-provided "
                    "size.",
                    flush=True,
                )
        else:
            print(
                "[Headless] KWin portal supplied the requested virtual display "
                f"size: {actual_width}x{actual_height}.",
                flush=True,
            )
        if fps != 60:
            print("[Headless] KWin's resizable virtual-output API uses a 60 Hz output mode; requested capture rate is separate.", flush=True)
        _emit_event({
            "type": "headless_ready",
            "name": portal_output_name,
            "node_id": int(node), "portal": True,
            "width": int(actual_width), "height": int(actual_height),
            "offset_x": int(offset_x), "offset_y": int(offset_y),
            "fps": 60,
        })
        print(
            "[Headless] KWin virtual display size: "
            f"{actual_width}x{actual_height}.",
            flush=True,
        )
        while not stopping:
            while context.pending():
                context.iteration(False)
            if negotiator is not None and negotiator.poll() is not None:
                raise RuntimeError("Virtual display size negotiation stream stopped")
            ready, _, _ = select.select([sys.stdin], [], [], 0.005)
            if ready and sys.stdin.readline().strip() in ("", "quit"):
                break
        return 0
    except Exception as exc:
        print(f"[ERROR] Virtual display portal failed: {exc}", flush=True)
        return 1
    finally:
        close_negotiator()
        if session_path:
            try:
                dbus.Interface(bus.get_object(service, session_path),
                               "org.freedesktop.portal.Session").Close()
            except Exception:
                pass
        if remote_fd is not None:
            os.close(remote_fd)
