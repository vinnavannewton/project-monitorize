"""GNOME Mutter virtual monitor layout helpers."""

import logging
import time

from monitorize.config.settings import (
    load_gnome_virtual_layout,
    save_gnome_virtual_layout,
)
from monitorize.input_bridge.geometry import _physical_contains_virtual_marker


log = logging.getLogger(__name__)
APPLY_METHOD_TEMPORARY = 1
WAIT_ATTEMPTS = 20
WAIT_DELAY = 0.1
MONITOR_CONFIG_PROPERTY_KEYS = {
    "color-mode",
    "rgb-range",
    "underscanning",
}
GLOBAL_CONFIG_PROPERTY_KEYS = {
    "layout-mode",
}


def _dbus():
    import dbus

    return dbus


def display_config_interface(bus=None, dbus=None):
    dbus = dbus or _dbus()
    bus = bus or dbus.SessionBus()
    obj = bus.get_object(
        "org.gnome.Mutter.DisplayConfig",
        "/org/gnome/Mutter/DisplayConfig",
    )
    return dbus.Interface(obj, "org.gnome.Mutter.DisplayConfig")


def _mutter_state(display_config=None):
    display_config = display_config or display_config_interface()
    return display_config.GetCurrentState()


def _connector_name(entry):
    try:
        spec = entry[0]
    except (TypeError, IndexError):
        return ""
    if isinstance(spec, str):
        return spec
    try:
        return str(spec[0])
    except (TypeError, IndexError):
        return ""


def _logical_connector_names(logical_monitor):
    try:
        connectors = logical_monitor[5]
    except (TypeError, IndexError):
        return []
    names = []
    for item in connectors:
        try:
            connector = str(item[0])
        except (TypeError, IndexError):
            continue
        if connector:
            names.append(connector)
    return names


def _connector_key(connectors):
    return tuple(sorted(str(connector) for connector in connectors if connector))


def _virtual_connectors(physical_monitors):
    connectors = [
        _connector_name(monitor)
        for monitor in physical_monitors
        if _physical_contains_virtual_marker(monitor)
    ]
    return [connector for connector in connectors if connector]


def virtual_connectors_from_state(state):
    try:
        return _virtual_connectors(state[1])
    except (TypeError, IndexError):
        return []


def monitor_info_from_state(state, connector):
    """Return the current mode and EDID identity for one exact connector."""
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return None
    for monitor in physical_monitors:
        if _connector_name(monitor) != str(connector):
            continue
        try:
            current = next(mode for mode in monitor[1] if mode[6].get("is-current"))
            spec = monitor[0]
            return {
                "connector": str(spec[0]),
                "vendor": str(spec[1]), "product": str(spec[2]),
                "serial": str(spec[3]),
                "width": int(current[1]), "height": int(current[2]),
                "refresh_rate": float(current[3]),
            }
        except (TypeError, ValueError, IndexError, StopIteration, AttributeError):
            return None
    return None


def new_virtual_connector(state, before, width=None, height=None):
    """Find one new Mutter virtual connector, never guess between two."""
    candidates = [
        connector for connector in virtual_connectors_from_state(state)
        if connector not in set(before or ())
    ]
    if len(candidates) != 1:
        return ""
    info = monitor_info_from_state(state, candidates[0])
    if not info:
        return ""
    if width and info["width"] != int(width):
        return ""
    if height and info["height"] != int(height):
        return ""
    return candidates[0]


def virtual_connector_from_state(state):
    _serial, physical_monitors, _logical_monitors, _properties = state
    return next(iter(_virtual_connectors(physical_monitors)), "")


def logical_layout_snapshot(state=None, display_config=None, role_connectors=None):
    """Return a serializable GNOME logical monitor layout snapshot.

    GNOME validates the entire layout on ApplyMonitorsConfig: coordinates must
    be non-negative, adjacent, and normalized to min x/y == 0. A virtual monitor
    on the left therefore requires moving the physical monitor right too, not
    only restoring the virtual monitor's x/y.
    """
    state = state or _mutter_state(display_config)
    _serial, physical_monitors, logical_monitors, _properties = state
    virtual_connectors = set(_virtual_connectors(physical_monitors))
    connector_roles = {
        str(connector): str(role)
        for role, connector in (role_connectors or {}).items() if connector
    }
    snapshot = []
    found_virtual = False
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        if not connectors:
            continue
        virtual = [connector for connector in connectors if connector in virtual_connectors]
        is_virtual = bool(virtual)
        if role_connectors and is_virtual:
            if len(virtual) != 1 or virtual[0] not in connector_roles:
                return None
        found_virtual = found_virtual or is_virtual
        try:
            x = int(float(logical_monitor[0]))
            y = int(float(logical_monitor[1]))
            scale = float(logical_monitor[2])
        except (TypeError, ValueError, IndexError):
            continue
        entry = {
            "connectors": connectors,
            "x": x,
            "y": y,
            "scale": scale,
            "virtual": is_virtual,
        }
        if role_connectors:
            entry["transform"] = int(logical_monitor[3])
            entry["primary"] = bool(logical_monitor[4])
            if is_virtual and virtual[0] in connector_roles:
                entry["role"] = connector_roles[virtual[0]]
        snapshot.append(entry)
    return snapshot if found_virtual else None


def virtual_scale_from_layout(logical_monitors, slot="primary"):
    if not isinstance(logical_monitors, list):
        return None
    for entry in logical_monitors:
        if not isinstance(entry, dict) or not entry.get("virtual"):
            continue
        if entry.get("role", "primary") != slot:
            continue
        try:
            scale = float(entry["scale"])
        except (KeyError, TypeError, ValueError):
            return None
        return scale if scale > 0 else None
    return None


def load_saved_virtual_scale(slot="primary"):
    return virtual_scale_from_layout(
        load_gnome_virtual_layout(slot).get("logical_monitors"), slot
    )


def _scale_supported(scale, supported_scales):
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return False
    return any(abs(scale - float(supported)) < 0.0001 for supported in supported_scales)


def _target_positions_from_saved_layout(state, saved_layout, role_connectors=None):
    if not isinstance(saved_layout, list):
        return None
    _serial, physical_monitors, logical_monitors, _properties = state
    current_virtual_connectors = set(_virtual_connectors(physical_monitors))
    if not current_virtual_connectors:
        return None

    saved_physical = {}
    saved_virtual = {}
    for entry in saved_layout:
        if not isinstance(entry, dict):
            continue
        connectors = entry.get("connectors")
        if not isinstance(connectors, (list, tuple)):
            continue
        try:
            x = int(float(entry["x"]))
            y = int(float(entry["y"]))
            scale = float(entry["scale"])
        except (KeyError, TypeError, ValueError):
            continue
        if scale <= 0:
            continue
        normalized = {
            "x": x, "y": y, "scale": scale,
            "transform": int(entry.get("transform", 0)),
            "primary": bool(entry.get("primary", False)),
        }
        if entry.get("virtual"):
            saved_virtual[entry.get("role", "primary")] = normalized
        else:
            key = _connector_key(connectors)
            if key:
                saved_physical[key] = normalized

    if not saved_virtual:
        return None

    connector_roles = {
        str(connector): str(role)
        for role, connector in (role_connectors or {}).items() if connector
    }
    if not connector_roles:
        current = list(current_virtual_connectors)
        if len(current) == 1:
            connector_roles[current[0]] = "primary"

    targets = []
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        if not connectors:
            return None
        virtual = [connector for connector in connectors if connector in current_virtual_connectors]
        if virtual:
            if len(virtual) != 1:
                return None
            target = saved_virtual.get(connector_roles.get(virtual[0]))
        else:
            target = saved_physical.get(_connector_key(connectors))
        if target is None:
            return None
        targets.append(target)

    if not targets:
        return None
    min_x = min(item["x"] for item in targets)
    min_y = min(item["y"] for item in targets)
    return [dict(item, x=item["x"] - min_x, y=item["y"] - min_y) for item in targets]


def _variant_dict(dbus, values=None):
    values = values or {}
    if hasattr(dbus, "Dictionary"):
        return dbus.Dictionary(values, signature="sv")
    return dict(values)


def _typed(dbus, name, value):
    constructor = getattr(dbus, name, None)
    return constructor(value) if constructor else value


def _allowed_properties(source, allowed_keys):
    if not hasattr(source, "items"):
        return {}
    return {
        str(key): value
        for key, value in source.items()
        if str(key) in allowed_keys
    }


def _monitor_config_properties(source):
    properties = _allowed_properties(source, MONITOR_CONFIG_PROPERTY_KEYS)
    if "underscanning" not in properties and hasattr(source, "get"):
        try:
            if "is-underscanning" in source:
                properties["underscanning"] = source.get("is-underscanning")
        except (TypeError, ValueError):
            pass
    return properties


def _monitor_properties_by_connector(physical_monitors):
    properties = {}
    for monitor in physical_monitors:
        connector = _connector_name(monitor)
        if not connector:
            continue
        try:
            properties[connector] = _monitor_config_properties(monitor[2])
        except (TypeError, IndexError):
            properties[connector] = {}
    return properties


def _current_modes(physical_monitors):
    current_modes = {}
    for monitor in physical_monitors:
        connector = _connector_name(monitor)
        if not connector:
            continue
        try:
            modes = monitor[1]
        except (TypeError, IndexError):
            return None
        current = next(
            (
                mode for mode in modes
                if len(mode) > 6
                and getattr(mode[6], "get", lambda _key: False)("is-current")
            ),
            None,
        )
        if current is None:
            return None
        try:
            supported_scales = [float(scale) for scale in current[5]]
        except (TypeError, ValueError, IndexError):
            supported_scales = []
        current_modes[connector] = {
            "id": str(current[0]),
            "supported_scales": supported_scales,
        }
    return current_modes


def _monitor_config(dbus, connector, mode_id, properties=None):
    values = [
        _typed(dbus, "String", connector),
        _typed(dbus, "String", mode_id),
        _variant_dict(dbus, properties),
    ]
    if hasattr(dbus, "Struct"):
        return dbus.Struct(values, signature="ssa{sv}")
    return tuple(values)


def _logical_monitor_config(
    dbus,
    logical_monitor,
    current_modes,
    monitor_properties,
    target,
):
    try:
        connectors = logical_monitor[5]
    except (TypeError, IndexError):
        return None

    monitor_configs = []
    for item in connectors:
        try:
            connector = str(item[0])
        except (TypeError, IndexError):
            return None
        mode = current_modes.get(connector)
        if not mode or not _scale_supported(target["scale"], mode["supported_scales"]):
            return None
        monitor_configs.append(
            _monitor_config(
                dbus,
                connector,
                mode["id"],
                monitor_properties.get(connector, {}),
            )
        )

    values = [
        _typed(dbus, "Int32", int(float(target["x"]))),
        _typed(dbus, "Int32", int(float(target["y"]))),
        _typed(dbus, "Double", float(target["scale"])),
        _typed(dbus, "UInt32", int(target["transform"])),
        _typed(dbus, "Boolean", bool(target["primary"])),
        dbus.Array(monitor_configs, signature="(ssa{sv})")
        if hasattr(dbus, "Array") else monitor_configs,
    ]
    if hasattr(dbus, "Struct"):
        return dbus.Struct(values, signature="iiduba(ssa{sv})")
    return tuple(values)


def build_monitors_config(state, dbus=None, logical_monitors=None, role_connectors=None):
    """Build an ApplyMonitorsConfig logical monitor payload.

    Returns None when the current state lacks enough mode/config detail to
    preserve every logical monitor unchanged.
    """
    dbus = dbus or _dbus()
    _serial, physical_monitors, current_logical_monitors, _properties = state
    if not virtual_connector_from_state(state):
        return None
    current_modes = _current_modes(physical_monitors)
    if not current_modes:
        return None
    monitor_properties = _monitor_properties_by_connector(physical_monitors)
    target_positions = _target_positions_from_saved_layout(
        state, logical_monitors, role_connectors
    )
    if target_positions is None:
        return None
    configs = []
    for index, logical_monitor in enumerate(current_logical_monitors):
        config = _logical_monitor_config(
            dbus,
            logical_monitor,
            current_modes,
            monitor_properties,
            target_positions[index],
        )
        if config is None:
            return None
        configs.append(config)
    if hasattr(dbus, "Array"):
        return dbus.Array(configs, signature="(iiduba(ssa{sv}))")
    return configs


def wait_for_virtual_state(display_config=None, attempts=WAIT_ATTEMPTS, delay=WAIT_DELAY):
    display_config = display_config or display_config_interface()
    for _attempt in range(attempts):
        state = _mutter_state(display_config)
        if virtual_connector_from_state(state):
            return state
        time.sleep(delay)
    return None


def restore_virtual_layout(
    slot="primary",
    logical_monitors=None,
    display_config=None,
    dbus=None,
    attempts=WAIT_ATTEMPTS,
    delay=WAIT_DELAY,
    role_connectors=None,
):
    if logical_monitors is None:
        logical_monitors = load_gnome_virtual_layout(slot).get("logical_monitors")
    if not logical_monitors:
        return False
    try:
        dbus = dbus or _dbus()
        display_config = display_config or display_config_interface(dbus=dbus)
        state = wait_for_virtual_state(display_config, attempts, delay)
        if not state:
            return False
        serial = state[0]
        configs = build_monitors_config(
            state, dbus, logical_monitors=logical_monitors,
            role_connectors=role_connectors,
        )
        if configs is None:
            return False
        display_config.ApplyMonitorsConfig(
            _typed(dbus, "UInt32", int(serial)),
            _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
            configs,
            _variant_dict(
                dbus,
                _allowed_properties(state[3], GLOBAL_CONFIG_PROPERTY_KEYS),
            ),
        )
        return True
    except Exception as exc:
        log.debug("Failed to restore GNOME virtual monitor layout: %s", exc)
        return False


def save_current_virtual_layout(slot="primary", role_connectors=None):
    try:
        logical_monitors = logical_layout_snapshot(role_connectors=role_connectors)
    except Exception as exc:
        log.debug("Failed to query GNOME virtual monitor layout: %s", exc)
        return False
    if not logical_monitors:
        return False
    save_gnome_virtual_layout(slot, logical_monitors)
    return True
