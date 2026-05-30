#!/usr/bin/env python3
"""
Streamer_gnome_usb.py — GNOME Wayland version.
Uses org.gnome.Mutter.ScreenCast RecordVirtual D-Bus API.
PipeWire node ID arrives via PipeWireStreamAdded signal, not a method call.
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

print(f"[Streamer GNOME USB] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}")

# Detect iGPU HW encoder once at startup
HW_ENCODER = detect_igpu_encoder()

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
    print("[Monitorize GNOME] Streaming. Ctrl+C to stop.\n")

    gst_proc = launch_with_fallback(
        pw_fd=None, node_id=node_id,
        width=WIDTH, height=HEIGHT, fps=FPS, bitrate=BITRATE, port=PORT,
        hw_encoder=HW_ENCODER,
        host=host, server_mode=server_mode,
    )

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

    stream_path = session.RecordVirtual({
        "size":         dbus.Struct(
                            [dbus.Int32(WIDTH), dbus.Int32(HEIGHT)],
                            signature="ii"),
        "position":     dbus.Struct(
                            [dbus.Int32(0), dbus.Int32(0)],
                            signature="ii"),
        "refresh-rate": dbus.UInt32(FPS * 1000),
    })
    print(f"[Mutter] Stream: {stream_path}")

    # ---- THIS is where the indentation bug was ----
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
