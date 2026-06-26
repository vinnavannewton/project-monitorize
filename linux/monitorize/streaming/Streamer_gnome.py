"""
Streamer_gnome.py — GNOME Wayland streamer.
Uses org.gnome.Mutter.ScreenCast RecordVirtual D-Bus API.
Handles both USB and Wi-Fi modes via the MODE argument.

Usage: python3 -m monitorize.streaming.Streamer_gnome <width> <height> <fps> <bitrate> <usb|wifi> [scale] [Extend|Mirror] [x] [y]
"""

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass

from monitorize.platform import gnome_virtual_monitor
from monitorize.streaming.pipeline_builder import get_encoder, launch_with_fallback


@dataclass
class StreamerConfig:
    width: int = 2560
    height: int = 1600
    fps: int = 60
    bitrate: int = 8000
    mode: str = "usb"
    scale: float = 1.0
    display_type: str = "Extend"
    virtual_position: tuple[int, int] | None = None


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    config = StreamerConfig(
        width=int(argv[0]) if len(argv) > 0 else 2560,
        height=int(argv[1]) if len(argv) > 1 else 1600,
        fps=int(argv[2]) if len(argv) > 2 else 60,
        bitrate=int(argv[3]) if len(argv) > 3 else 8000,
        mode=argv[4] if len(argv) > 4 else "usb",
        scale=float(argv[5]) if len(argv) > 5 else 1.0,
        display_type=argv[6] if len(argv) > 6 else "Extend",
    )
    if len(argv) > 8:
        try:
            config.virtual_position = (int(argv[7]), int(argv[8]))
        except (TypeError, ValueError):
            config.virtual_position = None
    return config


def _virtual_mode(dbus, config):
    mode = {
        "size": dbus.Struct(
            [dbus.Int32(config.width), dbus.Int32(config.height)],
            signature="ii",
        ),
        "refresh-rate": dbus.Double(float(config.fps)),
        "is-preferred": dbus.Boolean(True),
    }
    if hasattr(dbus, "Dictionary"):
        return dbus.Dictionary(mode, signature="sv")
    return mode


def _record_virtual(session, dbus, config):
    print(
        f"[Mutter] Creating virtual monitor resolution "
        f"{config.width}x{config.height}@{config.fps}"
    )
    return session.RecordVirtual({
        "modes": dbus.Array([_virtual_mode(dbus, config)], signature="a{sv}")
        if hasattr(dbus, "Array") else [_virtual_mode(dbus, config)],
        "cursor-mode": dbus.UInt32(1),
    })


def get_primary_connector(bus, dbus=None):
    dbus = dbus or _dbus()
    try:
        obj = bus.get_object(
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
        )
        display_config = dbus.Interface(obj, "org.gnome.Mutter.DisplayConfig")
        _serial, _physical_monitors, logical_monitors, _properties = (
            display_config.GetCurrentState()
        )
        for lm in logical_monitors:
            is_primary = bool(lm[4])
            monitors_list = lm[5]
            if is_primary and monitors_list:
                return str(monitors_list[0][0])
    except Exception as e:
        print(f"[Mutter] Failed to get primary connector via D-Bus: {e}")
    return "eDP-1"


def _dbus():
    import dbus

    return dbus


def _restore_virtual_layout(bus, dbus, config):
    if config.display_type.lower() == "mirror":
        return False
    return gnome_virtual_monitor.restore_virtual_layout(
        position=config.virtual_position,
        display_config=gnome_virtual_monitor.display_config_interface(bus, dbus),
        dbus=dbus,
    )


def _restore_and_launch(bus, dbus, config, launch_streaming, node_id):
    try:
        _restore_virtual_layout(bus, dbus, config)
    except Exception as exc:
        print(f"[Mutter] Virtual monitor layout restore skipped: {exc}")
    t = threading.Thread(
        target=launch_streaming,
        args=(int(node_id),),
        daemon=True,
    )
    t.start()


def _start_virtual_session(bus, dbus, config, launch_streaming):
    mutter_sc_obj = bus.get_object(
        "org.gnome.Mutter.ScreenCast",
        "/org/gnome/Mutter/ScreenCast",
    )
    mutter_sc = dbus.Interface(mutter_sc_obj, "org.gnome.Mutter.ScreenCast")

    session_path = mutter_sc.CreateSession({})
    print(f"[Mutter] Session: {session_path}")

    session_obj = bus.get_object("org.gnome.Mutter.ScreenCast", session_path)
    session = dbus.Interface(session_obj, "org.gnome.Mutter.ScreenCast.Session")

    if config.display_type.lower() == "mirror":
        primary_conn = get_primary_connector(bus, dbus)
        print(f"[Mutter] Mirroring primary monitor: {primary_conn}")
        stream_path = session.RecordMonitor(
            primary_conn,
            {
                "cursor-mode": dbus.UInt32(1),
            },
        )
    else:
        stream_path = _record_virtual(session, dbus, config)
    print(f"[Mutter] Stream: {stream_path}")

    def on_pipewire_stream_added(node_id):
        print(f"[Mutter] PipeWireStreamAdded — node_id={node_id}")
        _restore_and_launch(bus, dbus, config, launch_streaming, node_id)

    stream_obj = bus.get_object("org.gnome.Mutter.ScreenCast", stream_path)
    stream_obj.connect_to_signal(
        "PipeWireStreamAdded",
        on_pipewire_stream_added,
        dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
    )

    session.Start()
    print("[Mutter] Session started — waiting for PipeWireStreamAdded signal...")


def main(argv=None):
    dbus = _dbus()
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    config = parse_args(argv)
    server_mode = config.mode == "wifi"
    host = os.environ.get(
        "MONITORIZE_HOST",
        "0.0.0.0" if server_mode else "127.0.0.1",
    )
    port = int(os.environ.get("MONITORIZE_PORT", 7110 if server_mode else 7112))
    hw_encoder = get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu"))

    print(
        f"[Streamer GNOME] Resolution={config.width}x{config.height}  "
        f"FPS={config.fps}  Bitrate={config.bitrate}  Mode={config.mode}"
    )

    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bus = dbus.SessionBus()
    gst_proc = None

    def cleanup(_sig=None, _frame=None):
        print("\n[Monitorize GNOME] Shutting down...")
        if gst_proc and gst_proc.poll() is None:
            gst_proc.terminate()
            try:
                gst_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                gst_proc.kill()
        if loop.is_running():
            loop.quit()
        sys.exit(0)

    def launch_streaming(node_id):
        nonlocal gst_proc
        print(f"[Monitorize GNOME] Streaming ({config.mode} mode). Ctrl+C to stop.\n")

        gst_proc = launch_with_fallback(
            pw_fd=None,
            node_id=node_id,
            width=config.width,
            height=config.height,
            fps=config.fps,
            bitrate=config.bitrate,
            port=port,
            hw_encoder=hw_encoder,
            host=host,
            server_mode=server_mode,
        )

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    _start_virtual_session(bus, dbus, config, launch_streaming)
    loop.run()


if __name__ == "__main__":
    main()
