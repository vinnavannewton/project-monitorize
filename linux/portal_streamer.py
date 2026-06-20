
"""Shared ScreenCast portal runner for KDE and wlroots compositors."""

import signal
import subprocess
import sys
import threading

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from pipeline_builder import launch_with_fallback


def run_portal_streamer(
    compositor, selector_hint, width, height, fps, bitrate, mode, port,
    encoder, host,
):
    server_mode = mode == "wifi"
    print(
        f"[Streamer {compositor}] Resolution={width}x{height}  FPS={fps}  "
        f"Bitrate={bitrate}  Mode={mode}  Port={port}"
    )

    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bus = dbus.SessionBus()
    desktop = bus.get_object(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    )
    screen_cast = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
    state = {"step": "create_session", "session": None}
    process = {"gst": None}
    cleaning_up = False

    def cleanup(*_args):
        nonlocal cleaning_up
        if cleaning_up:
            return
        cleaning_up = True
        print(f"\n[Monitorize {compositor}] Shutting down...")
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

    def on_response(response, results, **_kwargs):
        if response != 0:
            print(f"[ERROR] Portal denied (code {response})")
            loop.quit()
            return

        if state["step"] == "create_session":
            state["session"] = str(results["session_handle"])
            state["step"] = "select_sources"
            screen_cast.SelectSources(state["session"], {
                "types": dbus.UInt32(1),
                "multiple": dbus.Boolean(False),
                "cursor_mode": dbus.UInt32(2),
                "handle_token": dbus.String("tok2"),
            })
        elif state["step"] == "select_sources":
            state["step"] = "start"
            screen_cast.Start(
                state["session"], "", {"handle_token": dbus.String("tok3")}
            )
        else:
            streams = results.get("streams", [])
            if not streams:
                print("[ERROR] No streams from portal.")
                loop.quit()
                return
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
    )
    print(f"[Portal] Creating session... {compositor} will ask you to select a monitor.")
    print(f"         {selector_hint}\n")
    screen_cast.CreateSession({
        "handle_token": dbus.String("tok1"),
        "session_handle_token": dbus.String("ses1"),
    })

    try:
        loop.run()
    finally:
        cleanup()
    return process["gst"].returncode if process["gst"] else 1
