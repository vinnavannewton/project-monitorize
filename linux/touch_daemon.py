
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


_HAS_SNEGG = False
try:
    import snegg.ei as ei
    import snegg.oeffis as oeffis
    _HAS_SNEGG = True
except ImportError:
    ei = None       
    oeffis = None   


_HAS_EVDEV = False
try:
    import evdev
    from evdev import UInput, ecodes as e_codes
    _HAS_EVDEV = True
except ImportError:
    evdev = None   


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
_EV_START_EMULATING  = 200   


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
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)   

logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
    format="[TouchDaemon] %(levelname)s %(message)s",
)
log = logging.getLogger("TouchDaemon")
if _DEBUG:
    log.debug("DEBUG mode enabled")


_ei_ctx       = None   
_touch_dev    = None   
_pen_dev      = None   
_uinput_dev   = None   
_ei_lock       = threading.Lock()
_portal_ready  = threading.Event()
_shutdown      = threading.Event()




_active_touches: dict = {}
_inject_fn = None   


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
                return (float(pos["x"]), float(pos["y"]),
                        float(size["width"]/scale), float(size["height"]/scale))
        
        for output in data.get("outputs", []):
            if output.get("primary") or output.get("enabled"):
                pos = output.get("pos", {"x": 0, "y": 0})
                size = output.get("size", {"width": 1280, "height": 800})
                scale = output.get("scale", 1.0)
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
            
            
            if name.startswith("HEADLESS") or name.lower().startswith("virtual-tabletdisplay"):
                x = float(mon.get("x", 0))
                y = float(mon.get("y", 0))
                w = float(mon.get("width", SCREEN_W))
                h = float(mon.get("height", SCREEN_H))
                scale = float(mon.get("scale", 1.0))
                
                log.info("Found Hyprland headless monitor %s at (%.0f, %.0f) %dx%d scale=%.1f",
                         name, x, y, int(w), int(h), scale)
                return (x, y, w / scale, h / scale)
    except Exception as e:
        log.warning("Failed to query hyprctl monitors: %s", e)
    return None

def _get_virtual_monitor_rect_gnome() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the GNOME virtual monitor by querying D-Bus."""
    try:
        import dbus
        bus = dbus.SessionBus()
        obj = bus.get_object('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig')
        dc = dbus.Interface(obj, 'org.gnome.Mutter.DisplayConfig')
        serial_num, pms, lms, props = dc.GetCurrentState()

        for pm in pms:
            connector = str(pm[0][0])
            
            if any(name in connector.lower() for name in ("virtual", "meta")):
                
                w, h = None, None
                for mode in pm[1]:
                    if mode[6].get('is-current'):
                        w = float(mode[1])
                        h = float(mode[2])
                        break
                if w is None and pm[1]:
                    w = float(pm[1][0][1])
                    h = float(pm[1][0][2])
                
                if w is not None:
                    
                    for lm in lms:
                        for mon in lm[5]:
                            if str(mon[0]) == connector:
                                x = float(lm[0])
                                y = float(lm[1])
                                scale = float(lm[2])
                                log.info("Found GNOME virtual monitor %s via D-Bus at (%.0f, %.0f) %dx%d scale=%.2f",
                                         connector, x, y, int(w), int(h), scale)
                                return (x, y, w / scale, h / scale)
    except Exception as e:
        log.warning("Failed to query GNOME Mutter DisplayConfig: %s", e)
    return None

def _get_virtual_monitor_rect() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the virtual monitor, dispatching by DE."""
    global _virtual_monitor_cache
    if _virtual_monitor_cache is not None:
        return _virtual_monitor_cache

    if _DETECTED_DE == "hyprland":
        result = _get_virtual_monitor_rect_hyprland()
    elif _DETECTED_DE == "kde":
        result = _get_virtual_monitor_rect_kde()
    elif _DETECTED_DE == "gnome":
        result = _get_virtual_monitor_rect_gnome()
    else:
        
        result = (_get_virtual_monitor_rect_hyprland()
                  or _get_virtual_monitor_rect_kde()
                  or _get_virtual_monitor_rect_gnome())

    if result is not None:
        _virtual_monitor_cache = result
    else:
        log.info("No virtual monitor found, caching default screen geometry: (0, 0, %d, %d)", SCREEN_W, SCREEN_H)
        _virtual_monitor_cache = (0.0, 0.0, float(SCREEN_W), float(SCREEN_H))
    return _virtual_monitor_cache

def _scale(dev, nx: int, ny: int) -> tuple[float, float]:
    """Map Android 0-65535 normalised coords to the Virtual Monitor region."""
    
    
    vm_rect = _get_virtual_monitor_rect()
    target_rx, target_ry = 0.0, 0.0
    
    if vm_rect:
        target_rx, target_ry, _, _ = vm_rect
    
    
    best_reg = None
    best_rx, best_ry, best_rw, best_rh = 0.0, 0.0, float(SCREEN_W), float(SCREEN_H)
    
    if dev.regions:
        best_reg = dev.regions[0] 
        for reg in dev.regions:
            rw, rh = reg.dimension
            rx, ry = 0.0, 0.0
            try:
                rx = float(_libei.region_get_x(reg._cobject))
                ry = float(_libei.region_get_y(reg._cobject))
            except Exception:
                pass
            
            
            if abs(rx - target_rx) < 5 and abs(ry - target_ry) < 5:
                best_rx, best_ry, best_rw, best_rh = rx, ry, rw, rh
                break
        else:
            
            try:
                best_rx = float(_libei.region_get_x(best_reg._cobject))
                best_ry = float(_libei.region_get_y(best_reg._cobject))
                best_rw, best_rh = best_reg.dimension
            except Exception:
                pass

    x = best_rx + (nx / COORD_MAX) * best_rw
    y = best_ry + (ny / COORD_MAX) * best_rh
    return x, y


def _inject_touch_libei(action: int, cid: int, nx: int, ny: int, frame: bool = True) -> None:
    dev = _touch_dev
    if dev is None:
        return

    x, y = _scale(dev, nx, ny)
    log.debug("touch action=%d cid=%d → (%.1f, %.1f)", action, cid, x, y)

    caps = dev.capabilities if dev else ()
    is_touch = ei.DeviceCapability.TOUCH in caps

    try:
        with _ei_lock:
            if is_touch:
                if action == ACTION_DOWN:
                    
                    touch = dev.touch_new()
                    _active_touches[cid] = touch
                    touch.down(x, y)
                    log.info("[INJECT] DOWN  cid=%d  coords=(%.1f, %.1f)  active_slots=%d",
                             cid, x, y, len(_active_touches))

                elif action == ACTION_MOVE:
                    touch = _active_touches.get(cid)
                    if touch is not None:
                        touch.motion(x, y)
                    else:
                        log.warning("[INJECT] MOVE  cid=%d  — no active touch slot!", cid)

                elif action == ACTION_UP:
                    touch = _active_touches.pop(cid, None)
                    if touch is not None:
                        touch.up()
                        log.info("[INJECT] UP    cid=%d  coords=(%.1f, %.1f)  remaining=%d",
                                 cid, x, y, len(_active_touches))
                    else:
                        log.warning("[INJECT] UP    cid=%d  — no active touch slot!", cid)
            else:
                
                btn_left = 0x110
                if action == ACTION_DOWN:
                    dev.pointer_motion_absolute(x, y)
                    dev.button_button(btn_left, True)
                    _active_touches[cid] = True
                    log.info("[INJECT POINTER] DOWN  cid=%d  coords=(%.1f, %.1f)", cid, x, y)
                elif action == ACTION_MOVE:
                    dev.pointer_motion_absolute(x, y)
                elif action == ACTION_UP:
                    _active_touches.pop(cid, None)
                    dev.pointer_motion_absolute(x, y)
                    dev.button_button(btn_left, False)
                    log.info("[INJECT POINTER] UP    cid=%d  coords=(%.1f, %.1f)", cid, x, y)

            if frame:
                if dev:
                    dev.frame()
                if _ei_ctx:
                    _ei_ctx.dispatch()

    except Exception as exc:
        log.error("inject_touch error cid=%d action=%d: %s", cid, action, exc, exc_info=True)


def _inject_touch_uinput(action: int, cid: int, nx: int, ny: int, frame: bool = True) -> None:
    """Inject touch via evdev/uinput virtual touchscreen (Hyprland backend)."""
    ui = _uinput_dev
    if ui is None:
        return

    
    vm = _get_virtual_monitor_rect()
    if vm:
        _, _, vw, vh = vm
    else:
        vw, vh = float(SCREEN_W), float(SCREEN_H)

    abs_x = int((nx / COORD_MAX) * vw)
    abs_y = int((ny / COORD_MAX) * vh)
    slot = cid % 10   

    try:
        with _ei_lock:
            if action == ACTION_DOWN:
                _active_touches[cid] = slot
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, slot)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_TRACKING_ID, cid & 0xFFFF)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_X, abs_x)
                ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_Y, abs_y)
                if slot == 0:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_X, abs_x)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_Y, abs_y)
                ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 1)
                log.info("[UINPUT] DOWN  cid=%d slot=%d coords=(%d, %d)  active=%d",
                         cid, slot, abs_x, abs_y, len(_active_touches))

            elif action == ACTION_MOVE:
                s = _active_touches.get(cid)
                if s is not None:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, s)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_X, abs_x)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_POSITION_Y, abs_y)
                    if s == 0:
                        ui.write(e_codes.EV_ABS, e_codes.ABS_X, abs_x)
                        ui.write(e_codes.EV_ABS, e_codes.ABS_Y, abs_y)

            elif action == ACTION_UP:
                s = _active_touches.pop(cid, None)
                if s is not None:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_SLOT, s)
                    ui.write(e_codes.EV_ABS, e_codes.ABS_MT_TRACKING_ID, -1)
                    if not _active_touches:
                        ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 0)
                    log.info("[UINPUT] UP    cid=%d slot=%d  remaining=%d",
                             cid, s, len(_active_touches))

            if frame:
                ui.syn()

    except Exception as exc:
        log.error("inject_touch_uinput error cid=%d action=%d: %s", cid, action, exc, exc_info=True)

def _inject_pen(action: int, tool: int, nx: int, ny: int, pressure: int, tx: int, btn_state: int, frame: bool = True) -> None:
    global _pen_dev, _touch_dev
    dev = _pen_dev if _pen_dev is not None else _touch_dev
    if dev is None:
        return

    x, y = _scale(dev, nx, ny)
    
    
    
    is_secondary = (btn_state & 32) != 0 or (tool == 2)
    
    
    
    button_code = 0x111 if is_secondary else 0x110
    
    try:
        with _ei_lock:
            if action == ACTION_DOWN:
                dev.pointer_motion_absolute(x, y)
                dev.button_button(button_code, True)
                log.info("[INJECT PEN] DOWN  coords=(%.1f, %.1f) tool=%d btn=0x%x", x, y, tool, button_code)
            elif action == ACTION_MOVE:
                dev.pointer_motion_absolute(x, y)
                
            elif action == ACTION_UP:
                dev.pointer_motion_absolute(x, y)
                dev.button_button(button_code, False)
                
                other_btn = 0x110 if is_secondary else 0x111
                dev.button_button(other_btn, False)
                log.info("[INJECT PEN] UP    coords=(%.1f, %.1f)", x, y)
            elif action == ACTION_HOVER:
                dev.pointer_motion_absolute(x, y)
                

            if frame:
                if dev:
                    dev.frame()
                if _ei_ctx:
                    _ei_ctx.dispatch()
    except Exception as exc:
        log.error("inject_pen error action=%d: %s", action, exc, exc_info=True)


def _setup_libei() -> None:
    """Run the full portal handshake, set up libei, then spin the dispatch loop."""
    global _touch_dev, _pen_dev, _ei_ctx

    if not _HAS_SNEGG:
        log.error("snegg not installed — libei backend unavailable.")
        _shutdown.set()
        return

    log.info("Requesting TOUCHSCREEN/POINTER permissions via XDG RemoteDesktop portal…")
    log.info("▶  Watch for the compositor popup 'Allow Remote Control' and click Allow.")

    devices_to_try = [
        oeffis.DeviceType.TOUCHSCREEN | oeffis.DeviceType.POINTER,
        oeffis.DeviceType.ALL_DEVICES,
        oeffis.DeviceType.POINTER
    ]

    oef = None
    eis_fd = None

    for idx, devices in enumerate(devices_to_try):
        try:
            log.info("Creating RemoteDesktop session request (try %d/%d)...", idx + 1, len(devices_to_try))
            oef = oeffis.Oeffis.create(devices=devices)
            
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline and not _shutdown.is_set():
                r, _, _ = select.select([oef.fd.fileno()], [], [], 1.0)
                if r:
                    if oef.dispatch():
                        eis_fd = oef.eis_fd
                        break
            if eis_fd is not None:
                break
        except oeffis.SessionClosedError:
            log.error("Portal session closed/denied by user.")
            break
        except oeffis.DisconnectedError as de:
            msg = getattr(de, "message", None) or str(de)
            log.warning("Portal disconnected during request (try %d): %s", idx + 1, msg)
            continue
        except Exception as e:
            log.warning("Failed to dispatch portal request (try %d): %s", idx + 1, e)
            continue

    if eis_fd is None:
        log.error("Portal timed out, connection failed, or permission denied — user must click Allow on the popup.")
        
        
        
        return

    log.info("Portal granted — Touch/Pointer fd=%d", eis_fd)

    
    
    
    global _io_fd
    _io_fd = os.fdopen(eis_fd, "rb", buffering=0)
    ctx = ei.Sender.create_for_fd(_io_fd, name="Virtual-TabletDisplay")

    touch_dev = None
    pen_dev = None
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
                    log.info("Seat added: %s, caps: %s", event.seat.name, event.seat.capabilities)
                    event.seat.bind(event.seat.capabilities)
                elif raw == _EV_DEVICE_ADDED:
                    dev = event.device
                    caps = dev.capabilities if dev else ()
                    if dev:
                        if ei.DeviceCapability.TOUCH in caps:
                            touch_dev = dev
                            dev.start_emulating()
                            log.info("start_emulating() sent — TOUCH device READY ✓")
                        elif ei.DeviceCapability.POINTER_ABSOLUTE in caps:
                            pen_dev = dev
                            dev.start_emulating()
                            log.info("start_emulating() sent — POINTER_ABSOLUTE device READY ✓")
                elif raw == _EV_DISCONNECT:
                    log.error("Touch Compositor disconnected!")
                    _shutdown.set()
                    return

        if connected and (touch_dev or pen_dev):
            break

    
    if not touch_dev and pen_dev:
        touch_dev = pen_dev
        pen_dev = None

    if not touch_dev:
        log.error("libei setup incomplete. Touch disabled.")
        _shutdown.set()
        return

    _touch_dev = touch_dev
    _pen_dev = pen_dev
    _ei_ctx = ctx
    
    log.info("Touch daemon ready — screen %dx%d", SCREEN_W, SCREEN_H)
    _portal_ready.set()

    
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

    
    monitor_name = None
    try:
        import subprocess, json
        res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True)
        if res.returncode == 0:
            monitors = json.loads(res.stdout)
            for mon in monitors:
                name = mon.get("name", "")
                if name.startswith("HEADLESS") or name.lower().startswith("virtual-tabletdisplay"):
                    monitor_name = name
                    break
    except Exception as e:
        log.warning("Failed to query monitor name for uinput mapping: %s", e)

    cap = {
        e_codes.EV_ABS: [
            (e_codes.ABS_X,               evdev.AbsInfo(value=0, min=0, max=max_x, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_Y,               evdev.AbsInfo(value=0, min=0, max=max_y, fuzz=0, flat=0, resolution=0)),
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

        
        if monitor_name:
            log.info("Mapping touch device 'monitorize-touch' to monitor '%s'", monitor_name)
            res = subprocess.run(["hyprctl", "keyword", "device:monitorize-touch:output", monitor_name], capture_output=True, text=True)
            log.info("hyprctl mapping output: stdout=%r stderr=%r", res.stdout, res.stderr)
        else:
            log.info("No headless monitor found, mapping to default 'HEADLESS-1'")
            res = subprocess.run(["hyprctl", "keyword", "device:monitorize-touch:output", "HEADLESS-1"], capture_output=True, text=True)
            log.info("hyprctl mapping output: stdout=%r stderr=%r", res.stdout, res.stderr)

        log.info("Waiting 2.0 seconds for compositor to detect and configure the new touch device...")
        time.sleep(2.0)

        log.info("Touch daemon ready (uinput) — screen %dx%d", SCREEN_W, SCREEN_H)
        _portal_ready.set()

        
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
    buffer = bytearray()
    try:
        
        first_chunk = client.recv(32, socket.MSG_PEEK)
        log.warning("[DEBUG] First chunk from Android (hex): %s", first_chunk.hex())

        while not _shutdown.is_set():
            chunk = client.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)

            
            
            packets_to_process = []
            while len(buffer) >= 18:
                if buffer[0:4] == b'\x00\x00\x00\x0d':
                    pkt_type = buffer[4]
                    payload = buffer[5:18]
                    packets_to_process.append((pkt_type, payload))
                    del buffer[0:18]
                else:
                    
                    del buffer[0]

            if packets_to_process:
                num_packets = len(packets_to_process)
                for idx, (pkt_type, payload) in enumerate(packets_to_process):
                    if pkt_type not in (PKT_TOUCH, PKT_PEN):
                        log.warning("Unknown packet type 0x%02x, skipping.", pkt_type)
                        continue

                    unpacked = struct.unpack(PAYLOAD_FMT, payload)
                    action, tool, cid, nx, ny, pressure, tx, ty = unpacked
                    pkt_count += 1

                    if pkt_count == 1:
                        log.info("[TCP] First packet parsed successfully! type=0x%02x", pkt_type)

                    log.debug("[TCP] pkt#%d type=0x%02x action=%d cid=%d norm=(%d,%d)",
                              pkt_count, pkt_type, action, cid, nx, ny)

                    
                    is_last = (idx == num_packets - 1)
                    if pkt_type == PKT_TOUCH or _DETECTED_DE == "hyprland":
                        _inject_fn(action, cid, nx, ny, frame=is_last)
                    else:
                        _inject_pen(action, tool, nx, ny, pressure, tx, ty, frame=is_last)

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
    
    _sp.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(0.5)   

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

def _run_udp_server() -> None:
    """
    Linux is the UDP SERVER on port 7113 for Wi-Fi touch.
    Android sends raw datagrams matching the TCP framing.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        server.bind(("0.0.0.0", 7113))
    except OSError as e:
        log.error("[UDP] Could not bind port 7113 — touch disabled. %s", e)
        return

    server.settimeout(1.0)
    log.info("[UDP] Server listening on 0.0.0.0:7113 (waiting for Android over Wi-Fi)")

    pkt_count = 0
    last_packet_time = 0.0

    while not _shutdown.is_set():
        try:
            data, addr = server.recvfrom(64)
            
            
            
            
            current_time = time.monotonic()
            if current_time - last_packet_time > 3.0:
                global _virtual_monitor_cache
                _virtual_monitor_cache = None
            last_packet_time = current_time

            if len(data) >= 13:
                
                offset = 4 if data[0] == 0x00 else 0
                if len(data) >= offset + 14:
                    payload = data[offset:offset+14]
                    pkt_type = payload[0]
                    if pkt_type in (PKT_TOUCH, PKT_PEN):
                        action, tool, cid, nx, ny, pr, tx, btn = struct.unpack(PAYLOAD_FMT, payload[1:14])
                        
                        pkt_count += 1
                        if pkt_count % 100 == 1 and _DEBUG:
                            log.debug("[UDP] pkt#%d type=%d action=%d cid=%d nx=%d ny=%d",
                                      pkt_count, pkt_type, action, cid, nx, ny)
                        
                        if pkt_type == PKT_TOUCH or _DETECTED_DE == "hyprland":
                            _inject_fn(action, cid, nx, ny)
                        else:
                            _inject_pen(action, tool, nx, ny, pr, tx, btn)
        except socket.timeout:
            continue
        except Exception as e:
            if not _shutdown.is_set():
                log.error("[UDP] recv error: %s", e)
    server.close()




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

    if _DETECTED_DE == "hyprland":
        log.info("Using uinput backend (Hyprland does not support RemoteDesktop portal)")
        _inject_fn = _inject_touch_uinput
        threading.Thread(target=_setup_uinput, daemon=True).start()
    else:
        log.info("Using libei backend (XDG RemoteDesktop portal)")
        _inject_fn = _inject_touch_libei
        threading.Thread(target=_setup_libei, daemon=True).start()

    if "--wifi" in sys.argv:
        threading.Thread(target=_run_udp_server, daemon=True).start()
    else:
        threading.Thread(target=_run_tcp_server, daemon=True).start()

    
    try:
        while not _shutdown.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        _cleanup()

if __name__ == "__main__":
    main()
