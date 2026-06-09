
"""
Streamer_sway_usb.py — Sway Wayland version.
Uses org.freedesktop.portal.ScreenCast (via xdg-desktop-portal-wlr).
Virtual monitor must be created first with:
  swaymsg create_output
  wlr-randr --output HEADLESS-1 --custom-mode 2560x1600@60

Usage: python3 Streamer_sway_usb.py <width> <height> <fps>
"""
import dbus, sys, signal, subprocess, threading
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pipeline_builder import detect_igpu_encoder, launch_with_fallback

WIDTH   = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
HEIGHT  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
FPS     = int(sys.argv[3]) if len(sys.argv) > 3 else 60
BITRATE = int(sys.argv[4]) if len(sys.argv) > 4 else 8000
MODE    = sys.argv[5] if len(sys.argv) > 5 else "usb"

server_mode = (MODE == "wifi")
host = "0.0.0.0" if server_mode else "127.0.0.1"

PORT    = 7110

print(f"[Streamer Sway USB] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}")


HW_ENCODER = detect_igpu_encoder()

DBusGMainLoop(set_as_default=True)
loop     = GLib.MainLoop()
bus      = dbus.SessionBus()
desktop  = bus.get_object("org.freedesktop.portal.Desktop",
                           "/org/freedesktop/portal/desktop")
sc       = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state    = {"step": "create_session", "session": None}
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize Sway] Shutting down...")
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate()
        try:    gst_proc.wait(timeout=3)
        except subprocess.TimeoutExpired: gst_proc.kill()
    if loop.is_running():
        loop.quit()
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

def launch_streaming(fd, node_id):
    global gst_proc
    print("[Monitorize Sway] Streaming. Ctrl+C to stop.\n")

    gst_proc = launch_with_fallback(
        pw_fd=fd, node_id=node_id,
        width=WIDTH, height=HEIGHT, fps=FPS, bitrate=BITRATE, port=PORT,
        hw_encoder=HW_ENCODER, pass_fds=(fd,),
        host=host, server_mode=server_mode,
    )

def on_response(response, results, **kw):
    if response != 0:
        print(f"[ERROR] Portal denied (code {response})")
        loop.quit()
        return

    step = state["step"]

    if step == "create_session":
        state["session"] = str(results["session_handle"])
        state["step"]    = "select_sources"
        sc.SelectSources(state["session"], {
            "types":        dbus.UInt32(1),
            "multiple":     dbus.Boolean(False),
            "cursor_mode":  dbus.UInt32(2),
            "handle_token": dbus.String("tok2"),
        })

    elif step == "select_sources":
        state["step"] = "start"
        sc.Start(state["session"], "", {"handle_token": dbus.String("tok3")})

    elif step == "start":
        streams = results.get("streams", [])
        if not streams:
            print("[ERROR] No streams from portal.")
            loop.quit()
            return

        node_id = int(streams[0][0])
        fd_obj  = sc.OpenPipeWireRemote(state["session"], {})
        fd      = fd_obj.take()
        print(f"[Portal] Got PipeWire node={node_id} fd={fd}")

        t = threading.Thread(target=launch_streaming, args=(fd, node_id), daemon=True)
        t.start()

bus.add_signal_receiver(on_response, signal_name="Response",
                        dbus_interface="org.freedesktop.portal.Request")

print("[Portal] Creating session... xdg-desktop-portal-wlr will show a picker.")
print("         Select 'HEADLESS-1' in the picker.\n")

sc.CreateSession({
    "handle_token":         dbus.String("tok1"),
    "session_handle_token": dbus.String("ses1"),
})

loop.run()
