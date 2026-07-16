"""Native GNOME/Mutter virtual display streamer."""

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass

from monitorize.platform import gnome_virtual_monitor
from monitorize.streaming.pipeline_builder import get_encoder, launch_with_fallback


READY_ATTEMPTS = 30
READY_INTERVAL_MS = 100


@dataclass
class StreamerConfig:
    width: int = 2560
    height: int = 1600
    fps: int = 60
    bitrate: int = 8000
    mode: str = "usb"
    display_type: str = "Extend"
    preferred_scale: float | None = None
    slot: str = "primary"


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    return StreamerConfig(
        width=int(argv[0]) if len(argv) > 0 else 2560,
        height=int(argv[1]) if len(argv) > 1 else 1600,
        fps=int(argv[2]) if len(argv) > 2 else 60,
        bitrate=int(argv[3]) if len(argv) > 3 else 8000,
        mode=argv[4] if len(argv) > 4 else "usb",
        display_type=argv[5] if len(argv) > 5 else "Extend",
        slot=os.environ.get("MONITORIZE_GNOME_VIRTUAL_SLOT", "primary"),
    )


def _dbus():
    import dbus
    return dbus


def _event(kind, **values):
    print("MONITORIZE_EVENT " + json.dumps(
        {"type": kind, **values}, separators=(",", ":")
    ), flush=True)


def _virtual_mode(dbus, config):
    values = {
        "size": dbus.Struct([dbus.UInt32(config.width), dbus.UInt32(config.height)], signature="uu"),
        "refresh-rate": dbus.Double(float(config.fps)),
        "is-preferred": dbus.Boolean(True),
    }
    if config.preferred_scale:
        values["preferred-scale"] = dbus.Double(float(config.preferred_scale))
    return dbus.Dictionary(values, signature="sv") if hasattr(dbus, "Dictionary") else values


def _record_virtual(session, dbus, config):
    print(f"[Mutter] Creating {config.slot} virtual display {config.width}x{config.height}@{config.fps}")
    modes = dbus.Array([_virtual_mode(dbus, config)], signature="a{sv}") if hasattr(dbus, "Array") else [_virtual_mode(dbus, config)]
    return session.RecordVirtual({
        "modes": modes,
        "cursor-mode": dbus.UInt32(1),
        "is-platform": dbus.Boolean(True),
    })


def get_primary_connector(bus, dbus=None):
    dbus = dbus or _dbus()
    obj = bus.get_object("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig")
    _serial, _physical, logical, _props = dbus.Interface(
        obj, "org.gnome.Mutter.DisplayConfig"
    ).GetCurrentState()
    for monitor in logical:
        if bool(monitor[4]) and monitor[5]:
            return str(monitor[5][0][0])
    raise RuntimeError("Mutter did not report a primary monitor")


def _restore_virtual_layout(bus, dbus, config):
    if config.display_type.lower() == "mirror":
        return False
    return gnome_virtual_monitor.restore_virtual_layout(
        slot=config.slot,
        display_config=gnome_virtual_monitor.display_config_interface(bus, dbus),
        dbus=dbus,
    )


def _restore_and_launch(bus, dbus, config, launch_streaming, node_id):
    """Legacy helper retained for callers; normal streaming uses MutterStream."""
    try:
        _restore_virtual_layout(bus, dbus, config)
    except Exception as exc:
        print(f"[Mutter] Layout restore skipped: {exc}")
    return launch_streaming(int(node_id))


class MutterStream:
    """One owned ScreenCast session; retries only after its output is gone."""

    def __init__(self, bus, dbus, glib, config, launch_streaming):
        self.bus, self.dbus, self.glib = bus, dbus, glib
        self.config, self.launch_streaming = config, launch_streaming
        self.display = gnome_virtual_monitor.display_config_interface(bus, dbus)
        self.session = self.stream = self.gst_proc = None
        self.before = set()
        self.connector = ""
        self.node_id = 0
        self.attempt = 0
        self.stopping = False
        self.finished = False
        self.exit_code = 0

    @property
    def extend(self):
        return self.config.display_type.lower() != "mirror"

    def start(self):
        self.attempt += 1
        self.connector = ""
        self.node_id = 0
        try:
            if self.extend:
                self.before = set(gnome_virtual_monitor.virtual_connectors_from_state(
                    self.display.GetCurrentState()
                ))
            screen_cast = self.dbus.Interface(
                self.bus.get_object("org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast"),
                "org.gnome.Mutter.ScreenCast",
            )
            path = screen_cast.CreateSession({})
            self.session = self.dbus.Interface(
                self.bus.get_object("org.gnome.Mutter.ScreenCast", path),
                "org.gnome.Mutter.ScreenCast.Session",
            )
            if self.extend:
                stream_path = _record_virtual(self.session, self.dbus, self.config)
            else:
                self.connector = get_primary_connector(self.bus, self.dbus)
                stream_path = self.session.RecordMonitor(self.connector, {"cursor-mode": self.dbus.UInt32(1)})
            self.stream = self.bus.get_object("org.gnome.Mutter.ScreenCast", stream_path)
            self.stream.connect_to_signal(
                "PipeWireStreamAdded", self._pipewire_added,
                dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
            )
            self.session.connect_to_signal("Closed", self._closed)
            self.session.Start()
            print("[Mutter] Session started; waiting for PipeWire…")
        except Exception as exc:
            self._failed(f"Mutter setup failed: {exc}")

    def _closed(self):
        if not self.stopping and not self.finished:
            self._failed("Mutter closed the ScreenCast session")

    def _pipewire_added(self, node_id):
        if self.finished or self.stopping:
            return
        self.node_id = int(node_id)
        self._wait_for_output(0)

    def _wait_for_output(self, tries):
        if self.finished or self.stopping:
            return False
        try:
            state = self.display.GetCurrentState()
            if self.extend:
                connector = gnome_virtual_monitor.new_virtual_connector(
                    state, self.before, self.config.width, self.config.height
                )
                if connector:
                    self.connector = connector
            if self.connector:
                return self._ready(state)
        except Exception as exc:
            if tries + 1 >= READY_ATTEMPTS:
                self._failed(f"Could not inspect Mutter output: {exc}")
                return False
        if tries + 1 >= READY_ATTEMPTS:
            self._failed("Mutter did not expose one matching virtual output")
            return False
        self.glib.timeout_add(READY_INTERVAL_MS, self._wait_for_output, tries + 1)
        return False

    def _ready(self, state):
        info = gnome_virtual_monitor.monitor_info_from_state(state, self.connector)
        if not info:
            self._failed("Mutter output disappeared before capture was ready")
            return False
        if self.extend:
            roles = {self.config.slot: self.connector}
            primary = os.environ.get("MONITORIZE_GNOME_PRIMARY_OUTPUT", "")
            if primary:
                roles["primary"] = primary
            topology = "+".join(role for role in ("primary", "additional") if role in roles)
            try:
                restored = gnome_virtual_monitor.restore_virtual_layout(
                    slot=topology, display_config=self.display, dbus=self.dbus,
                    attempts=1, delay=0, role_connectors=roles,
                )
                if restored:
                    state = self.display.GetCurrentState()
                    info = gnome_virtual_monitor.monitor_info_from_state(state, self.connector) or info
            except Exception as exc:
                print(f"[Mutter] Layout restore skipped: {exc}")
        _event("gnome_output_ready", slot=self.config.slot, **info)
        _event("gnome_capture_ready", slot=self.config.slot, connector=self.connector, node_id=self.node_id)
        self.gst_proc = self.launch_streaming(self.node_id)
        self.glib.timeout_add(250, self._watch_gstreamer)
        return False

    def _watch_gstreamer(self):
        if self.finished or self.stopping or not self.gst_proc:
            return False
        code = self.gst_proc.poll()
        if code is None:
            return True
        self._failed(f"GStreamer exited with code {code}")
        return False

    def _failed(self, reason):
        if self.finished or self.stopping:
            return
        if self.attempt >= 2:
            _event("gnome_error", slot=self.config.slot, message=reason)
            self.finish(1)
            return
        _event("gnome_retry", slot=self.config.slot, attempt=self.attempt, message=reason)
        self._stop_then_retry(0)

    def _stop_then_retry(self, tries):
        self.stopping = True
        if self.gst_proc and self.gst_proc.poll() is None:
            self.gst_proc.terminate()
        try:
            if self.session:
                self.session.Stop()
        except Exception:
            pass
        try:
            remaining = set(gnome_virtual_monitor.virtual_connectors_from_state(
                self.display.GetCurrentState()
            ))
        except Exception:
            remaining = set()
        if not self.connector or self.connector not in remaining:
            self.session = self.stream = self.gst_proc = None
            self.stopping = False
            self.glib.timeout_add(250, self._restart)
            return False
        if tries + 1 >= READY_ATTEMPTS:
            _event("gnome_error", slot=self.config.slot, message="Mutter did not remove the failed virtual output")
            self.finish(1)
            return False
        self.glib.timeout_add(READY_INTERVAL_MS, self._stop_then_retry, tries + 1)
        return False

    def _restart(self):
        if not self.finished:
            self.stopping = False
            self.start()
        return False

    def stop(self):
        self.stopping = True
        if self.gst_proc and self.gst_proc.poll() is None:
            self.gst_proc.terminate()
        try:
            if self.session:
                self.session.Stop()
        except Exception:
            pass
        self.finish(0)

    def finish(self, code):
        if self.finished:
            return
        self.finished = True
        self.exit_code = code


def main(argv=None):
    dbus = _dbus()
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    config = parse_args(argv)
    if config.display_type.lower() != "mirror":
        config.preferred_scale = gnome_virtual_monitor.load_saved_virtual_scale(config.slot)
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bus = dbus.SessionBus()
    host = os.environ.get("MONITORIZE_HOST", "0.0.0.0" if config.mode == "wifi" else "127.0.0.1")
    port = int(os.environ.get("MONITORIZE_PORT", 7110 if config.mode == "wifi" else 7112))
    encoder = get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu"))

    def launch(node_id):
        print(f"[Monitorize GNOME] Streaming ({config.mode})")
        return launch_with_fallback(
            pw_fd=None, node_id=node_id, width=config.width, height=config.height,
            fps=config.fps, bitrate=config.bitrate, port=port, hw_encoder=encoder,
            host=host, server_mode=config.mode == "wifi",
        )

    owner = MutterStream(bus, dbus, GLib, config, launch)
    original_finish = owner.finish
    def finish(code):
        original_finish(code)
        if loop.is_running():
            loop.quit()
    owner.finish = finish
    signal.signal(signal.SIGINT, lambda *_args: owner.stop())
    signal.signal(signal.SIGTERM, lambda *_args: owner.stop())
    owner.start()
    loop.run()
    return owner.exit_code


if __name__ == "__main__":
    sys.exit(main())
