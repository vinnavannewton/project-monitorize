
"""Shared ScreenCast portal runner for KDE and wlroots compositors."""

import signal
import secrets
import subprocess
import threading

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from monitorize.streaming.pipeline_builder import launch_with_fallback


def _portal_token(prefix):
    return f"{prefix}_{secrets.token_hex(8)}"


def _request_handle(value):
    return str(value) if value else None


def run_portal_streamer(
    compositor, selector_hint, width, height, fps, bitrate, mode, port,
    encoder, host, source_type=1, prepare_stream=None,
):
    server_mode = mode == "wifi"
    print(
        f"[Streamer {compositor}] Resolution={width}x{height}  FPS={fps}  "
        f"Bitrate={bitrate}  Mode={mode}  Port={port}  SourceType={source_type}"
    )

    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bus = dbus.SessionBus()
    desktop = bus.get_object(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    )
    screen_cast = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
    state = {"step": "create_session", "session": None, "request": None}
    tokens = {
        "create": _portal_token("create"),
        "session": _portal_token("session"),
        "select": _portal_token("select"),
        "start": _portal_token("start"),
    }
    process = {"gst": None}
    cleaning_up = False

    def close_session():
        session = state.get("session")
        if not session:
            return
        state["session"] = None
        try:
            session_object = bus.get_object(
                "org.freedesktop.portal.Desktop",
                session,
            )
            dbus.Interface(
                session_object,
                "org.freedesktop.portal.Session",
            ).Close()
        except Exception:
            pass

    def cleanup(*_args):
        nonlocal cleaning_up
        if cleaning_up:
            return
        cleaning_up = True
        print(f"\n[Monitorize {compositor}] Shutting down...")
        try:
            close_session()
        finally:
            gst = process["gst"]
            if gst and gst.poll() is None:
                gst.terminate()
                try:
                    gst.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    gst.kill()
            if loop.is_running():
                loop.quit()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    def launch_streaming(fd, node_id):
        print(f"[Monitorize {compositor}] Streaming ({mode} mode). Ctrl+C to stop.\n")
        process["gst"] = launch_with_fallback(
            pw_fd=fd,
            node_id=node_id,
            width=width,
            height=height,
            fps=fps,
            bitrate=bitrate,
            port=port,
            hw_encoder=encoder,
            pass_fds=(fd,),
            host=host,
            server_mode=server_mode,
        )
        code = process["gst"].wait()
        print(f"[GStreamer] EXITED: {code}", flush=True)
        GLib.idle_add(loop.quit)

    def on_response(response, results, path=None, **_kwargs):
        request = state.get("request")
        if request and path and str(path) != request:
            return

        if response != 0:
            print(f"[ERROR] Portal denied (code {response})")
            loop.quit()
            return

        if state["step"] == "create_session":
            state["session"] = str(results["session_handle"])
            state["step"] = "select_sources"
            state["request"] = _request_handle(
                screen_cast.SelectSources(state["session"], {
                    "types": dbus.UInt32(source_type),
                    "multiple": dbus.Boolean(False),
                    "cursor_mode": dbus.UInt32(2),
                    "handle_token": dbus.String(tokens["select"]),
                })
            )
        elif state["step"] == "select_sources":
            state["step"] = "start"
            state["request"] = _request_handle(
                screen_cast.Start(
                    state["session"], "",
                    {"handle_token": dbus.String(tokens["start"])},
                )
            )
        else:
            streams = results.get("streams", [])
            if not streams:
                print("[ERROR] No streams from portal.")
                loop.quit()
                return
            if prepare_stream:
                ok, output_name, message = prepare_stream()
                if not ok:
                    print(f"[ERROR] KDE virtual display configuration failed: {message}")
                    loop.quit()
                    return
                print(
                    f"[Portal] Virtual output ready name={output_name} "
                    f"mode={width}x{height}@{fps}"
                )
                print(f"[Portal] {message}")
            node_id = int(streams[0][0])
            fd = screen_cast.OpenPipeWireRemote(state["session"], {}).take()
            print(f"[Portal] Got PipeWire node={node_id} fd={fd}")
            threading.Thread(
                target=launch_streaming, args=(fd, node_id), daemon=True
            ).start()

    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface="org.freedesktop.portal.Request",
        path_keyword="path",
    )
    print(f"[Portal] Creating session... {compositor} will ask you to select a monitor.")
    print(f"         {selector_hint}\n")
    state["request"] = _request_handle(
        screen_cast.CreateSession({
            "handle_token": dbus.String(tokens["create"]),
            "session_handle_token": dbus.String(tokens["session"]),
        })
    )

    try:
        loop.run()
    finally:
        cleanup()
    return process["gst"].returncode if process["gst"] else 1
