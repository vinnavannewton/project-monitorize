#!/usr/bin/env python3
"""
Streamer_hyprland_usb.py — Hyprland Wayland version.
Uses org.freedesktop.portal.ScreenCast (via xdg-desktop-portal-hyprland).
Virtual monitor must be created first with:
  hyprctl output create headless Virtual-1
  hyprctl keyword monitor Virtual-1,2560x1600@60,auto,1

Usage: python3 Streamer_hyprland_usb.py <width> <height> <fps>
"""
import dbus, sys, signal, subprocess, threading
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

WIDTH   = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
HEIGHT  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
FPS     = int(sys.argv[3]) if len(sys.argv) > 3 else 60

PORT    = 7110
BITRATE = 8000

print(f"[Streamer Hyprland USB] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}")

DBusGMainLoop(set_as_default=True)
loop     = GLib.MainLoop()
bus      = dbus.SessionBus()
desktop  = bus.get_object("org.freedesktop.portal.Desktop",
                           "/org/freedesktop/portal/desktop")
sc       = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state    = {"step": "create_session", "session": None}
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize Hyprland] Shutting down...")
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
    sink = f"tcpclientsink host=127.0.0.1 port={PORT} sync=false"
    # videorate without drop-only — duplicates last frame during sparse
    # Wayland updates (typing, inactive virtual display) for smooth output
    pipeline = (
        f"gst-launch-1.0 -e -v "
        f"pipewiresrc fd={fd} path={node_id} do-timestamp=true always-copy=true ! "
        f"videorate ! video/x-raw,framerate={FPS}/1 ! "
        f"queue max-size-buffers=1 leaky=downstream ! "
        f"videoconvert n-threads=4 ! videoscale ! "
        f"video/x-raw,format=I420,width={WIDTH},height={HEIGHT} ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={BITRATE} "
        f"key-int-max=30 byte-stream=true "
        f"option-string=\"bframes=0:ref=1:sliced-threads=0:"
        f"rc-lookahead=0:sync-lookahead=0:threads=4:"
        f"vbv-bufsize=1000:vbv-maxrate={BITRATE}\" ! "
        f"h264parse config-interval=-1 ! "
        f"video/x-h264,stream-format=byte-stream,alignment=au ! "
        f"{sink}"
    )
    print(f"\n[GStreamer] {pipeline}\n")
    gst_proc = subprocess.Popen(pipeline, shell=True, pass_fds=(fd,))
    gst_proc.wait()

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

print("[Portal] Creating session... Hyprland will ask you to select a monitor.")
print("         Select 'Virtual-1' in the picker.\n")

sc.CreateSession({
    "handle_token":         dbus.String("tok1"),
    "session_handle_token": dbus.String("ses1"),
})

loop.run()
