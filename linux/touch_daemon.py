#!/usr/bin/env python3
"""
touch_daemon.py — Monitorize Wayland touch injector via libei / snegg.

Usage:
  python3 touch_daemon.py [width] [height] [--debug]
  Defaults: 2560 1600

Pass --debug for full verbose output (recommended when diagnosing touch issues).
"""

import sys, os, select, struct, socket, signal, logging, threading, time, ctypes
from typing import Optional

_DEBUG = "--debug" in sys.argv

# ── snegg imports ──────────────────────────────────────────────────────────────
try:
    import snegg.ei as ei
    import snegg.oeffis as oeffis
except ImportError as e:
    print(f"ERROR: snegg not found. Install snegg. ({e})", file=sys.stderr)
    sys.exit(1)

# ── raw libei C types (safe against unknown EventType enum values) ─────────────
_libei = ei.libei
_libei.event_get_type.restype  = ctypes.c_uint32
_libei.event_type_to_string.restype  = ctypes.c_char_p
_libei.event_type_to_string.argtypes = [ctypes.c_uint32]

_EV_CONNECT          = 1
_EV_DISCONNECT       = 2
_EV_SEAT_ADDED       = 3
_EV_DEVICE_ADDED     = 5
_EV_DEVICE_REMOVED   = 6
_EV_START_EMULATING  = 200   # must arrive before we send touch events

# ── config ─────────────────────────────────────────────────────────────────────
PORT      = 7111
SCREEN_W  = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
SCREEN_H  = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
COORD_MAX = 65535

PKT_TOUCH    = 0x03
PKT_PEN      = 0x04
ACTION_DOWN  = 0
ACTION_MOVE  = 1
ACTION_UP    = 2
ACTION_HOVER = 3

PAYLOAD_FMT  = ">BBBHHHhh"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)   # 13 bytes

logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
    format="[TouchDaemon] %(levelname)s %(message)s",
)
log = logging.getLogger("TouchDaemon")
if _DEBUG:
    log.debug("DEBUG mode enabled")

# ── global state ───────────────────────────────────────────────────────────────
_ei_ctx:       Optional[ei.Sender] = None
_touch_dev:    Optional[ei.Device] = None
_pen_dev:      Optional[ei.Device] = None
_ei_lock       = threading.Lock()
_portal_ready  = threading.Event()
_shutdown      = threading.Event()

# CRITICAL FIX #1 — Touch objects must be held in a STRONG dict.
# snegg's CObjectWrapper uses a WeakValueDictionary internally, so the C-level
# touch pointer can be GC'd between DOWN and MOVE/UP unless we hold a strong ref.
_active_touches: dict[int, ei.Touch] = {}

# ── coordinate scaling ─────────────────────────────────────────────────────────
_virtual_monitor_cache = None

def _get_virtual_monitor_rect() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of Virtual-TabletDisplay from kscreen-doctor."""
    global _virtual_monitor_cache
    if _virtual_monitor_cache:
        return _virtual_monitor_cache
    
    try:
        import subprocess, json
        res = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True)
        data = json.loads(res.stdout)
        for output in data.get("outputs", []):
            if output.get("name") == "Virtual-TabletDisplay":
                pos = output.get("pos", {"x": 0, "y": 0})
                size = output.get("size", {"width": 1280, "height": 800})
                scale = output.get("scale", 1.0)
                # kscreen-doctor -j returns unscaled size in the JSON `size` field,
                # but logical size is size / scale. Let's rely on pos for the match.
                _virtual_monitor_cache = (float(pos["x"]), float(pos["y"]),
                                          float(size["width"]/scale), float(size["height"]/scale))
                return _virtual_monitor_cache
    except Exception as e:
        log.warning("Failed to query kscreen-doctor: %s", e)
    
    return None

def _scale(dev: ei.Device, nx: int, ny: int) -> tuple[float, float]:
    """Map Android 0-65535 normalised coords to the Virtual Monitor region."""
    
    # Target the virtual monitor dynamically
    vm_rect = _get_virtual_monitor_rect()
    target_rx, target_ry = 0.0, 0.0
    
    if vm_rect:
        target_rx, target_ry, _, _ = vm_rect
    
    # Find the region that matches this position
    best_reg = None
    best_rx, best_ry, best_rw, best_rh = 0.0, 0.0, float(SCREEN_W), float(SCREEN_H)
    
    if dev.regions:
        best_reg = dev.regions[0] # fallback
        for reg in dev.regions:
            rw, rh = reg.dimension
            rx, ry = 0.0, 0.0
            try:
                rx = float(_libei.region_get_x(reg._cobject))
                ry = float(_libei.region_get_y(reg._cobject))
            except Exception:
                pass
            
            # If this region matches the virtual monitor's position, it's the one!
            if abs(rx - target_rx) < 5 and abs(ry - target_ry) < 5:
                best_rx, best_ry, best_rw, best_rh = rx, ry, rw, rh
                break
        else:
            # If no match found, just use the first region but fetch its real position
            try:
                best_rx = float(_libei.region_get_x(best_reg._cobject))
                best_ry = float(_libei.region_get_y(best_reg._cobject))
                best_rw, best_rh = best_reg.dimension
            except Exception:
                pass

    x = best_rx + (nx / COORD_MAX) * best_rw
    y = best_ry + (ny / COORD_MAX) * best_rh
    return x, y

# ── injection helpers ──────────────────────────────────────────────────────────
def _inject_touch(action: int, cid: int, nx: int, ny: int) -> None:
    dev = _touch_dev
    if dev is None:
        return

    x, y = _scale(dev, nx, ny)
    log.debug("touch action=%d cid=%d → (%.1f, %.1f)", action, cid, x, y)

    try:
        if action == ACTION_DOWN:
            # CRITICAL: Store the object immediately in our strong dict (prevents GC)
            touch = dev.touch_new()
            _active_touches[cid] = touch
            touch.down(x, y)
            dev.frame()
            log.info("[INJECT] DOWN  cid=%d  coords=(%.1f, %.1f)  active_slots=%d",
                     cid, x, y, len(_active_touches))

        elif action == ACTION_MOVE:
            touch = _active_touches.get(cid)
            if touch is not None:
                touch.motion(x, y)
                dev.frame()
                log.debug("[INJECT] MOVE  cid=%d  coords=(%.1f, %.1f)", cid, x, y)
            else:
                log.warning("[INJECT] MOVE  cid=%d  — no active touch slot!", cid)

        elif action == ACTION_UP:
            touch = _active_touches.pop(cid, None)
            if touch is not None:
                touch.up()
                dev.frame()
                log.info("[INJECT] UP    cid=%d  coords=(%.1f, %.1f)  remaining=%d",
                         cid, x, y, len(_active_touches))
            else:
                log.warning("[INJECT] UP    cid=%d  — no active touch slot!", cid)

    except Exception as exc:
        log.error("inject_touch error cid=%d action=%d: %s", cid, action, exc, exc_info=True)

# ── portal + libei setup ───────────────────────────────────────────────────────
def _setup_libei() -> None:
    """Run the full portal handshake, set up libei, then spin the dispatch loop."""
    global _ei_ctx, _touch_dev

    log.info("Requesting TOUCHSCREEN permission via XDG RemoteDesktop portal…")
    log.info("▶  Watch for the compositor popup 'Allow Remote Control' and click Allow.")

    # Phase 1: oeffis portal handshake
    oef = oeffis.Oeffis.create(devices=oeffis.DeviceType.TOUCHSCREEN)
    eis_fd_int: Optional[int] = None

    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and not _shutdown.is_set():
        r, _, _ = select.select([oef.fd.fileno()], [], [], 1.0)
        if r and oef.dispatch():
            eis_fd_int = oef.eis_fd
            break

    if eis_fd_int is None:
        log.error("Portal timed out — user must click Allow in compositor popup.")
        _shutdown.set()
        return

    log.info("Portal granted — EIS fd=%d", eis_fd_int)

    # Phase 2: snegg.ei.Sender connection
    io_fd = os.fdopen(eis_fd_int, "rb", buffering=0)
    ctx   = ei.Sender.create_for_fd(io_fd, name="monitorize-touch")
    _ei_ctx = ctx

    seat       = None
    touch_dev  = None
    emulating  = False
    connected  = False

    deadline2 = time.monotonic() + 20.0
    while time.monotonic() < deadline2 and not _shutdown.is_set():
        r, _, _ = select.select([ctx.fd], [], [], 0.1)
        if r:
            ctx.dispatch()

        for event in ctx.events:
            raw = int(_libei.event_get_type(event._cobject))

            if raw == _EV_CONNECT:
                connected = True
                log.info("libei connected to compositor ✓")

            elif raw == _EV_SEAT_ADDED:
                seat = event.seat
                log.info("Seat added: %s — binding TOUCH capability", seat.name)
                seat.bind((ei.DeviceCapability.TOUCH,))

            elif raw == _EV_DEVICE_ADDED:
                dev  = event.device
                caps = dev.capabilities if dev else ()
                log.info("Device added: '%s'  caps=%s regions=%s",
                         getattr(dev, 'name', '?'),
                         [c.name for c in caps],
                         getattr(dev, 'regions', []))
                if dev and ei.DeviceCapability.TOUCH in caps and touch_dev is None:
                    touch_dev = dev
                    dev.start_emulating()
                    # KWin does NOT send EI_EVENT_DEVICE_START_EMULATING on this version.
                    # Mark ready immediately after calling start_emulating().
                    emulating = True
                    log.info("start_emulating() sent — device READY (no confirmation needed) ✓")

            elif raw == _EV_DISCONNECT:
                log.error("Compositor disconnected from libei!")
                _shutdown.set()
                return

        if connected and touch_dev and emulating:
            break

    if not (connected and touch_dev and emulating):
        log.error("libei setup incomplete (connected=%s device=%s emulating=%s). Touch disabled.",
                  connected, touch_dev is not None, emulating)
        _shutdown.set()
        return

    _touch_dev = touch_dev
    log.info("Touch daemon ready — screen %dx%d, region: %s",
             SCREEN_W, SCREEN_H,
             touch_dev.regions[0].dimension if touch_dev.regions else "none")
    _portal_ready.set()

    # Phase 3: CRITICAL FIX #4 — spin dispatch loop forever.
    # Without this, compositor PINGs go unanswered (→ connection drop) and
    # device.frame() output buffers are never flushed to the EIS socket.
    log.info("Entering libei dispatch loop…")
    while not _shutdown.is_set():
        r, _, _ = select.select([ctx.fd], [], [], 0.05)
        if r:
            ctx.dispatch()
            for event in ctx.events:
                raw = int(_libei.event_get_type(event._cobject))
                if raw == _EV_DISCONNECT:
                    log.error("Compositor disconnected — shutting down.")
                    _shutdown.set()
                    break
        else:
            # Even when idle, dispatch pumps any pending outgoing writes
            ctx.dispatch()

# ── TCP server ─────────────────────────────────────────────────────────────────
def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("peer closed")
        buf.extend(chunk)
    return bytes(buf)

def _handle_client(conn: socket.socket, addr) -> None:
    log.info("Android connected from %s", addr)
    # Wait up to 30 s for portal to be ready
    if not _portal_ready.wait(timeout=30):
        log.warning("Portal not ready after 30 s — dropping connection")
        conn.close()
        return

    pkt_count = 0
    try:
        while not _shutdown.is_set():
            hdr      = _read_exact(conn, 5)
            length   = struct.unpack(">I", hdr[:4])[0]
            pkt_type = hdr[4]
            payload  = _read_exact(conn, length) if length else b""
            pkt_count += 1

            if pkt_count == 1:
                log.info("[TCP] First packet received from Android! type=0x%02x len=%d",
                         pkt_type, length)

            if pkt_type not in (PKT_TOUCH, PKT_PEN):
                log.warning("[TCP] Unknown packet type 0x%02x — skip", pkt_type)
                continue
            if len(payload) < PAYLOAD_SIZE:
                log.warning("[TCP] Short payload %d bytes (need %d) — skip",
                            len(payload), PAYLOAD_SIZE)
                continue

            action, tool, cid, nx, ny, pressure, tx, ty = \
                struct.unpack(PAYLOAD_FMT, payload[:PAYLOAD_SIZE])

            log.debug("[TCP] pkt#%d type=0x%02x action=%d cid=%d norm=(%d,%d)",
                      pkt_count, pkt_type, action, cid, nx, ny)

            # Both touch and pen are routed through the TOUCH device
            _inject_touch(action, cid, nx, ny)

    except EOFError:
        log.info("Android disconnected cleanly")
    except Exception as e:
        if not _shutdown.is_set():
            log.error("Client error: %s", e)
    finally:
        conn.close()

def _run_tcp_server() -> None:
    """
    Linux is the TCP SERVER on port 7111.
    Android's InputEventSender connects to its own localhost:7111.
    adb reverse tcp:7111 tcp:7111 forwards Android's localhost:7111 -> Linux:7111.
    """
    import subprocess as _sp
    # Force-free the port (kills whatever process holds it, including stale daemons)
    _sp.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(0.5)   # wait for kernel to release

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass

    for attempt in range(8):
        try:
            server.bind(("127.0.0.1", PORT))
            break
        except OSError:
            log.warning("[TCP] Port %d busy (attempt %d/8) — retrying in 1 s…", PORT, attempt + 1)
            time.sleep(1)
    else:
        log.error("[TCP] Could not bind port %d — touch disabled.", PORT)
        return

    server.listen(2)
    server.settimeout(1.0)
    log.info("[TCP] Server listening on 127.0.0.1:%d (waiting for Android via adb reverse)", PORT)

    while not _shutdown.is_set():
        try:
            conn, addr = server.accept()
            threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except Exception as e:
            if not _shutdown.is_set():
                log.error("[TCP] Accept error: %s", e)
            break
    server.close()



# ── main ───────────────────────────────────────────────────────────────────────
def _cleanup(sig=None, frame=None):
    log.info("Shutting down…")
    _shutdown.set()
    if _touch_dev:
        try: _touch_dev.stop_emulating()
        except Exception: pass
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    log.info("touch_daemon.py — screen %dx%d", SCREEN_W, SCREEN_H)

    threading.Thread(target=_setup_libei,    daemon=True).start()
    threading.Thread(target=_run_tcp_server, daemon=True).start()

    # Main thread keeps process alive
    try:
        while not _shutdown.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        _cleanup()

if __name__ == "__main__":
    main()
