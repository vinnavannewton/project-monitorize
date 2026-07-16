"""evdev/uinput touch and stylus backend."""

import errno
import logging
import os
import threading
import time

from .protocol import (
    ACTION_DOWN, ACTION_HOVER, ACTION_MOVE, ACTION_UP,
    ANDROID_STYLUS_PRIMARY, ANDROID_STYLUS_SECONDARY,
    COORD_MAX, DISTANCE_MAX, PEN_FLAG_CANCELED, PEN_FLAG_HOVER_EXIT,
)
from .geometry import (
    MONITORIZE_INPUT_VENDOR_ID,
    input_product_ids,
)

log = logging.getLogger("TouchDaemon")
STYLUS_AXIS_RESOLUTION = 100
UINPUT_PERMISSION_HINT = (
    "MONITORIZE_UINPUT_PERMISSION: Monitorize needs uinput permission. "
    "Install the Monitorize udev rule (or follow your distro's input-permission "
    "setup), then log out and log back in."
)

try:
    import evdev
    from evdev import UInput, ecodes
except ImportError:
    evdev = UInput = ecodes = None


class UInputBackend:
    def __init__(self, geometry, shutdown):
        self.geometry = geometry
        self.shutdown = shutdown
        self.touch = None
        self.stylus = None
        self.active = {}
        self.slot_to_cid = {}
        self.active_tool = None
        self.lock = threading.Lock()
        self.max_x = geometry.screen_w
        self.max_y = geometry.screen_h
        self.target = (0.0, 0.0, float(self.max_x), float(self.max_y))
        self.rotation = 0

    def setup(self, stylus_features=False):
        if not evdev:
            raise RuntimeError("python-evdev not installed — uinput backend unavailable")
        if self.geometry.de == "gnome":
            self.geometry.map_gnome_devices(stylus_features)
        self.max_x, self.max_y, x, y, width, height = self.geometry.uinput_bounds()
        self.target = x, y, width, height
        self.rotation = self.geometry.rotation()
        direct = [ecodes.INPUT_PROP_DIRECT] if hasattr(ecodes, "INPUT_PROP_DIRECT") else None
        touch_product, stylus_product = input_product_ids(self.geometry.input_slot)
        touch_name = (
            "Monitorize-Touch-2" if self.geometry.input_slot == "additional"
            else "Monitorize-Touch"
        )
        stylus_name = (
            "Monitorize-Stylus-2" if self.geometry.input_slot == "additional"
            else "Monitorize-Stylus"
        )
        try:
            self.touch = UInput(
                self._touch_capabilities(),
                name=touch_name,
                vendor=MONITORIZE_INPUT_VENDOR_ID,
                product=touch_product,
                bustype=ecodes.BUS_USB,
                input_props=direct,
            )
            if stylus_features:
                self.stylus = UInput(
                    self._stylus_capabilities(),
                    name=stylus_name,
                    vendor=MONITORIZE_INPUT_VENDOR_ID,
                    product=stylus_product,
                    bustype=ecodes.BUS_USB,
                    input_props=direct,
                )
        except OSError as exc:
            if getattr(exc, "errno", None) in (
                errno.EACCES, errno.EPERM, errno.ENOENT,
            ):
                raise RuntimeError(f"{UINPUT_PERMISSION_HINT} ({exc})") from exc
            raise
        self._map_devices(stylus_features)
        time.sleep(2)
        if self.geometry.de == "kde":
            mapped = self.geometry.map_kde_devices([self.touch, self.stylus])
            touch_event = self._event_name(self.touch, touch_name)
            if touch_event not in mapped:
                raise RuntimeError(
                    f"KDE could not bind {touch_name} to "
                    f"{os.environ.get('MONITORIZE_OUTPUT', 'Virtual-Monitorize-1')}"
                )
            if self.stylus and self._event_name(self.stylus, stylus_name) not in mapped:
                self.stylus.close()
                self.stylus = None
        elif self.geometry.de == "gnome":
            verify = getattr(self.geometry, "verify_gnome_devices", None)
            if callable(verify) and getattr(self.geometry, "_gnome_devices_mapped", True):
                try:
                    mapped = set(verify([self.touch, self.stylus]) or ())
                except Exception as exc:
                    log.warning("Failed to verify GNOME uinput mapping: %s", exc)
                    mapped = set()
                if self.stylus:
                    stylus_event = os.path.basename(self.stylus.device.path)
                    if stylus_event not in mapped:
                        log.warning(
                            "GNOME did not confirm %s output mapping; "
                            "stylus pressure may fall back to touch emulation",
                            stylus_name,
                        )

    def _event_name(self, device, label):
        event_device = getattr(device, "device", None)
        path = getattr(event_device, "path", "")
        if not path:
            raise RuntimeError(
                f"{UINPUT_PERMISSION_HINT} KDE could not read the {label} event node."
            )
        return os.path.basename(path)

    def _abs(self, code, minimum, maximum, resolution=0):
        return code, evdev.AbsInfo(0, minimum, maximum, 0, 0, resolution)

    def _touch_capabilities(self):
        return {
            ecodes.EV_ABS: [
                self._abs(ecodes.ABS_X, 0, self.max_x),
                self._abs(ecodes.ABS_Y, 0, self.max_y),
                self._abs(ecodes.ABS_MT_SLOT, 0, 9),
                self._abs(ecodes.ABS_MT_TRACKING_ID, 0, 65535),
                self._abs(ecodes.ABS_MT_POSITION_X, 0, self.max_x),
                self._abs(ecodes.ABS_MT_POSITION_Y, 0, self.max_y),
            ],
            ecodes.EV_KEY: [ecodes.BTN_TOUCH],
        }

    def _stylus_capabilities(self):
        abs_axes = [
            self._abs(ecodes.ABS_X, 0, self.max_x, STYLUS_AXIS_RESOLUTION),
            self._abs(ecodes.ABS_Y, 0, self.max_y, STYLUS_AXIS_RESOLUTION),
            self._abs(ecodes.ABS_PRESSURE, 0, COORD_MAX),
            self._abs(ecodes.ABS_DISTANCE, 0, DISTANCE_MAX),
            self._abs(ecodes.ABS_TILT_X, -90, 90),
            self._abs(ecodes.ABS_TILT_Y, -90, 90),
        ]
        if hasattr(ecodes, "ABS_MISC"):
            abs_axes.append(self._abs(ecodes.ABS_MISC, 0, COORD_MAX))
        capabilities = {
            ecodes.EV_ABS: abs_axes,
            ecodes.EV_KEY: [
                ecodes.BTN_TOUCH, ecodes.BTN_TOOL_PEN, ecodes.BTN_TOOL_RUBBER,
                ecodes.BTN_STYLUS, ecodes.BTN_STYLUS2,
            ],
        }
        if hasattr(ecodes, "EV_MSC") and hasattr(ecodes, "MSC_SERIAL"):
            capabilities[ecodes.EV_MSC] = [ecodes.MSC_SERIAL]
        return capabilities

    def _map_devices(self, stylus_features):
        touch_name = (
            "monitorize-touch-2" if self.geometry.input_slot == "additional"
            else "monitorize-touch"
        )
        stylus_name = (
            "monitorize-stylus-2" if self.geometry.input_slot == "additional"
            else "monitorize-stylus"
        )
        names = [touch_name] + ([stylus_name] if stylus_features else [])
        if self.geometry.de == "hyprland":
            output = self.geometry.hyprland_output_name()
            for name in names:
                self.geometry.map_hyprland_device(name, output)

    def _coords(self, x, y):
        tx, ty, width, height = self.target
        nx, ny = x / COORD_MAX, y / COORD_MAX
        if self.rotation == 90:
            nx, ny = 1 - ny, nx
        elif self.rotation == 180:
            nx, ny = 1 - nx, 1 - ny
        elif self.rotation == 270:
            nx, ny = ny, 1 - nx
        return (
            max(0, min(self.max_x, round(tx + nx * width))),
            max(0, min(self.max_y, round(ty + ny * height))),
        )

    def _allocate_slot(self, cid):
        if cid in self.active:
            return self.active[cid]
        for slot in range(10):
            if slot not in self.slot_to_cid:
                self.active[cid] = slot
                self.slot_to_cid[slot] = cid
                return slot
        log.warning("Ignoring touch contact %s: no free multitouch slots", cid)
        return None

    def _release_slot(self, cid):
        slot = self.active.pop(cid, None)
        if slot is not None:
            self.slot_to_cid.pop(slot, None)
        return slot

    def inject_touch(self, action, cid, x, y, frame=True):
        if not self.touch:
            return
        px, py = self._coords(x, y)
        with self.lock:
            if action == ACTION_DOWN:
                slot = self._allocate_slot(cid)
                if slot is None:
                    return
                self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_SLOT, slot)
                self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_TRACKING_ID, cid & 0xffff)
                self._write_touch_position(slot, px, py)
                self.touch.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 1)
            elif action == ACTION_MOVE and cid in self.active:
                self._write_touch_position(self.active[cid], px, py)
            elif action == ACTION_UP:
                active_slot = self._release_slot(cid)
                if active_slot is not None:
                    self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_SLOT, active_slot)
                    self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_TRACKING_ID, -1)
                    if not self.active:
                        self.touch.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0)
            if frame:
                self.touch.syn()

    def _write_touch_position(self, slot, x, y):
        self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_SLOT, slot)
        self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_POSITION_X, x)
        self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_POSITION_Y, y)
        if slot == 0:
            self.touch.write(ecodes.EV_ABS, ecodes.ABS_X, x)
            self.touch.write(ecodes.EV_ABS, ecodes.ABS_Y, y)

    def inject_pointer(self, *_args):
        return False

    def inject_pen(
        self, action, tool, x, y, pressure, tilt_x, tilt_y,
        distance, buttons, flags, frame=True,
    ):
        if not self.stylus:
            return False
        px, py = self._coords(x, y)
        tool_code = ecodes.BTN_TOOL_RUBBER if tool == 2 else ecodes.BTN_TOOL_PEN
        other_tool = ecodes.BTN_TOOL_PEN if tool_code == ecodes.BTN_TOOL_RUBBER else ecodes.BTN_TOOL_RUBBER
        ending = action == ACTION_UP or flags & (PEN_FLAG_CANCELED | PEN_FLAG_HOVER_EXIT)
        tool_serial = 2 if tool_code == ecodes.BTN_TOOL_RUBBER else 1
        with self.lock:
            if self.active_tool and self.active_tool != tool_code:
                self.stylus.write(ecodes.EV_KEY, self.active_tool, 0)
            if hasattr(ecodes, "EV_MSC") and hasattr(ecodes, "MSC_SERIAL"):
                self.stylus.write(ecodes.EV_MSC, ecodes.MSC_SERIAL, tool_serial)
            for code, value in (
                (ecodes.ABS_X, px), (ecodes.ABS_Y, py),
                (ecodes.ABS_TILT_X, max(-90, min(90, tilt_x))),
                (ecodes.ABS_TILT_Y, max(-90, min(90, tilt_y))),
                (ecodes.ABS_DISTANCE, max(0, min(DISTANCE_MAX, distance))),
            ):
                self.stylus.write(ecodes.EV_ABS, code, value)
            if hasattr(ecodes, "ABS_MISC"):
                self.stylus.write(ecodes.EV_ABS, ecodes.ABS_MISC, tool_serial)
            if ending:
                self.stylus.write(ecodes.EV_ABS, ecodes.ABS_PRESSURE, 0)
                for code in (
                    ecodes.BTN_TOUCH, ecodes.BTN_STYLUS, ecodes.BTN_STYLUS2,
                    tool_code, other_tool,
                ):
                    self.stylus.write(ecodes.EV_KEY, code, 0)
                self.active_tool = None
            else:
                self.stylus.write(ecodes.EV_KEY, tool_code, 1)
                self.stylus.write(ecodes.EV_KEY, other_tool, 0)
                self.stylus.write(ecodes.EV_KEY, ecodes.BTN_STYLUS, int(bool(buttons & ANDROID_STYLUS_PRIMARY)))
                self.stylus.write(ecodes.EV_KEY, ecodes.BTN_STYLUS2, int(bool(buttons & ANDROID_STYLUS_SECONDARY)))
                self.active_tool = tool_code
                touching = action in (ACTION_DOWN, ACTION_MOVE)
                self.stylus.write(ecodes.EV_ABS, ecodes.ABS_PRESSURE, max(0, min(COORD_MAX, pressure)) if touching else 0)
                self.stylus.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, int(touching))
            if frame:
                self.stylus.syn()
        return True

    def release_all(self):
        with self.lock:
            if self.touch:
                for cid in list(self.active):
                    active_slot = self._release_slot(cid)
                    if active_slot is not None:
                        self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_SLOT, active_slot)
                        self.touch.write(ecodes.EV_ABS, ecodes.ABS_MT_TRACKING_ID, -1)
                self.touch.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0)
                self.touch.syn()
            if self.stylus:
                for code in (
                    ecodes.BTN_TOUCH, ecodes.BTN_STYLUS, ecodes.BTN_STYLUS2,
                    ecodes.BTN_TOOL_PEN, ecodes.BTN_TOOL_RUBBER,
                ):
                    self.stylus.write(ecodes.EV_KEY, code, 0)
                self.stylus.write(ecodes.EV_ABS, ecodes.ABS_PRESSURE, 0)
                self.stylus.syn()
                self.active_tool = None

    def close(self):
        self.release_all()
        for device in (self.stylus, self.touch):
            if device:
                try:
                    device.close()
                except Exception:
                    pass
        self.stylus = self.touch = None
        self.active.clear()
        self.slot_to_cid.clear()
