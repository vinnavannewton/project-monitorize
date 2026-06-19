
"""
Streamer_kde.py — KDE Plasma PipeWire → H.264 → TCP streamer.
Handles both USB and Wi-Fi modes via the MODE argument.

Usage (from GUI):   python3 Streamer_kde.py <width> <height> <fps> <bitrate> <usb|wifi>
Usage (standalone): python3 Streamer_kde.py          (uses defaults)
"""
import dbus, sys, signal, subprocess, threading, os
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pipeline_builder import get_encoder, launch_with_fallback


WIDTH   = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
HEIGHT  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
FPS     = int(sys.argv[3]) if len(sys.argv) > 3 else 60
BITRATE = int(sys.argv[4]) if len(sys.argv) > 4 else 8000
MODE    = sys.argv[5] if len(sys.argv) > 5 else "usb"
PORT_OVERRIDE = int(sys.argv[6]) if len(sys.argv) > 6 else None

server_mode = (MODE == "wifi")
host = os.environ.get("MONITORIZE_HOST", "0.0.0.0" if server_mode else "127.0.0.1")

PORT = int(os.environ.get(
    "MONITORIZE_PORT",
    PORT_OVERRIDE if PORT_OVERRIDE else (7110 if server_mode else 7112),
))

print(f"[Streamer KDE] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}  Mode={MODE}  Port={PORT}")


HW_ENCODER = get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu"))

DBusGMainLoop(set_as_default=True)
loop    = GLib.MainLoop()
bus     = dbus.SessionBus()
desktop = bus.get_object("org.freedesktop.portal.Desktop",
                         "/org/freedesktop/portal/desktop")
sc      = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state   = {"step": "create_session", "session": None}
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize KDE] Shutting down...")
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
    print(f"[Monitorize KDE] Streaming ({MODE} mode). Ctrl+C to stop.\n")

    gst_proc = launch_with_fallback(
        pw_fd=fd, node_id=node_id,
        width=WIDTH, height=HEIGHT, fps=FPS, bitrate=BITRATE, port=PORT,
        hw_encoder=HW_ENCODER, pass_fds=(fd,),
        host=host, server_mode=server_mode,
    )
    code = gst_proc.wait()
    print(f"[GStreamer] EXITED: {code}", flush=True)
    GLib.idle_add(loop.quit)

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

print("[Portal] Creating session... KDE will ask you to select a monitor.")
print("         Select 'TabletDisplay' in the picker.\n")

sc.CreateSession({
    "handle_token":         dbus.String("tok1"),
    "session_handle_token": dbus.String("ses1"),
})

loop.run()
