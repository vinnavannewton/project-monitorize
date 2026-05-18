#!/usr/bin/env python3
import dbus
import sys
import signal
import subprocess
import threading
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

# Wi‑Fi‑tuned parameters
PORT    = 7110
WIDTH   = 2560   # keep in sync with your virtual monitor + Android
HEIGHT  = 1600
FPS     = 60
BITRATE = 8000   # lower than USB, more stable on Wi‑Fi

DBusGMainLoop(set_as_default=True)
loop    = GLib.MainLoop()
bus     = dbus.SessionBus()
desktop = bus.get_object(
    "org.freedesktop.portal.Desktop",
    "/org/freedesktop/portal/desktop"
)
sc      = dbus.Interface(desktop, "org.freedesktop.portal.ScreenCast")
state   = {"step": "create_session", "session": None}
gst_proc = None

def cleanup(sig=None, frame=None):
    print("\n[Monitorize Wi‑Fi] Shutting down...")
    global gst_proc
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate()
        try:
            gst_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            gst_proc.kill()
    if loop.is_running():
        loop.quit()
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

def launch_streaming(fd, node_id):
    """
    Wi‑Fi profile:
    - Lower bitrate for wireless stability.
    - More frequent keyframes (short GOP) so corruption heals quickly.
    - Still CPU x264enc because that felt most responsive on your setup.
    """
    global gst_proc

    # For Wi‑Fi we still use TCP, just over adb tcpip tunnel instead of USB.
    sink = f"tcpclientsink host=127.0.0.1 port={PORT} sync=false"

    pipeline = (
        f"gst-launch-1.0 -e -v "
        f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=16 ! "
        f"videorate skip-to-first=true ! "
        f"video/x-raw,framerate={FPS}/1 ! "
        f"queue max-size-buffers=1 leaky=downstream ! "
        f"videoconvert ! video/x-raw,format=I420 ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast "
        f"bitrate={BITRATE} vbv-bufsize=1000 vbv-maxrate={BITRATE} key-int-max=15 bframes=0 byte-stream=true ! "
        f"h264parse config-interval=-1 ! "
        f"video/x-h264,stream-format=byte-stream,alignment=au ! "
        f"{sink}"
    )

    print(f"\n[GStreamer Wi‑Fi] {pipeline}\n")
    print("[Monitorize Wi‑Fi] Streaming over Wi‑Fi. Ctrl+C to stop.\n")

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
            "types":        dbus.UInt32(1),   # monitor
            "multiple":     dbus.Boolean(False),
            "cursor_mode":  dbus.UInt32(2),   # embedded cursor
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

        t = threading.Thread(
            target=launch_streaming,
            args=(fd, node_id),
            daemon=True,
        )
        t.start()
        # Keep loop running to keep portal session alive

bus.add_signal_receiver(
    on_response,
    signal_name="Response",
    dbus_interface="org.freedesktop.portal.Request",
)

print("[Portal Wi‑Fi] Creating session... KDE will ask you to select a monitor.")
print("              Select 'TabletDisplay' in the picker.\n")

sc.CreateSession({
    "handle_token":         dbus.String("wifi_tok1"),
    "session_handle_token": dbus.String("wifi_ses1"),
})

loop.run()
