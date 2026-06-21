
"""
Streamer_gnome.py — GNOME Wayland streamer.
Uses org.gnome.Mutter.ScreenCast RecordVirtual D-Bus API.
Handles both USB and Wi-Fi modes via the MODE argument.

Usage: python3 Streamer_gnome.py <width> <height> <fps> <bitrate> <usb|wifi> [scale] [Extend|Mirror]
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
SCALE   = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
TYPE    = sys.argv[7] if len(sys.argv) > 7 else "Extend"

server_mode = (MODE == "wifi")
host = os.environ.get("MONITORIZE_HOST", "0.0.0.0" if server_mode else "127.0.0.1")

PORT = int(os.environ.get("MONITORIZE_PORT", 7110 if server_mode else 7112))

print(f"[Streamer GNOME] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}  Mode={MODE}")


HW_ENCODER = get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu"))

DBusGMainLoop(set_as_default=True)
loop     = GLib.MainLoop()
bus      = dbus.SessionBus()
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize GNOME] Shutting down...")
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate()
        try:    gst_proc.wait(timeout=3)
        except subprocess.TimeoutExpired: gst_proc.kill()
    if loop.is_running():
        loop.quit()
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

def launch_streaming(node_id):
    global gst_proc
    print(f"[Monitorize GNOME] Streaming ({MODE} mode). Ctrl+C to stop.\n")

    gst_proc = launch_with_fallback(
        pw_fd=None, node_id=node_id,
        width=WIDTH, height=HEIGHT, fps=FPS, bitrate=BITRATE, port=PORT,
        hw_encoder=HW_ENCODER,
        host=host, server_mode=server_mode,
    )

def get_primary_connector():
    try:
        obj = bus.get_object('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig')
        display_config = dbus.Interface(obj, 'org.gnome.Mutter.DisplayConfig')
        serial, physical_monitors, logical_monitors, properties = display_config.GetCurrentState()
        for lm in logical_monitors:
            is_primary = bool(lm[4])
            monitors_list = lm[5]
            if is_primary and monitors_list:
                return str(monitors_list[0][0])
    except Exception as e:
        print(f"[Mutter] Failed to get primary connector via D-Bus: {e}")
    return "eDP-1"

def start_virtual_session():
    mutter_sc_obj = bus.get_object(
        "org.gnome.Mutter.ScreenCast",
        "/org/gnome/Mutter/ScreenCast"
    )
    mutter_sc = dbus.Interface(mutter_sc_obj, "org.gnome.Mutter.ScreenCast")

    session_path = mutter_sc.CreateSession({})
    print(f"[Mutter] Session: {session_path}")

    session_obj = bus.get_object("org.gnome.Mutter.ScreenCast", session_path)
    session     = dbus.Interface(session_obj, "org.gnome.Mutter.ScreenCast.Session")

    if TYPE.lower() == "mirror":
        primary_conn = get_primary_connector()
        print(f"[Mutter] Mirroring primary monitor: {primary_conn}")
        stream_path = session.RecordMonitor(
            primary_conn,
            {
                "cursor-mode": dbus.UInt32(1),
            }
        )
    else:
        print(f"[Mutter] Creating virtual monitor at 0,0 resolution {WIDTH}x{HEIGHT}")
        stream_path = session.RecordVirtual({
            "size":         dbus.Struct(
                                [dbus.Int32(WIDTH), dbus.Int32(HEIGHT)],
                                signature="ii"),
            "position":     dbus.Struct(
                                [dbus.Int32(0), dbus.Int32(0)],
                                signature="ii"),
            "refresh-rate": dbus.UInt32(FPS * 1000),
            "cursor-mode":  dbus.UInt32(1),
        })
    print(f"[Mutter] Stream: {stream_path}")

    def on_pipewire_stream_added(node_id):
        print(f"[Mutter] PipeWireStreamAdded — node_id={node_id}")
        t = threading.Thread(
            target=launch_streaming,
            args=(int(node_id),),
            daemon=True
        )
        t.start()

    stream_obj = bus.get_object("org.gnome.Mutter.ScreenCast", stream_path)
    stream_obj.connect_to_signal(
        "PipeWireStreamAdded",
        on_pipewire_stream_added,
        dbus_interface="org.gnome.Mutter.ScreenCast.Stream"
    )

    session.Start()
    print("[Mutter] Session started — waiting for PipeWireStreamAdded signal...")

start_virtual_session()
loop.run()
