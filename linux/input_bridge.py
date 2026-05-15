#!/usr/bin/env python3
"""
input_bridge.py — Monitorize Linux input receiver.

Listens on TCP port 7111 for input packets forwarded from Android and
injects them into the kernel via uinput (evdev). Compositor-agnostic.

Packet framing (all big-endian):
  [4 bytes: payload length][1 byte: packet type][payload bytes]

Packet types received here:
  0x03  finger touch event
  0x04  pen / stylus event

Payload fields (all big-endian):
  u8   action       0=DOWN 1=MOVE 2=UP 3=HOVER
  u8   tool         0=FINGER 1=PEN 2=ERASER
  u8   contact_id   0-9  (for future multitouch; always 0 for single touch)
  u16  x            0-65535 normalized
  u16  y            0-65535 normalized
  u16  pressure     0-65535 normalized
  i16  tilt_x       -9000..9000  (hundredths of degrees; 0 if unavailable)
  i16  tilt_y       -9000..9000

Total payload = 13 bytes.

Usage:
  python3 input_bridge.py <screen_width> <screen_height>
  (Defaults: 2560 1600 if not given)

Permission note:
  Requires write access to /dev/uinput.
  Run setup_uinput_permissions.sh once, then re-login.
"""

import sys
import os
import struct
import socket
import signal
import logging
import threading
from typing import Optional

from evdev import UInput, ecodes, AbsInfo

# ── Configuration ────────────────────────────────────────────────────────────

PORT          = 7111
SCREEN_W      = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
SCREEN_H      = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
COORD_MAX     = 65535
PRESSURE_MAX  = 65535
TILT_MAX      = 9000        # hundredths of degrees

# Packet types
PKT_TOUCH = 0x03
PKT_PEN   = 0x04

# Input actions
ACTION_DOWN  = 0
ACTION_MOVE  = 1
ACTION_UP    = 2
ACTION_HOVER = 3

# Tool types
TOOL_FINGER  = 0
TOOL_PEN     = 1
TOOL_ERASER  = 2

# Payload struct: >B B B H H H h h  = 13 bytes
PAYLOAD_FMT  = ">BBBHHHhh"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)   # == 13

logging.basicConfig(
    level=logging.INFO,
    format="[InputBridge] %(levelname)s %(message)s",
)
log = logging.getLogger("InputBridge")


# ── uinput device creation ───────────────────────────────────────────────────

def _check_uinput_permission() -> bool:
    return os.access("/dev/uinput", os.W_OK)


def create_touch_device(w: int, h: int) -> UInput:
    """Create an absolute pointing device (simulating touch)."""
    events = {
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(0, 0, COORD_MAX, 0, 0, 0)),
            (ecodes.ABS_Y, AbsInfo(0, 0, COORD_MAX, 0, 0, 0)),
        ],
        ecodes.EV_SYN: [],
    }
    
    props = []
    if hasattr(ecodes, "INPUT_PROP_POINTER"):
        props.append(ecodes.INPUT_PROP_POINTER)
        
    dev = UInput(
        events,
        name="Monitorize Touch Pointer",
        vendor=0x1234,
        product=0x0001,
        input_props=props,
    )
    log.info("Touch pointer device: %s", dev.device.path if getattr(dev, "device", None) else "unknown")
    return dev


def create_pen_device(w: int, h: int) -> UInput:
    """Create a pen/stylus virtual device (tablet-style)."""
    events = {
        ecodes.EV_KEY: [
            ecodes.BTN_TOUCH,
            ecodes.BTN_TOOL_PEN,
            ecodes.BTN_TOOL_RUBBER,
            ecodes.BTN_STYLUS,
        ],
        ecodes.EV_ABS: [
            (ecodes.ABS_X,        AbsInfo(0, 0, COORD_MAX, 0, 0, 0)),
            (ecodes.ABS_Y,        AbsInfo(0, 0, COORD_MAX, 0, 0, 0)),
            (ecodes.ABS_PRESSURE, AbsInfo(0, 0, PRESSURE_MAX, 0, 0, 0)),
            (ecodes.ABS_TILT_X,   AbsInfo(0, -TILT_MAX, TILT_MAX, 0, 0, 0)),
            (ecodes.ABS_TILT_Y,   AbsInfo(0, -TILT_MAX, TILT_MAX, 0, 0, 0)),
        ],
        ecodes.EV_SYN: [],
    }
    dev = UInput(
        events,
        name="Monitorize Stylus",
        vendor=0x1234,
        product=0x0002,
    )
    log.info("Pen device:   %s", dev.device.path if getattr(dev, "device", None) else "unknown")
    return dev


# ── State: single-touch contact tracker ─────────────────────────────────────

class TouchState:
    """Tracks per-slot state so we can emit correct MT tracking IDs."""
    def __init__(self):
        # contact_id -> tracking_id (assigned on DOWN, cleared on UP)
        self._slots: dict[int, int] = {}
        self._next_tid = 1

    def down(self, contact_id: int) -> int:
        tid = self._next_tid
        self._next_tid = (self._next_tid % 65534) + 1
        self._slots[contact_id] = tid
        return tid

    def tracking_id(self, contact_id: int) -> int:
        return self._slots.get(contact_id, -1)

    def up(self, contact_id: int) -> int:
        return self._slots.pop(contact_id, -1)


# ── Injection helpers ─────────────────────────────────────────────────────────

def _inject_touch(dev: UInput, state: TouchState,
                  action: int, contact_id: int,
                  x: int, y: int, pressure: int) -> None:
    # Treat the first finger as an absolute mouse pointer.
    # Ignore multitouch fingers to avoid cursor jumping.
    if contact_id != 0:
        return

    dev.write(ecodes.EV_ABS, ecodes.ABS_X, x)
    dev.write(ecodes.EV_ABS, ecodes.ABS_Y, y)

    if action == ACTION_DOWN:
        dev.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
    elif action == ACTION_UP:
        dev.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
    # ACTION_MOVE just updates the ABS coordinates

    dev.syn()


def _inject_pen(dev: UInput,
                action: int, tool: int,
                x: int, y: int, pressure: int,
                tilt_x: int, tilt_y: int) -> None:

    is_hover = (action == ACTION_HOVER)

    # Tool button
    if tool == TOOL_ERASER:
        tool_btn = ecodes.BTN_TOOL_RUBBER
    else:
        tool_btn = ecodes.BTN_TOOL_PEN

    if action in (ACTION_DOWN, ACTION_HOVER):
        dev.write(ecodes.EV_KEY, tool_btn, 1)
        dev.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0 if is_hover else 1)

    dev.write(ecodes.EV_ABS, ecodes.ABS_X, x)
    dev.write(ecodes.EV_ABS, ecodes.ABS_Y, y)
    dev.write(ecodes.EV_ABS, ecodes.ABS_PRESSURE, pressure if not is_hover else 0)
    dev.write(ecodes.EV_ABS, ecodes.ABS_TILT_X, tilt_x)
    dev.write(ecodes.EV_ABS, ecodes.ABS_TILT_Y, tilt_y)

    if action == ACTION_UP:
        dev.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0)
        dev.write(ecodes.EV_KEY, tool_btn, 0)

    dev.syn()


# ── Packet reading ─────────────────────────────────────────────────────────────

def _read_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes or raise EOFError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connection closed")
        buf.extend(chunk)
    return bytes(buf)


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    """Read one framed packet: returns (pkt_type, payload_bytes)."""
    header = _read_exact(sock, 5)               # 4 len + 1 type
    length = struct.unpack(">I", header[:4])[0]
    pkt_type = header[4]
    payload = _read_exact(sock, length) if length else b""
    return pkt_type, payload


# ── Connection handler ────────────────────────────────────────────────────────

def handle_connection(conn: socket.socket, addr,
                      touch_dev: UInput, pen_dev: UInput) -> None:
    log.info("Input connection from %s", addr)
    touch_state = TouchState()
    try:
        while True:
            pkt_type, payload = _read_packet(conn)

            if pkt_type not in (PKT_TOUCH, PKT_PEN):
                log.warning("Unknown packet type 0x%02x — skipping", pkt_type)
                continue

            if len(payload) < PAYLOAD_SIZE:
                log.warning("Payload too short (%d < %d)", len(payload), PAYLOAD_SIZE)
                continue

            action, tool, contact_id, x, y, pressure, tilt_x, tilt_y = \
                struct.unpack(PAYLOAD_FMT, payload[:PAYLOAD_SIZE])

            if pkt_type == PKT_TOUCH:
                if action == ACTION_DOWN:
                    log.info("Touch DOWN at %d, %d (contact %d)", x, y, contact_id)
                _inject_touch(touch_dev, touch_state, action, contact_id, x, y, pressure)
            elif pkt_type == PKT_PEN:
                if action == ACTION_DOWN:
                    log.info("Pen DOWN at %d, %d", x, y)
                _inject_pen(pen_dev, action, tool, x, y, pressure, tilt_x, tilt_y)

    except EOFError:
        log.info("Android disconnected from %s", addr)
    except Exception as e:
        log.error("Handler error: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Server ────────────────────────────────────────────────────────────────────

_server_sock: Optional[socket.socket] = None
_touch_dev: Optional[UInput] = None
_pen_dev: Optional[UInput] = None


def cleanup(sig=None, frame=None) -> None:
    log.info("Shutting down input bridge…")
    try:
        if _server_sock:
            _server_sock.close()
    except Exception:
        pass
    try:
        if _touch_dev:
            _touch_dev.close()
    except Exception:
        pass
    try:
        if _pen_dev:
            _pen_dev.close()
    except Exception:
        pass
    log.info("Virtual input devices closed.")
    sys.exit(0)


def run_server() -> None:
    global _server_sock, _touch_dev, _pen_dev

    if not _check_uinput_permission():
        log.error(
            "/dev/uinput not writable. Run:\n"
            "  sudo usermod -aG input $USER\n"
            "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' "
            "| sudo tee /etc/udev/rules.d/99-uinput.rules\n"
            "  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
            "Then re-login and restart."
        )
        sys.exit(1)

    log.info("Creating virtual input devices...")
    _touch_dev = create_touch_device(SCREEN_W, SCREEN_H)
    _pen_dev   = create_pen_device(SCREEN_W, SCREEN_H)

    log.info("Attempting to connect to Android app on port %d...", PORT)
    
    while True:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            conn.connect(("127.0.0.1", PORT))
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            handle_connection(conn, ("127.0.0.1", PORT), _touch_dev, _pen_dev)
        except ConnectionRefusedError:
            log.warning("Connection refused by Android app. Is it running? Retrying in 2s...")
            time.sleep(2)
        except Exception as e:
            log.error("Network error: %s. Retrying in 2s...", e)
            time.sleep(2)
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        log.info("Shutting down input bridge...")
