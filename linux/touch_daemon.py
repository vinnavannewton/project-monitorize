"""
touch_daemon.py — Monitorize Wayland touch injector.

KDE / GNOME: uses libei via snegg + XDG RemoteDesktop portal by default,
             or uinput-only when --stylus-features is enabled.
Hyprland/Sway: use evdev/uinput because their portal backends do not implement
               the RemoteDesktop portal.

Usage:
  python3 touch_daemon.py [width] [height] [--wifi] [--stylus-features] [--stylus-only] [--debug]
  Defaults: 2560 1600

Pass --debug for full verbose output (recommended when diagnosing touch issues).
"""

import sys, os, select, struct, socket, signal, logging, threading, time, ctypes
import json
import subprocess
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
PKT_PEN_EXT  = 0x05
ACTION_DOWN  = 0
ACTION_MOVE  = 1
ACTION_UP    = 2
ACTION_HOVER = 3

PAYLOAD_FMT  = ">BBBHHHhh"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)   
PEN_EXT_FMT  = ">BBBHHHhhHHH"
PEN_EXT_SIZE = struct.calcsize(PEN_EXT_FMT)

PEN_FLAG_CANCELED   = 1
PEN_FLAG_HOVER_EXIT = 1 << 1

ANDROID_STYLUS_PRIMARY   = 0x20
ANDROID_STYLUS_SECONDARY = 0x40
DISTANCE_MAX = 1024
STYLUS_TOUCH_SUPPRESSION_SECS = 5.0

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
_uinput_touch_dev = None
_uinput_stylus_dev = None
_uinput_max_x = SCREEN_W
_uinput_max_y = SCREEN_H
_uinput_target_x = 0.0
_uinput_target_y = 0.0
_uinput_target_w = float(SCREEN_W)
_uinput_target_h = float(SCREEN_H)
_ei_lock       = threading.Lock()
_portal_ready  = threading.Event()
_shutdown      = threading.Event()

_active_touches: dict = {}
_stylus_active_tool = None
_inject_fn = None
_pen_inject_fn = None
_STYLUS_FEATURES = "--stylus-features" in sys.argv
_STYLUS_ONLY = "--stylus-only" in sys.argv
_logged_dropped_pen = False
_last_stylus_input_time = 0.0
_active_finger_touches: dict[int, tuple[int, int]] = {}


def _pen_touch_cid(cid: int) -> int:
    """Keep pen-as-touch fallback slots separate from finger touch slots."""
    return 10005 + (cid % 5)


def _finger_touch_is_suppressed() -> bool:
    if _STYLUS_ONLY:
        return True
    if _last_stylus_input_time <= 0:
        return False
    return (time.monotonic() - _last_stylus_input_time) < STYLUS_TOUCH_SUPPRESSION_SECS

def _release_active_finger_touches() -> None:
    if not _active_finger_touches or _inject_fn is None:
        _active_finger_touches.clear()
        return

    items = list(_active_finger_touches.items())
    for index, (cid, (nx, ny)) in enumerate(items):
        _inject_fn(ACTION_UP, cid, nx, ny, frame=(index == len(items) - 1))
    _active_finger_touches.clear()


def _dispatch_touch_packet(action: int, cid: int, nx: int, ny: int, frame: bool = True) -> None:
    if _inject_fn is None:
        return

    if _finger_touch_is_suppressed():
        if cid in _active_finger_touches:
            last_x, last_y = _active_finger_touches.pop(cid)
            release_x = nx if action == ACTION_UP else last_x
            release_y = ny if action == ACTION_UP else last_y
            _inject_fn(ACTION_UP, cid, release_x, release_y, frame=frame)
        return

    _inject_fn(action, cid, nx, ny, frame=frame)
    if action == ACTION_DOWN:
        _active_finger_touches[cid] = (nx, ny)
    elif action == ACTION_MOVE and cid in _active_finger_touches:
        _active_finger_touches[cid] = (nx, ny)
    elif action == ACTION_UP:
        _active_finger_touches.pop(cid, None)


def _detect_de() -> str:
    """Detect desktop environment."""
    hypr = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    sway = os.environ.get("SWAYSOCK", "")
    xdg  = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION", "").lower()
    combined = xdg + " " + dsess
    if hypr or "hyprland" in combined:
        return "hyprland"
    if sway or "sway" in combined:
        return "sway"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    return "unknown"

_DETECTED_DE = _detect_de()
log.info("Detected DE: %s", _DETECTED_DE)


_virtual_monitor_cache = None


def _json_command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else []


def _headless_output(outputs):
    return next(
        (
            output for output in outputs
            if output.get("name", "").startswith("HEADLESS")
            or output.get("name", "").lower().startswith("virtual-tabletdisplay")
        ),
        None,
    )


def _get_virtual_monitor_rect_kde() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of Virtual-TabletDisplay from kscreen-doctor."""
    try:
        data = _json_command(["kscreen-doctor", "-j"])
        
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
        monitor = _headless_output(_json_command(["hyprctl", "monitors", "-j"]))
        if monitor:
            x = float(monitor.get("x", 0))
            y = float(monitor.get("y", 0))
            w = float(monitor.get("width", SCREEN_W))
            h = float(monitor.get("height", SCREEN_H))
            scale = float(monitor.get("scale", 1.0))
            log.info(
                "Found Hyprland headless monitor %s at (%.0f, %.0f) %dx%d scale=%.1f",
                monitor.get("name", ""), x, y, int(w), int(h), scale,
            )
            return (x, y, w / scale, h / scale)
    except Exception as e:
        log.warning("Failed to query hyprctl monitors: %s", e)
    return None

def _get_virtual_monitor_rect_sway() -> tuple[float, float, float, float]:
    try:
        output_name = os.environ.get("MONITORIZE_OUTPUT", "")
        outputs = _json_command(["swaymsg", "-t", "get_outputs", "-r"])
        target = next(
            (
                output for output in outputs
                if output.get("name") == output_name
                or (not output_name and output.get("name", "").startswith("HEADLESS"))
            ),
            None,
        )
        if target:
            rect = target.get("rect", {})
            return (
                float(rect.get("x", 0)),
                float(rect.get("y", 0)),
                float(rect.get("width", SCREEN_W)),
                float(rect.get("height", SCREEN_H)),
            )
    except Exception as e:
        log.warning("Failed to query Sway outputs: %s", e)
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

def _get_gnome_desktop_bounds() -> tuple[float, float, float, float]:
    """Return GNOME logical desktop bounds as (min_x, min_y, width, height)."""
    try:
        import dbus
        bus = dbus.SessionBus()
        obj = bus.get_object('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig')
        dc = dbus.Interface(obj, 'org.gnome.Mutter.DisplayConfig')
        serial_num, pms, lms, props = dc.GetCurrentState()

        connector_sizes = {}
        for pm in pms:
            connector = str(pm[0][0])
            for mode in pm[1]:
                if mode[6].get('is-current'):
                    connector_sizes[connector] = (float(mode[1]), float(mode[2]))
                    break
            if connector not in connector_sizes and pm[1]:
                connector_sizes[connector] = (float(pm[1][0][1]), float(pm[1][0][2]))

        min_x = min_y = None
        max_x = max_y = None
        for lm in lms:
            x = float(lm[0])
            y = float(lm[1])
            scale = float(lm[2]) or 1.0
            width = height = 0.0
            for mon in lm[5]:
                size = connector_sizes.get(str(mon[0]))
                if size:
                    width = max(width, size[0] / scale)
                    height = max(height, size[1] / scale)
            if width <= 0 or height <= 0:
                continue
            min_x = x if min_x is None else min(min_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_x = x + width if max_x is None else max(max_x, x + width)
            max_y = y + height if max_y is None else max(max_y, y + height)

        if min_x is not None and min_y is not None and max_x is not None and max_y is not None:
            return (min_x, min_y, max_x - min_x, max_y - min_y)
    except Exception as e:
        log.warning("Failed to query GNOME desktop bounds: %s", e)
    return None

def _get_virtual_monitor_rect() -> tuple[float, float, float, float]:
    """Return (x, y, width, height) of the virtual monitor, dispatching by DE."""
    global _virtual_monitor_cache
    if _virtual_monitor_cache is not None:
        return _virtual_monitor_cache

    if _DETECTED_DE == "hyprland":
        result = _get_virtual_monitor_rect_hyprland()
    elif _DETECTED_DE == "sway":
        result = _get_virtual_monitor_rect_sway()
    elif _DETECTED_DE == "kde":
        result = _get_virtual_monitor_rect_kde()
    elif _DETECTED_DE == "gnome":
        result = _get_virtual_monitor_rect_gnome()
    else:
        
        result = (_get_virtual_monitor_rect_hyprland()
                  or _get_virtual_monitor_rect_sway()
                  or _get_virtual_monitor_rect_kde()
                  or _get_virtual_monitor_rect_gnome())

    if result is not None:
        _virtual_monitor_cache = result
    else:
        log.info("No virtual monitor found, caching default screen geometry: (0, 0, %d, %d)", SCREEN_W, SCREEN_H)
        _virtual_monitor_cache = (0.0, 0.0, float(SCREEN_W), float(SCREEN_H))
    return _virtual_monitor_cache

def _configure_uinput_geometry() -> None:
    """Configure absolute uinput coordinates for the active compositor."""
    global _uinput_max_x, _uinput_max_y
    global _uinput_target_x, _uinput_target_y, _uinput_target_w, _uinput_target_h

    rx, ry, rw, rh = _get_virtual_monitor_rect()
    if _DETECTED_DE == "gnome":
        bounds = _get_gnome_desktop_bounds()
        if bounds:
            bx, by, bw, bh = bounds
            _uinput_max_x = max(1, int(round(bw)))
            _uinput_max_y = max(1, int(round(bh)))
            _uinput_target_x = rx - bx
            _uinput_target_y = ry - by
            _uinput_target_w = rw
            _uinput_target_h = rh
            log.info(
                "%s uinput desktop bounds %.0fx%.0f; target offset=(%.0f, %.0f) size=%.0fx%.0f",
                _DETECTED_DE.upper(), bw, bh, _uinput_target_x, _uinput_target_y, rw, rh
            )
            return

    _uinput_max_x = max(1, int(round(rw)))
    _uinput_max_y = max(1, int(round(rh)))
    _uinput_target_x = 0.0
    _uinput_target_y = 0.0
    _uinput_target_w = rw
    _uinput_target_h = rh
    log.info(
        "%s uinput target size %.0fx%.0f",
        _DETECTED_DE.upper(), _uinput_target_w, _uinput_target_h
    )

def _uinput_coords(nx: int, ny: int) -> tuple[int, int]:
    x = _uinput_target_x + (nx / COORD_MAX) * _uinput_target_w
    y = _uinput_target_y + (ny / COORD_MAX) * _uinput_target_h
    return (
        max(0, min(_uinput_max_x, int(round(x)))),
        max(0, min(_uinput_max_y, int(round(y)))),
    )

def _get_hyprland_uinput_monitor_name() -> Optional[str]:
    try:
        monitor = _headless_output(_json_command(["hyprctl", "monitors", "-j"]))
        return monitor.get("name") if monitor else None
    except Exception as e:
        log.warning("Failed to query monitor name for uinput mapping: %s", e)
    return None

def _map_hyprland_uinput_device(device_name: str, monitor_name: Optional[str]) -> None:
    if _DETECTED_DE != "hyprland":
        return
    try:
        output = monitor_name or "HEADLESS-1"
        device_key = device_name.lower()
        log.info("Mapping uinput device '%s' to monitor '%s'", device_key, output)
        res = subprocess.run(
            ["hyprctl", "keyword", f"device:{device_key}:output", output],
            capture_output=True,
            text=True,
        )
        log.info("hyprctl mapping output: stdout=%r stderr=%r", res.stdout, res.stderr)
    except Exception as e:
        log.warning("Failed to map uinput device '%s': %s", device_name, e)

def _map_sway_uinput_devices(device_names: list[str]) -> bool:
    if _DETECTED_DE != "sway":
        return False
    output = os.environ.get("MONITORIZE_OUTPUT", "")
    if not output:
        log.error("MONITORIZE_OUTPUT is missing; refusing to map Sway input")
        return False
    try:
        deadline = time.monotonic() + 5
        normalize = lambda value: value.lower().replace("-", " ").replace("_", " ")
        pending = {normalize(name) for name in device_names}
        while pending and time.monotonic() < deadline:
            inputs = _json_command(["swaymsg", "-t", "get_inputs", "-r"])
            for item in inputs:
                name = normalize(item.get("name", ""))
                match = next((wanted for wanted in pending if wanted == name), None)
                if not match:
                    continue
                identifier = item.get("identifier", "")
                mapped = subprocess.run(
                    ["swaymsg", "input", identifier, "map_to_output", output],
                    capture_output=True, text=True,
                )
                if mapped.returncode == 0:
                    pending.remove(match)
                else:
                    log.error("Sway input mapping failed: %s", mapped.stderr.strip())
            if pending:
                time.sleep(0.2)
        if pending:
            log.error("Sway did not expose uinput devices: %s", ", ".join(sorted(pending)))
            return False
        return True
    except Exception as e:
        log.error("Failed to map Sway uinput devices: %s", e)
        return False

def _map_kde_uinput_devices_to_output(devices: list) -> set[str]:
    if _DETECTED_DE != "kde":
        return set()

    target_output = "Virtual-TabletDisplay"
    event_names = []
    for dev in devices:
        if not dev:
            continue
        path = getattr(getattr(dev, "device", None), "path", "")
        event_name = os.path.basename(path)
        if event_name:
            event_names.append(event_name)

    if not event_names:
        log.warning("KDE uinput output mapping skipped: no event device paths found")
        return set()

    try:
        import dbus
        bus = dbus.SessionBus()
        manager_obj = bus.get_object("org.kde.KWin", "/org/kde/KWin/InputDevice")
        manager_props = dbus.Interface(manager_obj, "org.freedesktop.DBus.Properties")
        kwin_obj = bus.get_object("org.kde.KWin", "/KWin")
        kwin = dbus.Interface(kwin_obj, "org.kde.KWin")

        deadline = time.monotonic() + 5.0
        pending = set(event_names)
        mapped = set()
        while pending and time.monotonic() < deadline and not _shutdown.is_set():
            try:
                known = {str(name) for name in manager_props.Get("org.kde.KWin.InputDeviceManager", "devicesSysNames")}
            except Exception:
                known = set()

            for event_name in list(pending):
                if known and event_name not in known:
                    continue

                path = f"/org/kde/KWin/InputDevice/{event_name}"
                obj = bus.get_object("org.kde.KWin", path)
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                name = str(props.Get("org.kde.KWin.InputDevice", "name"))
                props.Set("org.kde.KWin.InputDevice", "outputName", dbus.String(target_output))
                mapped.add(event_name)
                pending.remove(event_name)
                log.info("Mapped KDE uinput device %s (%s) to output %s", event_name, name, target_output)

            if pending:
                time.sleep(0.1)

        if mapped:
            try:
                kwin.reconfigure()
            except Exception as exc:
                log.debug("KWin reconfigure after uinput mapping failed: %s", exc)

        if pending:
            log.warning(
                "KDE uinput output mapping timed out for %s; those devices may stay mapped to the primary output",
                ", ".join(sorted(pending)),
            )
        return mapped
    except Exception as e:
        log.warning("Failed to map KDE uinput devices to %s: %s", target_output, e)
        return set()

def _scale(dev, nx: int, ny: int) -> tuple[float, float]:
    """Map Android 0-65535 normalised coords to the virtual monitor region."""
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
    ui = _uinput_touch_dev or _uinput_dev
    if ui is None:
        return

    abs_x, abs_y = _uinput_coords(nx, ny)
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

def _inject_stylus_uinput(
    action: int,
    tool: int,
    nx: int,
    ny: int,
    pressure: int,
    tilt_x: int,
    tilt_y: int,
    distance: int,
    btn_state: int,
    flags: int,
    frame: bool = True,
) -> None:
    """Inject a real uinput tablet/stylus event with pressure and tilt."""
    global _stylus_active_tool

    ui = _uinput_stylus_dev
    if ui is None:
        return

    abs_x, abs_y = _uinput_coords(nx, ny)
    tool_code = e_codes.BTN_TOOL_RUBBER if tool == 2 else e_codes.BTN_TOOL_PEN
    other_tool = e_codes.BTN_TOOL_PEN if tool_code == e_codes.BTN_TOOL_RUBBER else e_codes.BTN_TOOL_RUBBER
    canceled = (flags & PEN_FLAG_CANCELED) != 0
    hover_exit = (flags & PEN_FLAG_HOVER_EXIT) != 0

    pressure = max(0, min(COORD_MAX, int(pressure)))
    tilt_x = max(-90, min(90, int(tilt_x)))
    tilt_y = max(-90, min(90, int(tilt_y)))
    distance = max(0, min(DISTANCE_MAX, int(distance)))
    primary_button = (btn_state & ANDROID_STYLUS_PRIMARY) != 0
    secondary_button = (btn_state & ANDROID_STYLUS_SECONDARY) != 0

    try:
        with _ei_lock:
            if _stylus_active_tool is not None and _stylus_active_tool != tool_code:
                ui.write(e_codes.EV_KEY, _stylus_active_tool, 0)

            ui.write(e_codes.EV_ABS, e_codes.ABS_X, abs_x)
            ui.write(e_codes.EV_ABS, e_codes.ABS_Y, abs_y)
            ui.write(e_codes.EV_ABS, e_codes.ABS_TILT_X, tilt_x)
            ui.write(e_codes.EV_ABS, e_codes.ABS_TILT_Y, tilt_y)
            ui.write(e_codes.EV_ABS, e_codes.ABS_DISTANCE, distance)

            if action == ACTION_UP or canceled or hover_exit:
                ui.write(e_codes.EV_ABS, e_codes.ABS_PRESSURE, 0)
                ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 0)
                ui.write(e_codes.EV_KEY, e_codes.BTN_STYLUS, 0)
                ui.write(e_codes.EV_KEY, e_codes.BTN_STYLUS2, 0)
                ui.write(e_codes.EV_KEY, tool_code, 0)
                ui.write(e_codes.EV_KEY, other_tool, 0)
                _stylus_active_tool = None
                if action == ACTION_UP or canceled:
                    log.info("[UINPUT PEN] UP coords=(%d, %d) tool=%d canceled=%s", abs_x, abs_y, tool, canceled)
            else:
                ui.write(e_codes.EV_KEY, tool_code, 1)
                ui.write(e_codes.EV_KEY, other_tool, 0)
                _stylus_active_tool = tool_code
                ui.write(e_codes.EV_KEY, e_codes.BTN_STYLUS, 1 if primary_button else 0)
                ui.write(e_codes.EV_KEY, e_codes.BTN_STYLUS2, 1 if secondary_button else 0)

                if action in (ACTION_DOWN, ACTION_MOVE):
                    ui.write(e_codes.EV_ABS, e_codes.ABS_PRESSURE, pressure)
                    ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 1)
                    if action == ACTION_DOWN:
                        log.info(
                            "[UINPUT PEN] DOWN coords=(%d, %d) pressure=%d tilt=(%d, %d) tool=%d",
                            abs_x, abs_y, pressure, tilt_x, tilt_y, tool
                        )
                elif action == ACTION_HOVER:
                    ui.write(e_codes.EV_ABS, e_codes.ABS_PRESSURE, 0)
                    ui.write(e_codes.EV_KEY, e_codes.BTN_TOUCH, 0)

            if frame:
                ui.syn()

    except Exception as exc:
        log.error("inject_stylus_uinput error action=%d: %s", action, exc, exc_info=True)

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

    log.info("Requesting touch and pointer permissions via XDG RemoteDesktop portal…")

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
        log.error("Portal timed out, connection failed, or permission denied.")
        
        
        
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


def _close_uinput_devices() -> None:
    global _uinput_dev, _uinput_touch_dev, _uinput_stylus_dev
    for dev in (_uinput_stylus_dev, _uinput_touch_dev, _uinput_dev):
        if dev:
            try:
                dev.close()
            except Exception:
                pass
    _uinput_dev = None
    _uinput_touch_dev = None
    _uinput_stylus_dev = None

def _close_uinput_stylus_device() -> None:
    global _uinput_stylus_dev
    if _uinput_stylus_dev:
        try:
            _uinput_stylus_dev.close()
        except Exception:
            pass
    _uinput_stylus_dev = None

def _setup_uinput(stylus_features: bool = False) -> None:
    """Create virtual uinput devices for touch and optional real stylus axes."""
    global _uinput_dev, _uinput_touch_dev, _uinput_stylus_dev, _pen_inject_fn

    if not _HAS_EVDEV:
        msg = "python-evdev not installed — uinput backend unavailable."
        log.error("%s", msg)
        log.error("Install it:  pip install evdev   (or pacman -S python-evdev)")
        _shutdown.set()
        return

    _configure_uinput_geometry()
    log.info("Creating uinput virtual touchscreen: %dx%d", _uinput_max_x, _uinput_max_y)

    direct_props = [e_codes.INPUT_PROP_DIRECT] if hasattr(e_codes, "INPUT_PROP_DIRECT") else None
    touch_cap = {
        e_codes.EV_ABS: [
            (e_codes.ABS_X,              evdev.AbsInfo(value=0, min=0, max=_uinput_max_x, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_Y,              evdev.AbsInfo(value=0, min=0, max=_uinput_max_y, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_SLOT,        evdev.AbsInfo(value=0, min=0, max=9, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_TRACKING_ID, evdev.AbsInfo(value=0, min=0, max=65535, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_POSITION_X,  evdev.AbsInfo(value=0, min=0, max=_uinput_max_x, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_MT_POSITION_Y,  evdev.AbsInfo(value=0, min=0, max=_uinput_max_y, fuzz=0, flat=0, resolution=0)),
        ],
        e_codes.EV_KEY: [e_codes.BTN_TOUCH],
    }

    stylus_cap = {
        e_codes.EV_ABS: [
            (e_codes.ABS_X,        evdev.AbsInfo(value=0, min=0, max=_uinput_max_x, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_Y,        evdev.AbsInfo(value=0, min=0, max=_uinput_max_y, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_PRESSURE, evdev.AbsInfo(value=0, min=0, max=COORD_MAX, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_DISTANCE, evdev.AbsInfo(value=0, min=0, max=DISTANCE_MAX, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_TILT_X,   evdev.AbsInfo(value=0, min=-90, max=90, fuzz=0, flat=0, resolution=0)),
            (e_codes.ABS_TILT_Y,   evdev.AbsInfo(value=0, min=-90, max=90, fuzz=0, flat=0, resolution=0)),
        ],
        e_codes.EV_KEY: [
            e_codes.BTN_TOUCH,
            e_codes.BTN_TOOL_PEN,
            e_codes.BTN_TOOL_RUBBER,
            e_codes.BTN_STYLUS,
            e_codes.BTN_STYLUS2,
        ],
    }

    try:
        touch_ui = UInput(
            touch_cap,
            name="Monitorize-Touch",
            bustype=e_codes.BUS_USB,
            input_props=direct_props,
        )
        _uinput_touch_dev = touch_ui
        _uinput_dev = touch_ui
        log.info("uinput touch device created: %s  (fd=%d)", touch_ui.device.path, touch_ui.fd)

        if stylus_features:
            stylus_ui = UInput(
                stylus_cap,
                name="Monitorize-Stylus",
                bustype=e_codes.BUS_USB,
                input_props=direct_props,
            )
            _uinput_stylus_dev = stylus_ui
            log.info("uinput stylus device created: %s  (fd=%d)", stylus_ui.device.path, stylus_ui.fd)

        if _DETECTED_DE == "hyprland":
            monitor_name = _get_hyprland_uinput_monitor_name()
            _map_hyprland_uinput_device("monitorize-touch", monitor_name)
            if stylus_features:
                _map_hyprland_uinput_device("monitorize-stylus", monitor_name)
        elif _DETECTED_DE == "sway":
            names = ["monitorize-touch"]
            if stylus_features:
                names.append("monitorize-stylus")
            if not _map_sway_uinput_devices(names):
                _close_uinput_devices()
                _shutdown.set()
                return

        log.info("Waiting 2.0 seconds for compositor to detect and configure uinput devices...")
        time.sleep(2.0)
        if _DETECTED_DE == "kde":
            touch_event = os.path.basename(getattr(touch_ui.device, "path", ""))
            stylus_event = os.path.basename(getattr(getattr(_uinput_stylus_dev, "device", None), "path", ""))
            mapped_events = _map_kde_uinput_devices_to_output([touch_ui, _uinput_stylus_dev])
            if touch_event and touch_event not in mapped_events:
                msg = "KDE could not bind Monitorize uinput devices to Virtual-TabletDisplay."
                log.error("%s Refusing to continue with stylus features enabled to avoid targeting the primary output.", msg)
                _close_uinput_devices()
                _shutdown.set()
                return
            if stylus_event and stylus_event not in mapped_events:
                log.warning(
                    "KDE did not expose/bind Monitorize-Stylus (%s). Pen packets will use Monitorize-Touch without pressure/tilt to avoid mouse emulation.",
                    stylus_event,
                )
                _close_uinput_stylus_device()
                _pen_inject_fn = _inject_touch_uinput

        log.info(
            "Touch daemon ready (uinput%s) — screen %dx%d",
            " + stylus" if stylus_features else "",
            SCREEN_W,
            SCREEN_H,
        )
        _portal_ready.set()

        while not _shutdown.is_set():
            time.sleep(0.5)

    except PermissionError:
        msg = (
            "Cannot open /dev/uinput — add a udev rule and ensure the user is in the input group."
        )
        log.error("%s", msg)
        log.error('  echo \'KERNEL=="uinput", MODE="0660", GROUP="input"\' | sudo tee /etc/udev/rules.d/99-uinput.rules')
        log.error("  sudo udevadm control --reload-rules && sudo udevadm trigger")
        log.error("  sudo usermod -aG input $USER  # then log out and back in")
        _shutdown.set()
    except Exception as exc:
        msg = f"Failed to create uinput device: {exc}"
        log.error("%s", msg, exc_info=True)
        _shutdown.set()


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return bytes()
        buf.extend(chunk)
    return bytes(buf)

def _pop_framed_packets(buffer: bytearray) -> list[tuple[int, bytes]]:
    packets = []
    valid_lengths = (PAYLOAD_SIZE, PEN_EXT_SIZE)
    while len(buffer) >= 5:
        payload_len = int.from_bytes(buffer[0:4], byteorder="big", signed=False)
        if payload_len not in valid_lengths:
            del buffer[0]
            continue
        total_len = 5 + payload_len
        if len(buffer) < total_len:
            break
        pkt_type = buffer[4]
        payload = bytes(buffer[5:total_len])
        packets.append((pkt_type, payload))
        del buffer[0:total_len]
    return packets

def _parse_udp_packets(data: bytes) -> list[tuple[int, bytes]]:
    if len(data) >= 5:
        payload_len = int.from_bytes(data[0:4], byteorder="big", signed=False)
        if payload_len in (PAYLOAD_SIZE, PEN_EXT_SIZE) and len(data) >= 5 + payload_len:
            return [(data[4], data[5:5 + payload_len])]

    if len(data) >= 1 + PAYLOAD_SIZE and data[0] in (PKT_TOUCH, PKT_PEN):
        return [(data[0], data[1:1 + PAYLOAD_SIZE])]

    return []

def _dispatch_pen_packet(
    action: int,
    tool: int,
    cid: int,
    nx: int,
    ny: int,
    pressure: int,
    tilt_x: int,
    tilt_y: int,
    distance: int,
    btn_state: int,
    flags: int,
    frame: bool = True,
) -> None:
    global _logged_dropped_pen, _last_stylus_input_time

    _last_stylus_input_time = time.monotonic()
    _release_active_finger_touches()

    if _pen_inject_fn == _inject_stylus_uinput and _uinput_stylus_dev is not None:
        _inject_stylus_uinput(action, tool, nx, ny, pressure, tilt_x, tilt_y, distance, btn_state, flags, frame=frame)
    elif _pen_inject_fn in (_inject_touch_uinput, _inject_touch_libei):
        if action != ACTION_HOVER:
            _pen_inject_fn(action, _pen_touch_cid(cid), nx, ny, frame=frame)
    elif _pen_inject_fn == _inject_pen and not _STYLUS_FEATURES:
        _inject_pen(action, tool, nx, ny, pressure, tilt_x, btn_state, frame=frame)
    elif not _logged_dropped_pen:
        log.warning("Dropping pen packets because no non-mouse stylus backend is available.")
        _logged_dropped_pen = True

def _dispatch_input_packet(pkt_type: int, payload: bytes, frame: bool = True) -> bool:
    if pkt_type == PKT_TOUCH and len(payload) == PAYLOAD_SIZE:
        action, tool, cid, nx, ny, pressure, tx, ty = struct.unpack(PAYLOAD_FMT, payload)
        _dispatch_touch_packet(action, cid, nx, ny, frame=frame)
        return True

    if pkt_type == PKT_PEN and len(payload) == PAYLOAD_SIZE:
        action, tool, cid, nx, ny, pressure, tx, btn_state = struct.unpack(PAYLOAD_FMT, payload)
        _dispatch_pen_packet(
            action, tool, cid, nx, ny, pressure,
            max(-90, min(90, tx)), 0, 0, btn_state & 0xffff, 0,
            frame=frame,
        )
        return True

    if pkt_type == PKT_PEN_EXT and len(payload) == PEN_EXT_SIZE:
        action, tool, cid, nx, ny, pressure, tilt_x, tilt_y, distance, btn_state, flags = struct.unpack(PEN_EXT_FMT, payload)
        _dispatch_pen_packet(
            action, tool, cid, nx, ny, pressure,
            tilt_x, tilt_y, distance, btn_state, flags,
            frame=frame,
        )
        return True

    log.warning("Unknown or malformed packet type=0x%02x len=%d", pkt_type, len(payload))
    return False

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

            packets_to_process = _pop_framed_packets(buffer)

            if packets_to_process:
                num_packets = len(packets_to_process)
                for idx, (pkt_type, payload) in enumerate(packets_to_process):
                    pkt_count += 1

                    if pkt_count == 1:
                        log.info("[TCP] First packet parsed successfully! type=0x%02x", pkt_type)

                    is_last = (idx == num_packets - 1)
                    _dispatch_input_packet(pkt_type, payload, frame=is_last)

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

            packets = _parse_udp_packets(data)
            for pkt_type, payload in packets:
                pkt_count += 1
                if pkt_count % 100 == 1 and _DEBUG:
                    log.debug("[UDP] pkt#%d type=%d payload_len=%d", pkt_count, pkt_type, len(payload))
                _dispatch_input_packet(pkt_type, payload)
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
    _close_uinput_devices()
    sys.exit(0)

def main():
    global _inject_fn, _pen_inject_fn
    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    stylus_features = _STYLUS_FEATURES and _DETECTED_DE in ("kde", "gnome", "hyprland", "sway")
    log.info(
        "touch_daemon.py — screen %dx%d  DE=%s  stylus_features=%s",
        SCREEN_W, SCREEN_H, _DETECTED_DE, stylus_features
    )

    if _DETECTED_DE in ("hyprland", "sway"):
        log.info(
            "Using uinput backend%s (%s portal does not support RemoteDesktop)",
            " with stylus features" if stylus_features else ""
            , _DETECTED_DE
        )
        _inject_fn = _inject_touch_uinput
        _pen_inject_fn = _inject_stylus_uinput if stylus_features else _inject_touch_uinput
        threading.Thread(target=_setup_uinput, args=(stylus_features,), daemon=True).start()
    elif _DETECTED_DE in ("kde", "gnome") and stylus_features:
        log.info("Using uinput-only backend with stylus features")
        _inject_fn = _inject_touch_uinput
        _pen_inject_fn = _inject_stylus_uinput
        threading.Thread(target=_setup_uinput, args=(True,), daemon=True).start()
    else:
        log.info("Using libei backend (XDG RemoteDesktop portal)")
        _inject_fn = _inject_touch_libei
        _pen_inject_fn = _inject_pen
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
