#!/usr/bin/env python3
"""
touch_daemon.py — Monitorize Wayland touch injector via libei.

Replaces input_bridge.py. Uses the XDG RemoteDesktop portal + libei
to inject touch and pen events into the compositor without moving the
real mouse cursor.

Usage:
  python3 touch_daemon.py <screen_width> <screen_height>
  Defaults: 2560 1600

The GUI (monitorize_gui.py) launches this via pkexec. See Section 4.

Packet framing (big-endian, same as input_bridge.py so Android unchanged):
  [4 bytes: uint32 payload_length][1 byte: packet_type][payload_length bytes]

Packet types:
  0x03 = finger touch
  0x04 = pen / stylus

Payload format (13 bytes, big-endian struct ">BBBHHHhh"):
  u8  action       0=DOWN 1=MOVE 2=UP 3=HOVER
  u8  tool         0=FINGER 1=PEN 2=ERASER
  u8  contact_id   0-9 (finger slot / pointer ID from Android)
  u16 x            0-65535 normalized (0=left edge, 65535=right edge)
  u16 y            0-65535 normalized (0=top edge, 65535=bottom edge)
  u16 pressure     0-65535 normalized
  i16 tilt_x       hundredths of degrees, -9000..9000
  i16 tilt_y       hundredths of degrees, -9000..9000
"""

import sys
import os
import struct
import socket
import signal
import logging
import threading
import time
from typing import Optional

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

# libei Python bindings
try:
    import ei
except ImportError:
    print("ERROR: pyei not installed. Run: pip install pyei", file=sys.stderr)
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

PORT      = 7111
SCREEN_W  = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
SCREEN_H  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
COORD_MAX = 65535

PKT_TOUCH = 0x03
PKT_PEN   = 0x04

ACTION_DOWN  = 0
ACTION_MOVE  = 1
ACTION_UP    = 2
ACTION_HOVER = 3

TOOL_FINGER  = 0
TOOL_PEN     = 1
TOOL_ERASER  = 2

PAYLOAD_FMT  = ">BBBHHHhh"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)  # 13

logging.basicConfig(
    level=logging.INFO,
    format="[TouchDaemon] %(levelname)s %(message)s",
)
log = logging.getLogger("TouchDaemon")

# ── Global state ──────────────────────────────────────────────────────────────

_glib_loop: Optional[GLib.MainLoop] = None
_ei_context: Optional[object] = None       # ei.Context
_touch_device: Optional[object] = None    # ei emulated touchscreen device
_pen_device: Optional[object] = None      # ei emulated stylus device
_active_touches: dict[int, object] = {}   # contact_id -> ei Touch object
_session_handle: Optional[str] = None
_portal_ready = threading.Event()          # set when portal FD is obtained
_shutdown = threading.Event()

# ── Phase 1: XDG Desktop Portal handshake ─────────────────────────────────────

PORTAL_BUS  = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.RemoteDesktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"

def _unique_token() -> str:
    return f"monitorize_{os.getpid()}_{int(time.time())}"


def _request_path(token: str, sender: str) -> str:
    # D-Bus request path convention
    sender_clean = sender.lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender_clean}/{token}"


def _do_portal_handshake(bus: dbus.SessionBus) -> int:
    """
    Performs the full RemoteDesktop portal handshake.
    Returns the EIS file descriptor integer on success.
    Raises RuntimeError on failure.

    Steps:
      1. CreateSession  → get session_handle
      2. SelectDevices  → request TOUCHSCREEN capability (type=3)
      3. Start          → triggers KDE/GNOME "Allow Remote Control" popup
      4. ConnectToEIS   → returns the file descriptor
    """
    desktop = bus.get_object(PORTAL_BUS, PORTAL_PATH)
    portal  = dbus.Interface(desktop, PORTAL_IFACE)

    # ── Step 1: CreateSession ──────────────────────────────────────────────

    session_token = _unique_token()
    req_token     = _unique_token()

    session_handle_result = []
    session_created = threading.Event()

    def on_create_session_response(response, results):
        if response != 0:
            log.error("CreateSession failed: response=%d results=%s", response, results)
            session_created.set()
            return
        session_handle_result.append(str(results["session_handle"]))
        log.info("Session created: %s", session_handle_result[0])
        session_created.set()

    req_path = _request_path(req_token, bus.get_unique_name())
    req_obj  = bus.get_object(PORTAL_BUS, req_path)
    req_iface = dbus.Interface(req_obj, REQUEST_IFACE)
    req_iface.connect_to_signal("Response", on_create_session_response)

    portal.CreateSession(
        dbus.Dictionary({
            "handle_token":  dbus.String(req_token),
            "session_handle_token": dbus.String(session_token),
        }, signature="sv")
    )

    session_created.wait(timeout=10)
    if not session_handle_result:
        raise RuntimeError("CreateSession timed out or was denied")

    global _session_handle
    _session_handle = session_handle_result[0]

    # ── Step 2: SelectDevices ─────────────────────────────────────────────
    # device_types bitmask: 1=keyboard, 2=pointer, 4=touchscreen
    # We request TOUCHSCREEN only (4) so cursor never moves.

    req_token2 = _unique_token()
    select_done = threading.Event()

    def on_select_response(response, results):
        if response != 0:
            log.error("SelectDevices failed: response=%d", response)
        else:
            log.info("Devices selected (TOUCHSCREEN mode)")
        select_done.set()

    req_path2 = _request_path(req_token2, bus.get_unique_name())
    req_obj2  = bus.get_object(PORTAL_BUS, req_path2)
    dbus.Interface(req_obj2, REQUEST_IFACE).connect_to_signal("Response", on_select_response)

    portal.SelectDevices(
        dbus.ObjectPath(_session_handle),
        dbus.Dictionary({
            "handle_token": dbus.String(req_token2),
            "types": dbus.UInt32(4),  # 4 = TOUCHSCREEN
            # "persist_mode": dbus.UInt32(2),  # optional: 2=persistent across reboots
        }, signature="sv")
    )

    select_done.wait(timeout=10)

    # ── Step 3: Start ─────────────────────────────────────────────────────
    # This triggers the OS "Allow Remote Control?" popup for the user.

    req_token3 = _unique_token()
    start_done = threading.Event()
    start_ok   = [False]

    def on_start_response(response, results):
        if response != 0:
            log.error("Start denied or failed: response=%d", response)
        else:
            log.info("RemoteDesktop session started — user approved")
            start_ok[0] = True
        start_done.set()

    req_path3 = _request_path(req_token3, bus.get_unique_name())
    req_obj3  = bus.get_object(PORTAL_BUS, req_path3)
    dbus.Interface(req_obj3, REQUEST_IFACE).connect_to_signal("Response", on_start_response)

    portal.Start(
        dbus.ObjectPath(_session_handle),
        dbus.String(""),  # parent window handle — empty string is fine
        dbus.Dictionary({
            "handle_token": dbus.String(req_token3),
        }, signature="sv")
    )

    start_done.wait(timeout=60)  # User has 60s to click Allow
    if not start_ok[0]:
        raise RuntimeError("User denied remote desktop access or timed out")

    # ── Step 4: ConnectToEIS ──────────────────────────────────────────────
    # This is the KDE Plasma 6 / GNOME 44+ method.
    # Returns a Unix file descriptor that libei connects to.

    try:
        fd = portal.ConnectToEIS(
            dbus.ObjectPath(_session_handle),
            dbus.Dictionary({}, signature="sv")
        )
        fd_int = int(fd.take())  # dbus.types.UnixFd → int
        log.info("Got EIS file descriptor: %d", fd_int)
        return fd_int
    except dbus.DBusException as e:
        raise RuntimeError(f"ConnectToEIS failed: {e}") from e


# ── Phase 2: libei setup ──────────────────────────────────────────────────────

def _setup_ei(fd: int) -> None:
    """
    Connect libei to the compositor via the FD from ConnectToEIS.
    Create a TOUCHSCREEN capability device and a STYLUS capability device.
    Stores them in globals _touch_device and _pen_device.
    """
    global _ei_context, _touch_device, _pen_device

    # Create ei sender context (we are the emulated input sender)
    ctx = ei.Context(ei.Context.Type.SENDER)
    ctx.connect_to_fd(fd)

    # Process events until the context is set up and devices are negotiated.
    # The compositor will send EI_EVENT_SEAT and EI_EVENT_DEVICE events.
    log.info("Waiting for compositor to accept libei connection...")

    seat = None
    touch_dev = None
    pen_dev = None
    deadline = time.time() + 10

    while time.time() < deadline:
        event = ctx.get_event()
        if event is None:
            ctx.dispatch()
            time.sleep(0.01)
            continue

        etype = event.type

        if etype == ei.EventType.CONNECT:
            log.info("libei connected to compositor")

        elif etype == ei.EventType.SEAT:
            seat = event.seat
            log.info("Got seat: %s", seat.name)
            # Request TOUCHSCREEN capability on this seat
            seat.bind_capabilities(ei.DeviceCapability.TOUCH)

        elif etype == ei.EventType.DEVICE:
            dev = event.device
            log.info("Device available: %s caps=%s", dev.name, dev.capabilities)
            caps = dev.capabilities
            if ei.DeviceCapability.TOUCH in caps:
                touch_dev = dev
                dev.start_emulating()
                log.info("Touch device ready: %s", dev.name)

        elif etype == ei.EventType.DEVICE_REMOVED:
            log.warning("Device removed: %s", event.device.name)

        if touch_dev:
            break

    if touch_dev is None:
        raise RuntimeError(
            "Compositor did not provide a TOUCH device. "
            "Check that SelectDevices was called with types=4."
        )

    # Also request a POINTER_ABSOLUTE device for the pen/stylus.
    # On most compositors, TOUCHSCREEN covers stylus too, but request
    # POINTER_ABSOLUTE as a fallback so pressure/tilt can be sent.
    # If compositor doesn't grant it, we fall back to touch events for pen.
    if seat:
        seat.bind_capabilities(ei.DeviceCapability.POINTER_ABSOLUTE)
        deadline2 = time.time() + 3
        while time.time() < deadline2:
            event = ctx.get_event()
            if event is None:
                ctx.dispatch()
                time.sleep(0.01)
                continue
            if event.type == ei.EventType.DEVICE:
                dev = event.device
                if ei.DeviceCapability.POINTER_ABSOLUTE in dev.capabilities:
                    pen_dev = dev
                    dev.start_emulating()
                    log.info("Pen/stylus device ready: %s", dev.name)
                    break

    _ei_context  = ctx
    _touch_device = touch_dev
    _pen_device   = pen_dev if pen_dev else touch_dev  # fall back to touch device for pen

    log.info("libei setup complete. Touch=%s Pen=%s",
             _touch_device.name if _touch_device else "None",
             _pen_device.name   if _pen_device   else "None")
    _portal_ready.set()


# ── Phase 3: Injection helpers ────────────────────────────────────────────────

def _scale_x(norm: int) -> float:
    return (norm / COORD_MAX) * SCREEN_W

def _scale_y(norm: int) -> float:
    return (norm / COORD_MAX) * SCREEN_H


def _inject_touch(action: int, contact_id: int, x: int, y: int, pressure: int) -> None:
    global _active_touches
    dev = _touch_device
    if dev is None:
        return

    rx = _scale_x(x)
    ry = _scale_y(y)

    ctx = _ei_context

    if action == ACTION_DOWN:
        touch = dev.touch_new(contact_id)  # libei: create a new tracked finger
        _active_touches[contact_id] = touch
        ctx.frame(time.monotonic_ns())
        touch.down(rx, ry)
        ctx.dispatch()

    elif action == ACTION_MOVE:
        touch = _active_touches.get(contact_id)
        if touch:
            ctx.frame(time.monotonic_ns())
            touch.motion(rx, ry)
            ctx.dispatch()

    elif action == ACTION_UP:
        touch = _active_touches.pop(contact_id, None)
        if touch:
            ctx.frame(time.monotonic_ns())
            touch.up()
            ctx.dispatch()


def _inject_pen(action: int, tool: int, x: int, y: int,
                pressure: int, tilt_x: int, tilt_y: int) -> None:
    dev = _pen_device
    if dev is None:
        return

    rx = _scale_x(x)
    ry = _scale_y(y)
    ctx = _ei_context

    # libei pointer_absolute: move + button state
    # Pressure and tilt are passed as axis values where supported.
    if action == ACTION_DOWN:
        ctx.frame(time.monotonic_ns())
        dev.pointer_motion_absolute(rx, ry)
        # Send pressure as scroll axis if device supports it — compositor-dependent
        # Primary stylus button = BTN_TOUCH equivalent via pointer button
        dev.button_button(0x14a, True)  # 0x14a = BTN_TOUCH in Linux evdev
        ctx.dispatch()

    elif action == ACTION_MOVE or action == ACTION_HOVER:
        ctx.frame(time.monotonic_ns())
        dev.pointer_motion_absolute(rx, ry)
        ctx.dispatch()

    elif action == ACTION_UP:
        ctx.frame(time.monotonic_ns())
        dev.pointer_motion_absolute(rx, ry)
        dev.button_button(0x14a, False)
        ctx.dispatch()


# ── Phase 4: TCP server for Android packets ───────────────────────────────────

def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connection closed")
        buf.extend(chunk)
    return bytes(buf)


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    header   = _read_exact(sock, 5)
    length   = struct.unpack(">I", header[:4])[0]
    pkt_type = header[4]
    payload  = _read_exact(sock, length) if length else b""
    return pkt_type, payload


def handle_connection(conn: socket.socket, addr) -> None:
    log.info("Android connected from %s", addr)
    try:
        while not _shutdown.is_set():
            pkt_type, payload = _read_packet(conn)

            if pkt_type not in (PKT_TOUCH, PKT_PEN):
                log.debug("Unknown packet 0x%02x — skip", pkt_type)
                continue

            if len(payload) < PAYLOAD_SIZE:
                log.warning("Short payload %d < %d", len(payload), PAYLOAD_SIZE)
                continue

            action, tool, contact_id, x, y, pressure, tilt_x, tilt_y = \
                struct.unpack(PAYLOAD_FMT, payload[:PAYLOAD_SIZE])

            if not _portal_ready.is_set():
                log.debug("Portal not ready yet, dropping packet")
                continue

            if pkt_type == PKT_TOUCH:
                _inject_touch(action, contact_id, x, y, pressure)
            elif pkt_type == PKT_PEN:
                _inject_pen(action, tool, x, y, pressure, tilt_x, tilt_y)

    except EOFError:
        log.info("Android disconnected")
    except Exception as e:
        log.error("Connection error: %s", e)
    finally:
        conn.close()


def run_tcp_server() -> None:
    """TCP server thread. Runs independently of GLib loop."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)
    server.settimeout(1.0)
    log.info("Listening on port %d for Android input events", PORT)

    while not _shutdown.is_set():
        try:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_connection,
                args=(conn, addr),
                daemon=True
            ).start()
        except socket.timeout:
            continue
        except Exception as e:
            if not _shutdown.is_set():
                log.error("Accept error: %s", e)
            break

    server.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def cleanup(sig=None, frame=None):
    log.info("Shutting down…")
    _shutdown.set()
    if _glib_loop and _glib_loop.is_running():
        _glib_loop.quit()
    if _touch_device:
        try: _touch_device.stop_emulating()
        except Exception: pass
    if _pen_device and _pen_device is not _touch_device:
        try: _pen_device.stop_emulating()
        except Exception: pass
    sys.exit(0)


def main():
    global _glib_loop

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    _glib_loop = GLib.MainLoop()

    log.info("Screen: %dx%d", SCREEN_W, SCREEN_H)

    # Portal handshake runs in background thread (GLib loop must run for signals)
    def portal_thread():
        try:
            fd = _do_portal_handshake(bus)
            _setup_ei(fd)
        except Exception as e:
            log.error("Portal/libei setup failed: %s", e)
            _shutdown.set()
            GLib.idle_add(_glib_loop.quit)

    threading.Thread(target=portal_thread, daemon=True).start()

    # TCP server runs in background thread
    threading.Thread(target=run_tcp_server, daemon=True).start()

    # GLib main loop needed for D-Bus signal delivery
    _glib_loop.run()


if __name__ == "__main__":
    main()
