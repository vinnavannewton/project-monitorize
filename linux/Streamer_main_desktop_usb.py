#!/usr/bin/env python3
"""
Streamer_main_desktop_usb.py — Main Desktop Streamer with Touch Injection.
Streams the main desktop instead of creating a virtual display, and applies
Android touch input directly to the main desktop using the existing snegg daemon.
"""
import dbus, sys, signal, subprocess, threading, os
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pipeline_builder import detect_igpu_encoder, launch_with_fallback

# ---- Parse optional CLI arguments ----
WIDTH   = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
HEIGHT  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
FPS     = int(sys.argv[3]) if len(sys.argv) > 3 else 60
BITRATE = int(sys.argv[4]) if len(sys.argv) > 4 else 36000

PORT    = 7110

print(f"[Main Streamer] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}")
print(f"[Main Streamer] Video Bitrate={BITRATE}kbps")

# ---- ADB Port Forwarding ----
print("[Main Streamer] Setting up ADB port forwarding...")
try:
    # Cleanup any old forwards that might hog port 7111 on Linux
    subprocess.run(["adb", "forward", "--remove", "tcp:7111"], capture_output=True)
    
    # Video: Linux (GStreamer) connects to Android → adb forward
    subprocess.run(["adb", "forward", "tcp:7110", "tcp:7110"], check=False)
    # Touch: Android (InputEventSender) connects to Linux → adb reverse
    subprocess.run(["adb", "reverse", "tcp:7111", "tcp:7111"], check=False)
    print("[ADB] adb forward 7110 + adb reverse 7111 set up")
except Exception as e:
    print(f"[ERROR] ADB setup failed: {e}")


# ---- Kill any stale touch_daemon instances so port 7111 is free ----
subprocess.run(["pkill", "-f", "touch_daemon.py"], capture_output=True)
import time as _time; _time.sleep(0.5)  # let the port release

# ---- Launch touch_daemon.py in the background ----
script_dir = os.path.dirname(os.path.abspath(__file__))
touch_daemon_path = os.path.join(script_dir, "touch_daemon.py")
print(f"[Main Streamer] Starting touch daemon from {touch_daemon_path}...")
touch_proc = subprocess.Popen(["python3", touch_daemon_path, str(WIDTH), str(HEIGHT)])

# Detect iGPU HW encoder once at startup
HW_ENCODER = detect_igpu_encoder()

DBusGMainLoop(set_as_default=True)
loop    = GLib.MainLoop()
bus     = dbus.SessionBus()
desktop = bus.get_object("org.freedesktop.portal.Desktop",
                         "/org/freedesktop/portal/desktop")
sc      = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state   = {"step": "create_session", "session": None}
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize] Shutting down...")
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate()
        try:    gst_proc.wait(timeout=3)
        except subprocess.TimeoutExpired: gst_proc.kill()
    if touch_proc and touch_proc.poll() is None:
        touch_proc.terminate()
        try:    touch_proc.wait(timeout=3)
        except subprocess.TimeoutExpired: touch_proc.kill()
    if loop.is_running():
        loop.quit()
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

def launch_streaming(fd, node_id):
    global gst_proc
    print("[Monitorize] Streaming. Ctrl+C to stop.\n")

    gst_proc = launch_with_fallback(
        pw_fd=fd, node_id=node_id,
        width=WIDTH, height=HEIGHT, fps=FPS, bitrate=BITRATE, port=PORT,
        hw_encoder=HW_ENCODER, pass_fds=(fd,),
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
        # DO NOT call loop.quit() here — keeps portal session alive

bus.add_signal_receiver(on_response, signal_name="Response",
                        dbus_interface="org.freedesktop.portal.Request")

print("\n[Portal] Creating session... KDE will ask you to select a monitor.")
print("         => Select your MAIN DESKTOP / MAIN DISPLAY in the picker.")
print("         => ALSO, watch for the 'Allow Remote Control' popup for touch input and click Allow.\n")

sc.CreateSession({
    "handle_token":         dbus.String("tok1"),
    "session_handle_token": dbus.String("ses1"),
})

loop.run()
