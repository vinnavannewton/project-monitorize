"""Compositor geometry discovery and uinput output mapping."""

import json
import logging
import os
import subprocess
import time

log = logging.getLogger("TouchDaemon")

MONITORIZE_INPUT_VENDOR_ID = 0x4D5A
MONITORIZE_TOUCH_PRODUCT_ID = 0x1001
MONITORIZE_STYLUS_PRODUCT_ID = 0x1002
GNOME_INPUT_MAPPING_TIMEOUT = 5.0
GNOME_INPUT_MAPPING_INTERVAL = 0.1
GNOME_TOUCHSCREEN_SCHEMA = "org.gnome.desktop.peripherals.touchscreen"
GNOME_TABLET_SCHEMA = "org.gnome.desktop.peripherals.tablet"


def _gnome_device_path(group: str, vendor: int, product: int) -> str:
    return f"/org/gnome/desktop/peripherals/{group}/{vendor:04x}:{product:04x}/"


def _gio_settings(schema: str, path: str):
    from gi.repository import Gio

    return Gio.Settings.new_with_path(schema, path)


def _physical_contains_virtual_marker(entry):
    try:
        values = entry[0]
    except (TypeError, IndexError):
        return False
    if isinstance(values, str):
        values = [values]
    try:
        values = list(values)
    except TypeError:
        values = [values]
    return any(
        marker in str(value).lower()
        for value in values
        for marker in ("meta", "virtual")
    )


def gnome_virtual_monitor_edid_from_state(state):
    try:
        _serial, physical, _logical, _props = state
    except (TypeError, ValueError):
        return None
    for monitor in physical:
        if not _physical_contains_virtual_marker(monitor):
            continue
        try:
            _connector, vendor, product, serial = monitor[0]
        except (TypeError, ValueError, IndexError):
            continue
        edid = tuple(str(value) for value in (vendor, product, serial))
        if all(edid):
            return edid
    return None


def write_gnome_input_mapping(edid, stylus_features=False):
    try:
        values = [str(value) for value in edid]
    except TypeError:
        return False
    if len(values) != 3 or not all(values):
        return False
    try:
        touch = _gio_settings(
            GNOME_TOUCHSCREEN_SCHEMA,
            _gnome_device_path(
                "touchscreens",
                MONITORIZE_INPUT_VENDOR_ID,
                MONITORIZE_TOUCH_PRODUCT_ID,
            ),
        )
        touch.set_strv("output", values)
        if stylus_features:
            tablet = _gio_settings(
                GNOME_TABLET_SCHEMA,
                _gnome_device_path(
                    "tablets",
                    MONITORIZE_INPUT_VENDOR_ID,
                    MONITORIZE_STYLUS_PRODUCT_ID,
                ),
            )
            tablet.set_strv("output", values)
            tablet.set_string("mapping", "absolute")
    except Exception as exc:
        log.warning("Failed to write GNOME input mapping: %s", exc)
        return False
    return True


def detect_de() -> str:
    combined = (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        + " "
        + os.environ.get("DESKTOP_SESSION", "")
    ).lower()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in combined:
        return "hyprland"
    if os.environ.get("SWAYSOCK") or "sway" in combined:
        return "sway"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    return "unknown"


def json_command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else []


def headless_output(outputs):
    return next((
        output for output in outputs
        if output.get("name", "").startswith("HEADLESS")
        or output.get("name", "").lower().startswith("virtual-tabletdisplay")
    ), None)


def kde_virtual_output(outputs):
    exact = next(
        (item for item in outputs if item.get("name") == "Virtual-TabletDisplay"),
        None,
    )
    if exact:
        return exact
    return next(
        (
            item for item in outputs
            if item.get("enabled")
            and item.get("connected", True)
            and str(item.get("name", "")).lower().startswith("virtual")
        ),
        None,
    )


class Geometry:
    def __init__(self, de: str, screen_w: int, screen_h: int):
        self.de = de
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._cache = None

    def invalidate(self):
        self._cache = None

    def virtual_rect(self):
        if self._cache is None:
            finder = getattr(self, f"_rect_{self.de}", None)
            self._cache = finder() if finder else self._fallback_rect()
            if self._cache is None:
                self._cache = (0.0, 0.0, float(self.screen_w), float(self.screen_h))
        return self._cache

    def rotation(self):
        if self.de == "kde":
            outputs = json_command(["kscreen-doctor", "-j"]).get("outputs", [])
            output = kde_virtual_output(outputs)
            value = output.get("rotation", 1) if output else 1
            key = str(value).strip().lower()
            rotations = {
                "1": 0, "2": 270, "4": 180, "8": 90,
                "none": 0, "left": 270, "inverted": 180, "right": 90,
            }
            if key not in rotations:
                log.warning("Unknown KDE output rotation %r; using 0 degrees", value)
            return rotations.get(key, 0)
        return 0

    def _fallback_rect(self):
        return (
            self._rect_hyprland()
            or self._rect_sway()
            or self._rect_kde()
            or self._rect_gnome()
        )

    def _rect_kde(self):
        try:
            outputs = json_command(["kscreen-doctor", "-j"]).get("outputs", [])
            target = kde_virtual_output(outputs)
            if (
                target is None
                and os.environ.get("MONITORIZE_PORTAL_SOURCE_TYPE") != "4"
            ):
                target = next(
                    (item for item in outputs if item.get("primary") or item.get("enabled")),
                    None,
                )
            if target:
                pos = target.get("pos", {})
                size = target.get("size", {})
                scale = target.get("scale", 1.0)
                return (
                    float(pos.get("x", 0)),
                    float(pos.get("y", 0)),
                    float(size.get("width", self.screen_w)) / scale,
                    float(size.get("height", self.screen_h)) / scale,
                )
        except Exception as exc:
            log.warning("Failed to query kscreen-doctor: %s", exc)
        return None

    def _rect_hyprland(self):
        try:
            monitor = headless_output(json_command(["hyprctl", "monitors", "-j"]))
            if monitor:
                scale = float(monitor.get("scale", 1.0))
                return (
                    float(monitor.get("x", 0)),
                    float(monitor.get("y", 0)),
                    float(monitor.get("width", self.screen_w)) / scale,
                    float(monitor.get("height", self.screen_h)) / scale,
                )
        except Exception as exc:
            log.warning("Failed to query hyprctl monitors: %s", exc)
        return None

    def _rect_sway(self):
        try:
            output_name = os.environ.get("MONITORIZE_OUTPUT", "")
            outputs = json_command(["swaymsg", "-t", "get_outputs", "-r"])
            target = next((
                item for item in outputs
                if item.get("name") == output_name
                or (not output_name and item.get("name", "").startswith("HEADLESS"))
            ), None)
            if target:
                rect = target.get("rect", {})
                return tuple(float(rect.get(key, default)) for key, default in (
                    ("x", 0), ("y", 0),
                    ("width", self.screen_w), ("height", self.screen_h),
                ))
        except Exception as exc:
            log.warning("Failed to query Sway outputs: %s", exc)
        return None

    def _mutter_state(self):
        import dbus
        bus = dbus.SessionBus()
        obj = bus.get_object(
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
        )
        return dbus.Interface(
            obj, "org.gnome.Mutter.DisplayConfig"
        ).GetCurrentState()

    def _rect_gnome(self):
        try:
            _serial, physical, logical, _props = self._mutter_state()
            for monitor in physical:
                connector = str(monitor[0][0])
                if not any(name in connector.lower() for name in ("virtual", "meta")):
                    continue
                mode = next((mode for mode in monitor[1] if mode[6].get("is-current")), monitor[1][0])
                for layout in logical:
                    if any(str(item[0]) == connector for item in layout[5]):
                        scale = float(layout[2])
                        return (
                            float(layout[0]), float(layout[1]),
                            float(mode[1]) / scale, float(mode[2]) / scale,
                        )
        except Exception as exc:
            log.warning("Failed to query GNOME virtual monitor: %s", exc)
        return None

    def map_gnome_devices(
        self,
        stylus_features=False,
        timeout=GNOME_INPUT_MAPPING_TIMEOUT,
        interval=GNOME_INPUT_MAPPING_INTERVAL,
    ) -> bool:
        if self.de != "gnome":
            return False
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                edid = gnome_virtual_monitor_edid_from_state(self._mutter_state())
                if edid:
                    return write_gnome_input_mapping(edid, stylus_features)
            except Exception as exc:
                last_error = exc
            time.sleep(interval)
        if last_error:
            log.warning("Failed to map GNOME uinput devices: %s", last_error)
        else:
            log.warning("Failed to map GNOME uinput devices: no virtual monitor EDID")
        return False

    def desktop_bounds(self):
        if self.de != "gnome":
            return None
        try:
            _serial, physical, logical, _props = self._mutter_state()
            sizes = {}
            for monitor in physical:
                modes = monitor[1]
                mode = next((item for item in modes if item[6].get("is-current")), modes[0])
                sizes[str(monitor[0][0])] = (float(mode[1]), float(mode[2]))
            rects = []
            for layout in logical:
                scale = float(layout[2]) or 1.0
                width = max((sizes.get(str(item[0]), (0, 0))[0] / scale for item in layout[5]), default=0)
                height = max((sizes.get(str(item[0]), (0, 0))[1] / scale for item in layout[5]), default=0)
                if width and height:
                    rects.append((float(layout[0]), float(layout[1]), width, height))
            if rects:
                min_x = min(item[0] for item in rects)
                min_y = min(item[1] for item in rects)
                max_x = max(item[0] + item[2] for item in rects)
                max_y = max(item[1] + item[3] for item in rects)
                return min_x, min_y, max_x - min_x, max_y - min_y
        except Exception as exc:
            log.warning("Failed to query GNOME desktop bounds: %s", exc)
        return None

    def uinput_bounds(self):
        rx, ry, rw, rh = self.virtual_rect()
        bounds = self.desktop_bounds()
        if bounds:
            bx, by, bw, bh = bounds
            return int(round(bw)), int(round(bh)), rx - bx, ry - by, rw, rh
        return int(round(rw)), int(round(rh)), 0.0, 0.0, rw, rh

    def hyprland_output_name(self):
        monitor = headless_output(json_command(["hyprctl", "monitors", "-j"]))
        return monitor.get("name") if monitor else None

    def map_hyprland_device(self, device_name: str, monitor_name=None):
        output = monitor_name or "HEADLESS-1"
        result = subprocess.run(
            ["hyprctl", "keyword", f"device:{device_name.lower()}:output", output],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def map_sway_devices(self, device_names: list[str]) -> bool:
        output = os.environ.get("MONITORIZE_OUTPUT", "")
        if not output:
            return False
        normalize = lambda value: value.lower().replace("-", " ").replace("_", " ")
        pending = {normalize(name) for name in device_names}
        deadline = time.monotonic() + 5
        while pending and time.monotonic() < deadline:
            for item in json_command(["swaymsg", "-t", "get_inputs", "-r"]):
                match = next((name for name in pending if name == normalize(item.get("name", ""))), None)
                if not match:
                    continue
                result = subprocess.run(
                    ["swaymsg", "input", item.get("identifier", ""), "map_to_output", output],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    pending.remove(match)
            if pending:
                time.sleep(0.2)
        return not pending

    def map_kde_devices(self, devices: list) -> set[str]:
        event_names = {
            os.path.basename(getattr(getattr(dev, "device", None), "path", ""))
            for dev in devices if dev
        } - {""}
        if not event_names:
            return set()
        target_output = (
            os.environ.get("MONITORIZE_OUTPUT")
            or (kde_virtual_output(json_command(["kscreen-doctor", "-j"]).get("outputs", [])) or {}).get("name")
            or "Virtual-TabletDisplay"
        )
        try:
            import dbus
            bus = dbus.SessionBus()
            manager = bus.get_object("org.kde.KWin", "/org/kde/KWin/InputDevice")
            manager_props = dbus.Interface(manager, "org.freedesktop.DBus.Properties")
            pending, mapped = set(event_names), set()
            deadline = time.monotonic() + 5
            while pending and time.monotonic() < deadline:
                known = {
                    str(name) for name in manager_props.Get(
                        "org.kde.KWin.InputDeviceManager", "devicesSysNames"
                    )
                }
                for event_name in list(pending):
                    if known and event_name not in known:
                        continue
                    obj = bus.get_object("org.kde.KWin", f"/org/kde/KWin/InputDevice/{event_name}")
                    props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                    props.Set(
                        "org.kde.KWin.InputDevice",
                        "outputName",
                        dbus.String(target_output),
                    )
                    pending.remove(event_name)
                    mapped.add(event_name)
                if pending:
                    time.sleep(0.1)
            try:
                dbus.Interface(
                    bus.get_object("org.kde.KWin", "/KWin"), "org.kde.KWin"
                ).reconfigure()
            except Exception:
                pass
            return mapped
        except Exception as exc:
            log.warning("Failed to map KDE uinput devices: %s", exc)
            return set()
