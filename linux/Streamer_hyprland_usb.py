
"""
Streamer_hyprland_usb.py — Hyprland Wayland version.
Uses org.freedesktop.portal.ScreenCast (via xdg-desktop-portal-hyprland).
Virtual monitor must be created first with:
  hyprctl output create headless

Usage: python3 Streamer_hyprland_usb.py <width> <height> <fps>
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

print(f"[Streamer Hyprland USB] Resolution={WIDTH}x{HEIGHT}  FPS={FPS}  Bitrate={BITRATE}")


HW_ENCODER = detect_igpu_encoder()

def get_current_headless_monitors():
    import subprocess, re
    try:
        res = subprocess.run(["hyprctl", "monitors", "all"], capture_output=True, text=True)
        if res.returncode == 0:
            return set(re.findall(r"\bHEADLESS-\d+\b", res.stdout))
    except Exception:
        pass
    return set()


headless_arg = sys.argv[6] if len(sys.argv) > 6 else None
created_monitor = None

if headless_arg and headless_arg != "mirror":
    created_monitor = headless_arg
    print(f"[Hyprland] Using headless monitor from GUI: {created_monitor}")
elif headless_arg == "mirror":
    print("[Hyprland] Mirror mode: using primary monitor, no virtual display spawned.")
else:
    
    print("[Hyprland] Standalone mode: Creating virtual monitor...")
    old_mons = get_current_headless_monitors()
    subprocess.run(["hyprctl", "output", "create", "headless"], capture_output=True)
    new_mons = get_current_headless_monitors()
    diff = new_mons - old_mons
    if diff:
        created_monitor = list(diff)[0]
    else:
        created_monitor = "HEADLESS-1"
    print(f"[Hyprland] Created virtual monitor: {created_monitor}")

DBusGMainLoop(set_as_default=True)
loop     = GLib.MainLoop()
bus      = dbus.SessionBus()
desktop  = bus.get_object("org.freedesktop.portal.Desktop",
                           "/org/freedesktop/portal/desktop")
sc       = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state    = {"step": "create_session", "session": None}
gst_proc = None

cleaning_up = False

def cleanup(sig=None, frame=None):
    global created_monitor, cleaning_up
    if cleaning_up:
        return
    cleaning_up = True
    print("\n[Monitorize Hyprland] Shutting down...")
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate()
        try:    gst_proc.wait(timeout=3)
        except subprocess.TimeoutExpired: gst_proc.kill()

    if created_monitor:
        print(f"[Hyprland] Removing created headless monitor: {created_monitor}")
        subprocess.run(["hyprctl", "output", "remove", created_monitor], capture_output=True)
        created_monitor = None

    if loop.is_running():
        loop.quit()
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

def launch_streaming(fd, node_id):
    global gst_proc
    print("[Monitorize Hyprland] Streaming. Ctrl+C to stop.\n")

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

print("[Portal] Creating session... Hyprland will ask you to select a monitor.")
if headless_arg == "mirror":
    print("         Select your primary monitor in the picker.\n")
else:
    print("         Select 'HEADLESS' in the picker.\n")

sc.CreateSession({
    "handle_token":         dbus.String("tok1"),
    "session_handle_token": dbus.String("ses1"),
})

try:
    loop.run()
finally:
    cleanup()
