#!/usr/bin/env python3
"""
touch_daemon.py — Monitorize Wayland touch injector.

KDE / GNOME: uses libei via snegg + XDG RemoteDesktop portal.
Hyprland:    uses evdev/uinput (kernel-level virtual touchscreen),
             because xdg-desktop-portal-hyprland does NOT implement
             the RemoteDesktop portal.

Usage:
  python3 touch_daemon.py [width] [height] [--debug]
  Defaults: 2560 1600

Pass --debug for full verbose output (recommended when diagnosing touch issues).
"""

import sys, os, select, struct, socket, signal, logging, threading, time, ctypes
from typing import Optional

_DEBUG = "--debug" in sys.argv

# ── snegg imports (optional — only needed for KDE/GNOME) ──────────────────────
_HAS_SNEGG = False
try:
    import snegg.ei as ei
    import snegg.oeffis as oeffis
    _HAS_SNEGG = True
except ImportError:
    ei = None       # type: ignore
    oeffis = None   # type: ignore

# ── evdev imports (optional — only needed for Hyprland) ───────────────────────
_HAS_EVDEV = False
try:
    import evdev
    from evdev import UInput, ecodes as e_codes
    _HAS_EVDEV = True
except ImportError:
    evdev = None   # type: ignore

# ── raw libei C types (safe against unknown EventType enum values) ─────────────
_libei = None
if _HAS_SNEGG:
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
_ei_ctx       = None   # ei.Sender (KDE/GNOME)
_touch_dev    = None   # ei.Device (KDE/GNOME)
_pen_dev      = None   # ei.Device (KDE/GNOME)
_uinput_dev   = None   # evdev.UInput (Hyprland)
_ei_lock       = threading.Lock()
_portal_ready  = threading.Event()
_shutdown      = threading.Event()

# CRITICAL FIX #1 — Touch objects must be held in a STRONG dict.
# snegg's CObjectWrapper uses a WeakValueDictionary internally, so the C-level
# touch pointer can be GC'd between DOWN and MOVE/UP unless we hold a strong ref.
_active_touches: dict = {}
_inject_fn = None   # set in main() → _inject_touch_libei or _inject_touch_uinput

# ── DE detection (for coordinate mapping) ─────────────────────────────────────
def _detect_de() -> str:
    """Detect desktop environment. Returns 'kde', 'gnome', 'hyprland', or 'unknown'."""
    hypr = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    xdg  = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION", "").lower()
    combined = xdg + " " + dsess
    if hypr or "hyprland" in combined:
        return "hyprland"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    return "unknown"

_DETECTED_DE = _detect_de()
log.info("Detected DE: %s", _DETECTED_DE)

# ── coordinate scaling ─────────────────────────────────────────────────────────
_virtual_monitor_cache = None

def _get_virtual_monitor_rect_kde() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of Virtual-TabletDisplay from kscreen-doctor."""
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
                # but logical size is size / scale.
                return (float(pos["x"]), float(pos["y"]),
                        float(size["width"]/scale), float(size["height"]/scale))
    except Exception as e:
        log.warning("Failed to query kscreen-doctor: %s", e)
    return None

def _get_virtual_monitor_rect_hyprland() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the headless virtual monitor from hyprctl."""
    try:
        import subprocess, json
        res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True)
        monitors = json.loads(res.stdout)
        for mon in monitors:
            name = mon.get("name", "")
            # Hyprland headless monitors are named HEADLESS-1, HEADLESS-2, etc.
            if name.startswith("HEADLESS"):
                x = float(mon.get("x", 0))
                y = float(mon.get("y", 0))
                w = float(mon.get("width", SCREEN_W))
                h = float(mon.get("height", SCREEN_H))
                scale = float(mon.get("scale", 1.0))
                # hyprctl reports pixel dimensions; logical size = pixels / scale
                log.info("Found Hyprland headless monitor %s at (%.0f, %.0f) %dx%d scale=%.1f",
                         name, x, y, int(w), int(h), scale)
                return (x, y, w / scale, h / scale)
    except Exception as e:
        log.warning("Failed to query hyprctl monitors: %s", e)
    return None

def _get_virtual_monitor_rect_gnome() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the GNOME virtual monitor.

    GNOME's Streamer_gnome_usb.py creates the virtual monitor via Mutter's
    RecordVirtual D-Bus API with a known position (currently 0,0).  We query
    org.gnome.Mutter.DisplayConfig to find the logical monitor whose
    connector starts with 'Virtual-' (Mutter's naming for RecordVirtual
    outputs).  If the D-Bus query fails we fall back to (0, 0, SCREEN_W,
    SCREEN_H) which matches the RecordVirtual defaults.
    """
    try:
        import subprocess, json
        # gdctl (GNOME 47+) or gnome-monitor-config can dump JSON, but the
        # most universal method is the D-Bus DisplayConfig.GetCurrentState
        # introspection.  However, parsing that is complex.  A simpler
        # approach: Mutter stores the layout in ~/.config/monitors.xml but
        # RecordVirtual monitors are ephemeral and won't appear there.
        #
        # Best portable approach: try gdctl first, fall back to the known
        # position that Streamer_gnome_usb.py passes to RecordVirtual.
        res = subprocess.run(
            ["gdctl", "show", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for mon in data.get("monitors", data.get("logical-monitors", [])):
                connector = mon.get("connector", mon.get("name", ""))
                if connector.startswith("Virtual") or connector.startswith("virtual"):
                    x = float(mon.get("x", 0))
                    y = float(mon.get("y", 0))
                    # gdctl may report the mode inside a nested structure
                    mode = mon.get("current-mode", mon)
                    w = float(mode.get("width", SCREEN_W))
                    h = float(mode.get("height", SCREEN_H))
                    scale = float(mon.get("scale", 1.0))
                    log.info("Found GNOME virtual monitor %s at (%.0f, %.0f) %dx%d",
                             connector, x, y, int(w), int(h))
                    return (x, y, w / scale, h / scale)
    except FileNotFoundError:
        log.debug("gdctl not found (pre-GNOME 47), using RecordVirtual defaults")
    except Exception as e:
        log.warning("Failed to query GNOME monitor config: %s", e)

    # Fallback: Streamer_gnome_usb.py creates the virtual monitor at (0, 0)
    # with the user-specified SCREEN_W x SCREEN_H, so use those directly.
    log.info("Using GNOME RecordVirtual defaults: (0, 0, %d, %d)", SCREEN_W, SCREEN_H)
    return (0.0, 0.0, float(SCREEN_W), float(SCREEN_H))

def _get_virtual_monitor_rect() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the virtual monitor, dispatching by DE."""
    global _virtual_monitor_cache
    if _virtual_monitor_cache:
        return _virtual_monitor_cache

    if _DETECTED_DE == "hyprland":
        result = _get_virtual_monitor_rect_hyprland()
    elif _DETECTED_DE == "kde":
        result = _get_virtual_monitor_rect_kde()
    elif _DETECTED_DE == "gnome":
        result = _get_virtual_monitor_rect_gnome()
    else:
        # Unknown DE — try all, most specific first
        result = (_get_virtual_monitor_rect_hyprland()
                  or _get_virtual_monitor_rect_kde()
                  or _get_virtual_monitor_rect_gnome())

    if result:
        _virtual_monitor_cache = result
    return result

def _scale(dev, nx: int, ny: int) -> tuple[float, float]:
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

# ── injection helpers (libei — KDE/GNOME) ──────────────────────────────────────
def _inject_touch_libei(action: int, cid: int, nx: int, ny: int) -> None:
    dev = _touch_dev
    if dev is None:
        return

    x, y = _scale(dev, nx, ny)
    log.debug("touch action=%d cid=%d → (%.1f, %.1f)", action, cid, x, y)

    try:
        with _ei_lock:
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
                    # log.debug("[INJECT] MOVE  cid=%d  coords=(%.1f, %.1f)", cid, x, y)
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

            if _ei_ctx:
                _ei_ctx.dispatch()

    except Exception as exc:
        log.error("inject_touch error cid=%d action=%d: %s", cid, action, exc, exc_info=True)

# ── injection helpers (uinput — Hyprland) ──────────────────────────────────────
def _inject_touch_uinput(action: int, cid: int, nx: int, ny: int) -> None:
    """Inject touch via evdev/uinput virtual touchscreen (Hyprland backend)."""
    ui = _uinput_dev
    if ui is None:
        return

    # Map normalised 0-65535 to the virtual screen pixel coordinates
    vm = _get_virtual_monitor_rect()
    if vm:
        _, _, vw, vh = vm
    else:
        vw, vh = float(SCREEN_W), float(SCREEN_H)

    abs_x = int((nx / COORD_MAX) * vw)
    abs_y = int((ny / COORD_MAX) * vh)
    slot = cid % 10   # max 10 slots

    try:
        with _ei_lock:
            if action == ACTION_DOWN:
                _active_touches[cid] = slot
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, slot)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_TRACKING_ID, cid & 0xFFFF)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_X, abs_x)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_Y, abs_y)
                ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 1)
                ui.syn()
                log.info("[UINPUT] DOWN  cid=%d slot=%d coords=(%d, %d)  active=%d",
                         cid, slot, abs_x, abs_y, len(_active_touches))

            elif action == ACTION_MOVE:
                s = _active_touches.get(cid)
                if s is not None:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, s)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_X, abs_x)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_Y, abs_y)
                    ui.syn()

            elif action == ACTION_UP:
                s = _active_touches.pop(cid, None)
                if s is not None:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, s)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_TRACKING_ID, -1)
                    if not _active_touches:
                        ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 0)
                    ui.syn()
                    log.info("[UINPUT] UP    cid=%d slot=%d  remaining=%d",
                             cid, s, len(_active_touches))

    except Exception as exc:
        log.error("inject_touch_uinput error cid=%d action=%d: %s", cid, action, exc, exc_info=True)

def _inject_pen(action: int, tool: int, nx: int, ny: int, pressure: int, tx: int, btn_state: int) -> None:
    global _pen_dev, _touch_dev
    dev = _pen_dev if _pen_dev is not None else _touch_dev
    if dev is None:
        return

    x, y = _scale(dev, nx, ny)
    
    # 32 is BUTTON_STYLUS_PRIMARY on Android (the side button)
    # Tool 2 is ERASER
    is_secondary = (btn_state & 32) != 0 or (tool == 2)
    # 0x110 is BTN_LEFT, 0x111 is BTN_RIGHT
    # Note: earlier we used 0x14a for touch, but libei absolute pointer supports BTN_LEFT/BTN_RIGHT standard mouse codes
    # We will use 0x110/0x111 to avoid compatibility issues with drawing apps
    button_code = 0x111 if is_secondary else 0x110
    
    try:
        with _ei_lock:
            if action == ACTION_DOWN:
                dev.pointer_motion_absolute(x, y)
                dev.button_button(button_code, True)
                dev.frame()
                log.info("[INJECT PEN] DOWN  coords=(%.1f, %.1f) tool=%d btn=0x%x", x, y, tool, button_code)
            elif action == ACTION_MOVE:
                dev.pointer_motion_absolute(x, y)
                dev.frame()
                # log.debug("[INJECT PEN] MOVE  coords=(%.1f, %.1f)", x, y)
            elif action == ACTION_UP:
                dev.pointer_motion_absolute(x, y)
                dev.button_button(button_code, False)
                # also release the other button just in case state changed while pressed
                other_btn = 0x110 if is_secondary else 0x111
                dev.button_button(other_btn, False)
                dev.frame()
                log.info("[INJECT PEN] UP    coords=(%.1f, %.1f)", x, y)
            elif action == ACTION_HOVER:
                dev.pointer_motion_absolute(x, y)
                dev.frame()
                # log.debug("[INJECT PEN] HOVER coords=(%.1f, %.1f) btn_state=%d", x, y, btn_state)

            if _ei_ctx_pen:
                _ei_ctx_pen.dispatch()
    except Exception as exc:
        log.error("inject_pen error action=%d: %s", action, exc, exc_info=True)

# ── portal + libei setup (KDE / GNOME) ─────────────────────────────────────────
def _setup_libei() -> None:
    """Run the full portal handshake, set up libei, then spin the dispatch loop."""
    global _touch_dev, _ei_ctx_touch

    if not _HAS_SNEGG:
        log.error("snegg not installed — libei backend unavailable.")
        _shutdown.set()
        return

    log.info("Requesting TOUCHSCREEN permission via XDG RemoteDesktop portal…")
    log.info("▶  Watch for the compositor popup 'Allow Remote Control' and click Allow.")

    # Phase 1: oeffis portal handshake (TOUCH ONLY)
    oef = oeffis.Oeffis.create(devices=oeffis.DeviceType.TOUCHSCREEN)
    
    eis_fd: Optional[int] = None

    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and not _shutdown.is_set():
        r, _, _ = select.select([oef.fd.fileno()], [], [], 1.0)
        
        if r and oef.dispatch():
            eis_fd = oef.eis_fd
            break

    if eis_fd is None:
        log.error("Portal timed out — user must click Allow on the popup.")
        _shutdown.set()
        return

    log.info("Portal granted — Touch fd=%d", eis_fd)

    # Phase 2: snegg.ei.Sender connections
    # We MUST keep a reference to the Python file object, otherwise Python's GC
    # will automatically close the underlying fd, causing Bad file descriptor!
    global _io_fd
    _io_fd = os.fdopen(eis_fd, "rb", buffering=0)
    ctx = ei.Sender.create_for_fd(_io_fd, name="Virtual-TabletDisplay")

    touch_dev = None
    connected = False

    deadline2 = time.monotonic() + 20.0
    while time.monotonic() < deadline2 and not _shutdown.is_set():
        r, _, _ = select.select([ctx.fd], [], [], 0.1)
        
        if ctx.fd in r:
            ctx.dispatch()
            for event in ctx.events:
                raw = int(_libei.event_get_type(event._cobject))
                if raw == _EV_CONNECT:
                    connected = True
                elif raw == _EV_SEAT_ADDED:
                    event.seat.bind((ei.DeviceCapability.TOUCH,))
                elif raw == _EV_DEVICE_ADDED:
                    dev = event.device
                    caps = dev.capabilities if dev else ()
                    if dev and ei.DeviceCapability.TOUCH in caps and touch_dev is None:
                        touch_dev = dev
                        dev.start_emulating()
                        log.info("start_emulating() sent — TOUCH device READY ✓")
                elif raw == _EV_DISCONNECT:
                    log.error("Touch Compositor disconnected!")
                    _shutdown.set()
                    return

        if connected and touch_dev:
            break

    if not touch_dev:
        log.error("libei setup incomplete. Touch disabled.")
        _shutdown.set()
        return

    _touch_dev = touch_dev
    _ei_ctx_touch = ctx
    
    log.info("Touch daemon ready — screen %dx%d", SCREEN_W, SCREEN_H)
    _portal_ready.set()

    # Phase 3: spin dispatch loop forever.
    log.info("Entering libei dispatch loop…")
    while not _shutdown.is_set():
        r, _, _ = select.select([ctx.fd], [], [], 0.05)
        with _ei_lock:
            if ctx.fd in r:
                ctx.dispatch()
                for event in ctx.events:
                    if int(_libei.event_get_type(event._cobject)) == _EV_DISCONNECT:
                        _shutdown.set()
                        break
            else:
                ctx.dispatch()

# ── uinput setup (Hyprland) ────────────────────────────────────────────────────
def _setup_uinput() -> None:
    """Create a virtual multitouch device via evdev/uinput for Hyprland.

    This bypasses the XDG RemoteDesktop portal entirely — Hyprland picks up
    the uinput device through libinput like any physical touchscreen.
    Requires write access to /dev/uinput (root or udev rule).
    """
    global _uinput_dev

    if not _HAS_EVDEV:
        log.error("python-evdev not installed — uinput backend unavailable.")
        log.error("Install it:  pip install evdev   (or pacman -S python-evdev)")
        _shutdown.set()
        return

    vm = _get_virtual_monitor_rect()
    if vm:
        _, _, vw, vh = vm
    else:
        vw, vh = float(SCREEN_W), float(SCREEN_H)
    max_x, max_y = int(vw), int(vh)

    log.info("Creating uinput virtual touchscreen: %dx%d", max_x, max_y)

    cap = {
        e_codes.EV_ABS: [
            (e_codes.ABS_MT_SLOT,         evdev.AbsInfo(value=0, min=0, max=9, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_TRACKING_ID,  evdev.AbsInfo(value=0, min=0, max=65535, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_POSITION_X,   evdev.AbsInfo(value=0, min=0, max=max_x, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_POSITION_Y,   evdev.AbsInfo(value=0, min=0, max=max_y, fuzz=0, flat=0, resolution=0)),
        ],
        e_codes.EV_KEY: [e_codes.BTN_TOUCH],
    }

    try:
        ui = UInput(cap, name="Monitorize-Touch", bustype=e_codes.BUS_USB)
        _uinput_dev = ui
        log.info("uinput device created: %s  (fd=%d)", ui.device.path, ui.fd)
        log.info("Touch daemon ready (uinput) — screen %dx%d", SCREEN_W, SCREEN_H)
        _portal_ready.set()

        # Keep thread alive so UInput is not garbage-collected
        while not _shutdown.is_set():
            time.sleep(0.5)

    except PermissionError:
        log.error("Cannot open /dev/uinput — run touch_daemon as root or add a udev rule:")
        log.error('  echo \'KERNEL=="uinput", MODE="0660", GROUP="input"\' | sudo tee /etc/udev/rules.d/99-uinput.rules')
        log.error("  sudo udevadm control --reload-rules && sudo udevadm trigger")
        log.error("  # Then add your user to the 'input' group:  sudo usermod -aG input $USER")
        _shutdown.set()
    except Exception as exc:
        log.error("Failed to create uinput device: %s", exc, exc_info=True)
        _shutdown.set()

# ── TCP server ─────────────────────────────────────────────────────────────────
def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return bytes()
        buf.extend(chunk)
    return bytes(buf)

def _handle_client(client: socket.socket, addr: tuple) -> None:
    log.info("Android connected from %s", addr)
    pkt_count = 0
    try:
        # Read the first 32 bytes to see EXACTLY what Android is sending!
        first_chunk = client.recv(32, socket.MSG_PEEK)
        log.warning("[DEBUG] First chunk from Android (hex): %s", first_chunk.hex())

        while not _shutdown.is_set():
            # Try to read 1 byte first. If it's 0x00, it's probably a 4-byte length prefix.
            b1 = _read_exact(client, 1)
            if not b1: break

            if b1[0] == 0x00:
                # Modern framing: 4-byte length prefix (00 00 00 0D)
                _read_exact(client, 3) # read the rest of the length (00 00 0D)
                pkt_type_bytes = _read_exact(client, 1)
                pkt_type = pkt_type_bytes[0]
                length = 13
            else:
                # Legacy framing: No length prefix, the first byte IS the type!
                pkt_type = b1[0]
                length = 12 # Old framing only sent 12 bytes of payload?
                # Actually, if PAYLOAD_FMT requires 13 bytes, we read 13 bytes.
                # But wait, if type was the first byte, we read 12 more. Let's read 13 bytes total anyway.
                # Actually, let's just assume payload is 13 bytes for PKT_TOUCH.
                length = 13
            
            if pkt_type not in (PKT_TOUCH, PKT_PEN):
                log.warning("Unknown packet type 0x%02x, closing connection.", pkt_type)
                break

            payload = _read_exact(client, length)
            if len(payload) < PAYLOAD_SIZE:
                log.warning("Short payload, skipping.")
                continue

            unpacked = struct.unpack(PAYLOAD_FMT, payload[:PAYLOAD_SIZE])
            action, tool, cid, nx, ny, pressure, tx, ty = unpacked
            pkt_count += 1

            if pkt_count == 1:
                log.info("[TCP] First packet parsed successfully! type=0x%02x", pkt_type)

            log.debug("[TCP] pkt#%d type=0x%02x action=%d cid=%d norm=(%d,%d)",
                      pkt_count, pkt_type, action, cid, nx, ny)

            _inject_fn(action, cid, nx, ny)

    except EOFError:
        log.info("Android disconnected cleanly")
    except Exception as e:
        if not _shutdown.is_set():
            log.error("Client error: %s", e)
    finally:
        client.close()
        _active_touches.clear()

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
    if _uinput_dev:
        try: _uinput_dev.close()
        except Exception: pass
    sys.exit(0)

def main():
    global _inject_fn
    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    log.info("touch_daemon.py — screen %dx%d  DE=%s", SCREEN_W, SCREEN_H, _DETECTED_DE)

    # Choose backend based on desktop environment
    if _DETECTED_DE == "hyprland":
        log.info("Using uinput backend (Hyprland does not support RemoteDesktop portal)")
        _inject_fn = _inject_touch_uinput
        threading.Thread(target=_setup_uinput, daemon=True).start()
    else:
        log.info("Using libei backend (XDG RemoteDesktop portal)")
        _inject_fn = _inject_touch_libei
        threading.Thread(target=_setup_libei, daemon=True).start()

    threading.Thread(target=_run_tcp_server, daemon=True).start()

    # Main thread keeps process alive
    try:
        while not _shutdown.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        _cleanup()

if __name__ == "__main__":
    main()
