#!/usr/bin/env python3
"""
touch_daemon.py — Monitorize Wayland touch injector via libei / snegg.

Replaces the broken "import ei" implementation with the correct snegg API.
Uses snegg.oeffis for the XDG RemoteDesktop portal handshake and
snegg.ei.Sender for the libei client connection.

Usage:
  python3 touch_daemon.py <screen_width> <screen_height>
  Defaults: 2560 1600

The GUI (monitorize_gui.py) launches this via pkexec.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API NOTES (verified against local snegg install):

  Package  : snegg   (NOT "ei" — top-level snegg.__init__ is empty)
  Modules  : snegg.ei    — libei client (Sender/Receiver/Context)
             snegg.oeffis — liboeffis portal helper

  Sender creation:
    ctx = snegg.ei.Sender.create_for_fd(os.fdopen(eis_fd_int))

  Event loop:
    ctx.dispatch()        — pump the libei fd
    for event in ctx.events:   — yields Event objects until queue empty

  Seat binding:
    seat.bind((DeviceCapability.TOUCH,))   ← tuple, not single value!

  Touch device:
    touch = device.touch_new()   ← no contact_id arg
    touch.down(x, y)
    touch.motion(x, y)
    touch.up()
    device.frame()               ← frame() is on Device, not Context

  Pen/stylus falls back to POINTER_ABSOLUTE on this compositor because
  there is no dedicated stylus capability in the snegg.ei.DeviceCapability
  enum (only POINTER, POINTER_ABSOLUTE, KEYBOARD, TOUCH, SCROLL, BUTTON).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Packet framing (big-endian, identical to input_bridge.py so Android unchanged):
  [4 bytes: uint32 payload_length][1 byte: packet_type][payload_length bytes]

Packet types:
  0x03 = finger touch
  0x04 = pen / stylus

Payload format (13 bytes, big-endian struct ">BBBHHHH hh"):
  u8  action       0=DOWN 1=MOVE 2=UP 3=HOVER
  u8  tool         0=FINGER 1=PEN 2=ERASER
  u8  contact_id   0-9
  u16 x            0-65535 normalised
  u16 y            0-65535 normalised
  u16 pressure     0-65535 normalised
  i16 tilt_x       hundredths of degrees
  i16 tilt_y       hundredths of degrees
"""

import sys
import os
import select
import struct
import socket
import signal
import logging
import threading
import time
from typing import Optional

# ── Dependency check: snegg ────────────────────────────────────────────────────

try:
    import snegg.ei as ei
    import snegg.oeffis as oeffis
except ImportError as _snegg_err:
    print(
        "ERROR: snegg/libei Python bindings not found.\n"
        "Install with:\n"
        "  sudo dnf install libei libei-devel gcc python3-devel meson ninja-build pkg-config git\n"
        "  python3 -m pip install --user "
        "git+https://gitlab.freedesktop.org/whot/snegg\n"
        "\n"
        f"Import error: {_snegg_err}",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Configuration ──────────────────────────────────────────────────────────────

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
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)   # 13 bytes

logging.basicConfig(
    level=logging.INFO,
    format="[TouchDaemon] %(levelname)s %(message)s",
)
log = logging.getLogger("TouchDaemon")

# ── Global state ───────────────────────────────────────────────────────────────

_ei_sender:      Optional[ei.Sender] = None    # snegg.ei.Sender (libei client)
_touch_device:   Optional[ei.Device] = None    # device with TOUCH capability
_pen_device:     Optional[ei.Device] = None    # device with POINTER_ABSOLUTE capability
_active_touches: dict[int, ei.Touch] = {}      # contact_id → Touch object
_portal_ready    = threading.Event()            # set once devices are ready
_shutdown        = threading.Event()

# ── Phase 1: Portal handshake via snegg.oeffis ────────────────────────────────

def _portal_handshake_oeffis() -> int:
    """
    Use snegg.oeffis (liboeffis wrapper) to run the full XDG RemoteDesktop
    portal flow and return the raw EIS file-descriptor integer.

    Oeffis.create(devices=TOUCHSCREEN) calls:
      CreateSession → SelectDevices(types=TOUCHSCREEN) → Start → ConnectToEIS
    internally via liboeffis.  We just need to poll oeffis.fd and call
    oeffis.dispatch() until it returns True (connected) or raises.

    oeffis.DeviceType.TOUCHSCREEN == 4
    """
    log.info("Starting portal session via snegg.oeffis (TOUCHSCREEN)…")
    log.info("Waiting for compositor 'Allow Remote Control' popup — please approve it.")

    ctx = oeffis.Oeffis.create(devices=oeffis.DeviceType.TOUCHSCREEN)

    # Poll oeffis.fd for up to 60 s (user must click Allow in the popup)
    deadline = time.monotonic() + 60.0
    oeffis_fileno = ctx.fd.fileno()

    while time.monotonic() < deadline:
        if _shutdown.is_set():
            raise RuntimeError("Shutdown requested during portal wait")

        ready, _, _ = select.select([oeffis_fileno], [], [], 1.0)
        if not ready:
            continue  # timeout — keep waiting

        connected = ctx.dispatch()
        if connected:
            eis_fd = ctx.eis_fd   # raw int fd to pass to snegg.ei.Sender
            log.info("Portal connected — EIS fd: %d", eis_fd)
            # Keep ctx alive; store it so it isn't GC'd (oeffis holds the fd)
            _portal_handshake_oeffis._ctx = ctx
            return eis_fd

    raise RuntimeError(
        "Timed out waiting for the compositor to grant remote-desktop access "
        "(60 s). Did you see and approve the 'Allow Remote Control' popup?"
    )


# ── Phase 2: libei setup via snegg.ei.Sender ──────────────────────────────────

def _setup_ei(eis_fd_int: int) -> None:
    """
    Connect snegg.ei.Sender to the compositor using the FD returned by the portal.
    Process events until the compositor grants a TOUCH device (and optionally a
    POINTER_ABSOLUTE device for pen fall-back).

    Verified API (snegg.ei):
      Sender.create_for_fd(io_fd, name)    — io_fd must be an IO object (os.fdopen)
      ctx.dispatch()                        — pump the libei event queue
      ctx.events                            — property: Iterator[Event]
      event.event_type                      — EventType enum
      event.seat                            — Seat | None
      event.device                          — Device | None
      seat.bind((DeviceCapability.TOUCH,))  — request capability; tuple required
      device.capabilities                   — tuple[DeviceCapability]
      device.start_emulating()              — announce we will send events
      device.touch_new()                    — Touch object (no contact_id arg)
      touch.down(x, y)
      touch.motion(x, y)
      touch.up()
      device.frame()                        — commit events (frame on Device!)
    """
    global _ei_sender, _touch_device, _pen_device

    # snegg.ei.Sender.create_for_fd expects an IO object, not a raw int
    io_fd = os.fdopen(eis_fd_int, "rb", buffering=0)
    log.info("Creating snegg.ei.Sender for EIS fd %d…", eis_fd_int)
    ctx = ei.Sender.create_for_fd(io_fd, name="monitorize-touch")

    ei_fileno = ctx.fd   # int: the fd to poll for incoming events
    log.info("Waiting for compositor to accept libei connection (fd=%d)…", ei_fileno)

    seat         = None
    touch_dev    = None
    pen_dev      = None
    connected    = False
    deadline     = time.monotonic() + 15.0

    # ── Event loop: pump until we get a TOUCH device ────────────────────────
    while time.monotonic() < deadline:
        if _shutdown.is_set():
            raise RuntimeError("Shutdown requested")

        # Wait up to 500 ms for data on the libei fd
        ready, _, _ = select.select([ei_fileno], [], [], 0.5)
        if ready:
            ctx.dispatch()

        for event in ctx.events:
            etype = event.event_type
            log.debug("ei event: %s", etype.name)

            if etype == ei.EventType.CONNECT:
                connected = True
                log.info("snegg/libei connected to compositor")

            elif etype == ei.EventType.SEAT_ADDED:
                seat = event.seat
                log.info("Seat added: %s (caps: %s)",
                         seat.name,
                         [c.name for c in seat.capabilities])
                # Request TOUCH capability on this seat
                # seat.bind() takes a tuple of DeviceCapability
                seat.bind((ei.DeviceCapability.TOUCH,))
                log.info("Requested TOUCH capability on seat %s", seat.name)

            elif etype == ei.EventType.DEVICE_ADDED:
                dev = event.device
                caps = dev.capabilities
                log.info("Device added: %s  caps=%s",
                         dev.name, [c.name for c in caps])

                if ei.DeviceCapability.TOUCH in caps and touch_dev is None:
                    touch_dev = dev
                    dev.start_emulating()
                    log.info("Touch device ready: %s", dev.name)

                elif ei.DeviceCapability.POINTER_ABSOLUTE in caps and pen_dev is None:
                    pen_dev = dev
                    dev.start_emulating()
                    log.info("Pen/pointer-absolute device ready: %s", dev.name)

            elif etype == ei.EventType.DEVICE_REMOVED:
                d = event.device
                log.warning("Device removed: %s", d.name if d else "?")

            elif etype == ei.EventType.DISCONNECT:
                raise RuntimeError("Compositor disconnected from libei")

        if touch_dev:
            break

    if not connected:
        raise RuntimeError(
            "snegg/libei never received a CONNECT event from the compositor. "
            "The EIS fd may have been invalid or the compositor does not support libei."
        )

    if touch_dev is None:
        raise RuntimeError(
            "Compositor did not grant a TOUCH device within 15 s. "
            "Check that SelectDevices was called with TOUCHSCREEN (types=4). "
            "Also verify the compositor supports XDG RemoteDesktop + libei."
        )

    # ── Optional: also request POINTER_ABSOLUTE for pen ─────────────────────
    if seat and pen_dev is None:
        log.info("Requesting POINTER_ABSOLUTE capability for pen/stylus fall-back…")
        seat.bind((ei.DeviceCapability.POINTER_ABSOLUTE,))

        deadline2 = time.monotonic() + 3.0
        while time.monotonic() < deadline2:
            ready, _, _ = select.select([ei_fileno], [], [], 0.5)
            if ready:
                ctx.dispatch()

            for event in ctx.events:
                if event.event_type == ei.EventType.DEVICE_ADDED:
                    dev = event.device
                    if ei.DeviceCapability.POINTER_ABSOLUTE in dev.capabilities:
                        pen_dev = dev
                        dev.start_emulating()
                        log.info("Pen device ready: %s", dev.name)
                        break
            if pen_dev:
                break

    _ei_sender    = ctx
    _touch_device = touch_dev
    # Fall back pen events to touch device if no POINTER_ABSOLUTE device available
    _pen_device   = pen_dev if pen_dev else touch_dev

    if pen_dev:
        log.info("Pen support: FULL (POINTER_ABSOLUTE device: %s)", pen_dev.name)
    else:
        log.info(
            "Pen support: DEGRADED — compositor did not grant POINTER_ABSOLUTE. "
            "Pen events will be routed through the touch device."
        )

    log.info(
        "libei setup complete. Touch=%s  Pen=%s",
        _touch_device.name,
        _pen_device.name,
    )
    _portal_ready.set()


# ── Phase 3: Injection helpers ────────────────────────────────────────────────

def _scale_x(norm: int) -> float:
    return (norm / COORD_MAX) * SCREEN_W

def _scale_y(norm: int) -> float:
    return (norm / COORD_MAX) * SCREEN_H


def _inject_touch(action: int, contact_id: int, x: int, y: int) -> None:
    """
    Inject a finger touch event.

    Verified snegg.ei API shape:
      touch = device.touch_new()   ← no contact_id; we track via dict
      touch.down(rx, ry)
      touch.motion(rx, ry)
      touch.up()
      device.frame()               ← commits the event batch to compositor
    """
    global _active_touches
    dev = _touch_device
    if dev is None:
        return

    rx = _scale_x(x)
    ry = _scale_y(y)

    try:
        if action == ACTION_DOWN:
            touch = dev.touch_new()
            _active_touches[contact_id] = touch
            touch.down(rx, ry)
            dev.frame()

        elif action == ACTION_MOVE:
            touch = _active_touches.get(contact_id)
            if touch:
                touch.motion(rx, ry)
                dev.frame()

        elif action == ACTION_UP:
            touch = _active_touches.pop(contact_id, None)
            if touch:
                touch.up()
                dev.frame()

    except Exception as exc:
        log.error("Touch injection error (contact=%d action=%d): %s",
                  contact_id, action, exc)


def _inject_pen(action: int, x: int, y: int) -> None:
    """
    Inject a pen/stylus event via POINTER_ABSOLUTE (or touch fall-back).

    snegg.ei has no dedicated stylus capability — we use POINTER_ABSOLUTE.
    BTN_TOUCH = 0x14a (330) simulates tip contact.
    """
    dev = _pen_device
    if dev is None:
        return

    rx = _scale_x(x)
    ry = _scale_y(y)

    # If the pen device actually has TOUCH capability (fall-back path),
    # use a fixed contact_id=9 so it doesn't collide with finger slots.
    if ei.DeviceCapability.TOUCH in dev.capabilities:
        _inject_touch(action, contact_id=9, x=x, y=y)
        return

    # POINTER_ABSOLUTE path
    try:
        if action == ACTION_DOWN:
            dev.pointer_motion_absolute(rx, ry)
            dev.button_button(0x14a, True)   # BTN_TOUCH press
            dev.frame()

        elif action in (ACTION_MOVE, ACTION_HOVER):
            dev.pointer_motion_absolute(rx, ry)
            dev.frame()

        elif action == ACTION_UP:
            dev.pointer_motion_absolute(rx, ry)
            dev.button_button(0x14a, False)  # BTN_TOUCH release
            dev.frame()

    except Exception as exc:
        log.error("Pen injection error (action=%d): %s", action, exc)


# ── Phase 4: TCP server for Android packets ───────────────────────────────────

def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connection closed by peer")
        buf.extend(chunk)
    return bytes(buf)


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    """Read one framed packet: [4-byte length][1-byte type][payload]."""
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
                log.debug("Unknown packet type 0x%02x — skipping", pkt_type)
                continue

            if len(payload) < PAYLOAD_SIZE:
                log.warning("Short payload %d < %d bytes — skipping", len(payload), PAYLOAD_SIZE)
                continue

            action, tool, contact_id, x, y, pressure, tilt_x, tilt_y = \
                struct.unpack(PAYLOAD_FMT, payload[:PAYLOAD_SIZE])

            if not _portal_ready.is_set():
                log.debug("Portal not yet ready — dropping packet (action=%d)", action)
                continue

            if pkt_type == PKT_TOUCH:
                _inject_touch(action, contact_id, x, y)
            elif pkt_type == PKT_PEN:
                _inject_pen(action, x, y)

    except EOFError:
        log.info("Android disconnected cleanly")
    except Exception as exc:
        if not _shutdown.is_set():
            log.error("Connection error: %s", exc)
    finally:
        conn.close()


def run_tcp_server() -> None:
    """TCP server thread — accepts Android connections independently of the GLib loop."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)
    server.settimeout(1.0)
    log.info("Listening on 127.0.0.1:%d for Android input events", PORT)

    while not _shutdown.is_set():
        try:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_connection,
                args=(conn, addr),
                daemon=True,
            ).start()
        except socket.timeout:
            continue
        except Exception as exc:
            if not _shutdown.is_set():
                log.error("Accept error: %s", exc)
            break

    server.close()
    log.info("TCP server stopped")


# ── Main ───────────────────────────────────────────────────────────────────────

def cleanup(sig=None, frame=None):
    log.info("Shutting down (signal=%s)…", sig)
    _shutdown.set()
    if _touch_device:
        try:
            _touch_device.stop_emulating()
        except Exception:
            pass
    if _pen_device and _pen_device is not _touch_device:
        try:
            _pen_device.stop_emulating()
        except Exception:
            pass
    sys.exit(0)


def portal_and_ei_thread() -> None:
    """Background thread: portal handshake → libei setup → set _portal_ready."""
    try:
        eis_fd = _portal_handshake_oeffis()
        _setup_ei(eis_fd)
    except Exception as exc:
        log.error("Portal/libei setup failed: %s", exc)
        log.error(
            "Possible causes:\n"
            "  • User denied the 'Allow Remote Control' compositor popup\n"
            "  • Compositor does not support XDG RemoteDesktop portal + libei\n"
            "  • liboeffis or libei not installed (check 'ldconfig -p | grep oeffis')\n"
            "  • DBUS_SESSION_BUS_ADDRESS not set in the pkexec environment"
        )
        _shutdown.set()


def main():
    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    log.info("touch_daemon.py starting — screen %dx%d", SCREEN_W, SCREEN_H)
    log.info("Using snegg.ei (libei) and snegg.oeffis (liboeffis) — no manual D-Bus glue")

    # Portal + libei in background (needs event loop to be running for D-Bus signals,
    # but snegg.oeffis uses liboeffis which handles its own D-Bus internally)
    t = threading.Thread(target=portal_and_ei_thread, daemon=True)
    t.start()

    # TCP server in background
    threading.Thread(target=run_tcp_server, daemon=True).start()

    log.info(
        "⚠  Watch for the compositor 'Allow Remote Control' popup and click Allow."
    )

    # Main thread: wait for shutdown signal (keeps process alive for daemon threads)
    try:
        while not _shutdown.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
