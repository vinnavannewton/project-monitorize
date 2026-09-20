"""GNOME Mutter virtual monitor layout helpers."""

import logging
import math
import time

from monitorize.config.settings import (
    load_gnome_virtual_layout,
    save_gnome_virtual_layout,
)
log = logging.getLogger(__name__)
APPLY_METHOD_TEMPORARY = 1
WAIT_ATTEMPTS = 20
WAIT_DELAY = 0.1
VKMS_DISCOVERY_ATTEMPTS = 100
VKMS_ACTIVATION_ATTEMPTS = 30
VKMS_APPLY_RETRIES = 2
REFRESH_RATE_TOLERANCE_HZ = 0.75
MONITOR_CONFIG_PROPERTY_KEYS = {
    "color-mode",
    "rgb-range",
    "underscanning",
}
GLOBAL_CONFIG_PROPERTY_KEYS = {
    "layout-mode",
}


def _physical_contains_virtual_marker(entry):
    """Return whether a Mutter monitor tuple describes a virtual output."""
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


def physical_connector_names(state):
    """Return the connector names Mutter currently exposes as physical monitors."""
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return []
    return [
        connector for connector in (_connector_name(item) for item in physical_monitors)
        if connector
    ]


def logical_connector_names(state):
    """Return the connector names currently participating in Mutter's layout."""
    try:
        logical_monitors = state[2]
    except (TypeError, IndexError):
        return []
    return [
        connector
        for logical_monitor in logical_monitors
        for connector in _logical_connector_names(logical_monitor)
    ]


def _is_vkms_connector(connector):
    """Match DRM VKMS connectors while deliberately excluding Mutter Meta outputs."""
    return str(connector).lower().startswith("virtual-")


def _physical_monitor(state, connector):
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return None
    return next(
        (monitor for monitor in physical_monitors if _connector_name(monitor) == str(connector)),
        None,
    )


def _monitor_identity(monitor):
    """Return Mutter's stable connector/EDID identity tuple when available."""
    try:
        spec = monitor[0]
        return tuple(str(value) for value in spec[:4])
    except (TypeError, IndexError):
        return ()


def physical_monitor_identities(state):
    """Snapshot connector identities for before/after hot-plug discovery."""
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return {}
    return {
        connector: _monitor_identity(monitor)
        for monitor in physical_monitors
        if (connector := _connector_name(monitor))
    }


def new_vkms_connector_from_state(state, before_identities, width=None, height=None):
    """Find exactly one newly discovered configfs VKMS connector.

    A connector name alone is not enough: stale `Virtual-*` outputs can be
    present, so only a connector absent from the before snapshot (or with a
    changed Mutter identity) is considered.  `Meta-*` is never a candidate.
    """
    before_identities = dict(before_identities or {})
    candidates = []
    for monitor in state[1] if len(state) > 1 else ():
        connector = _connector_name(monitor)
        if not _is_vkms_connector(connector):
            continue
        if before_identities.get(connector) == _monitor_identity(monitor):
            continue
        candidates.append(monitor)
    if len(candidates) == 1:
        return _connector_name(candidates[0]), ""
    if len(candidates) > 1 and width and height:
        matching = []
        for monitor in candidates:
            modes = monitor[1] if len(monitor) > 1 else ()
            if any(int(mode[1]) == int(width) and int(mode[2]) == int(height) for mode in modes):
                matching.append(monitor)
        if len(matching) == 1:
            return _connector_name(matching[0]), ""
    names = ", ".join(_connector_name(monitor) for monitor in candidates) or "none"
    return "", f"expected exactly one new VKMS Virtual-* connector; found {len(candidates)} ({names})"


def wait_for_new_vkms_connector(
    before_identities,
    width,
    height,
    display_config=None,
    attempts=VKMS_DISCOVERY_ATTEMPTS,
    delay=WAIT_DELAY,
):
    """Wait for Mutter to expose the new configfs connector physically."""
    display_config = display_config or display_config_interface()
    latest_error = ""
    for _attempt in range(attempts):
        state = _mutter_state(display_config)
        connector, error = new_vkms_connector_from_state(
            state, before_identities, width, height
        )
        if connector:
            return connector, state, ""
        latest_error = error
        time.sleep(delay)
    return "", None, (
        "Mutter did not expose a new Monitorize VKMS connector in DisplayConfig "
        f"within {attempts * delay:g} seconds. {latest_error}"
    )


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


def active_output_modes(state=None, display_config=None):
    """Return current native modes for connectors in active Mutter layouts."""
    state = _mutter_state(display_config) if state is None else state
    try:
        logical_monitors = state[2]
    except (TypeError, IndexError):
        return {}
    active_connectors = {
        connector
        for logical_monitor in logical_monitors
        for connector in _logical_connector_names(logical_monitor)
    }
    result = {}
    for connector in active_connectors:
        info = monitor_info_from_state(state, connector)
        if info and info["width"] > 0 and info["height"] > 0:
            result[connector] = {
                "width": info["width"],
                "height": info["height"],
                "refresh_rate": info["refresh_rate"],
            }
    return result


def _preferred_mode_for_size(monitor, width, height, target_refresh):
    modes = list(monitor[1])
    candidates = [
        mode for mode in modes
        if int(mode[1]) == int(width) and int(mode[2]) == int(height)
    ]
    if candidates:
        return min(
            candidates,
            key=lambda mode: (abs(float(mode[3]) - float(target_refresh)), str(mode[0])),
        ), False
    preferred = next(
        (
            mode for mode in modes
            if len(mode) > 6
            and getattr(mode[6], "get", lambda _key, _default=False: False)(
                "is-preferred", False
            )
        ),
        None,
    )
    if preferred is None:
        preferred = next(
            (
                mode for mode in modes
                if len(mode) > 6
                and getattr(mode[6], "get", lambda _key, _default=False: False)(
                    "is-current", False
                )
            ),
            None,
        )
    return preferred, True


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


def verified_new_virtual_monitor(
    state,
    before,
    width,
    height,
    refresh_rate,
    refresh_tolerance=REFRESH_RATE_TOLERANCE_HZ,
):
    """Return an exact new virtual monitor and a useful readiness error.

    Mutter state can briefly expose a connector before its current mode is
    populated.  Callers may therefore retry when ``info`` is ``None`` and use
    the returned message if their readiness deadline expires.
    """
    candidates = sorted(
        connector
        for connector in virtual_connectors_from_state(state)
        if connector not in set(before or ())
    )
    if len(candidates) != 1:
        return "", None, (
            "expected exactly one new Mutter virtual connector; "
            f"found {len(candidates)} ({', '.join(candidates) or 'none'})"
        )

    connector = candidates[0]
    info = monitor_info_from_state(state, connector)
    if not info:
        return "", None, f"{connector} has no current mode yet"

    expected_width = int(width)
    expected_height = int(height)
    expected_refresh = float(refresh_rate)
    actual_width = int(info["width"])
    actual_height = int(info["height"])
    actual_refresh = float(info["refresh_rate"])
    if (actual_width, actual_height) != (expected_width, expected_height):
        return "", info, (
            f"{connector} mode is {actual_width}x{actual_height}, expected "
            f"{expected_width}x{expected_height}"
        )
    if abs(actual_refresh - expected_refresh) > float(refresh_tolerance):
        return "", info, (
            f"{connector} refresh rate is {actual_refresh:g}Hz, expected "
            f"{expected_refresh:g}Hz"
        )
    return connector, info, ""


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


def load_saved_virtual_scale(slot="primary", role=None):
    role = role or slot
    return virtual_scale_from_layout(
        load_gnome_virtual_layout(slot).get("logical_monitors"), role
    )


def has_saved_virtual_layout(slot="primary"):
    return bool(load_gnome_virtual_layout(slot).get("logical_monitors"))


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
            # Inactive physical outputs do not need a mode in an
            # ApplyMonitorsConfig payload that preserves only active layouts.
            continue
        try:
            supported_scales = [float(scale) for scale in current[5]]
        except (TypeError, ValueError, IndexError):
            supported_scales = []
        current_modes[connector] = {
            "id": str(current[0]),
            "supported_scales": supported_scales,
        }
    return current_modes


def _current_mode_for_connector(physical_monitors, connector):
    """Return Mutter's current mode for one already-active connector."""
    monitor = next(
        (item for item in physical_monitors if _connector_name(item) == str(connector)),
        None,
    )
    if monitor is None:
        return None
    try:
        return next(
            mode for mode in monitor[1]
            if len(mode) > 6 and mode[6].get("is-current")
        )
    except (TypeError, IndexError, StopIteration, AttributeError):
        return None


def select_vkms_mode(state, connector, width, height, refresh):
    """Select an exact-size Mutter mode without falling back to another size."""
    monitor = _physical_monitor(state, connector)
    if monitor is None:
        return None, f"Mutter did not expose {connector}"
    try:
        modes = list(monitor[1])
    except (TypeError, IndexError):
        return None, f"Mutter reported no modes for {connector}"
    candidates = [
        mode for mode in modes
        if int(mode[1]) == int(width) and int(mode[2]) == int(height)
    ]
    if not candidates:
        available = ", ".join(
            sorted({f"{int(mode[1])}x{int(mode[2])}" for mode in modes if len(mode) > 3})
        ) or "none"
        return None, (
            f"Mutter does not offer the requested VKMS mode {width}x{height} "
            f"on {connector}; available sizes: {available}"
        )
    selected = min(candidates, key=lambda mode: abs(float(mode[3]) - float(refresh)))
    if abs(float(selected[3]) - float(refresh)) > REFRESH_RATE_TOLERANCE_HZ:
        return None, (
            f"Mutter mode {width}x{height}@{float(selected[3]):g}Hz on {connector} "
            f"does not match the requested {float(refresh):g}Hz"
        )
    return selected, ""


def _supported_scale_for_mode(mode):
    try:
        scales = [float(scale) for scale in mode[5] if float(scale) > 0]
    except (TypeError, ValueError, IndexError):
        scales = []
    if not scales:
        return None
    return min(scales, key=lambda scale: abs(scale - 1.0))


def _logical_width(logical_monitor, physical_monitors):
    """Return a logical monitor's width after its active scale/transform."""
    connectors = _logical_connector_names(logical_monitor)
    if not connectors:
        return None
    mode = _current_mode_for_connector(physical_monitors, connectors[0])
    if mode is None:
        return None
    try:
        width, height = int(mode[1]), int(mode[2])
        scale = float(logical_monitor[2])
        transform = int(logical_monitor[3])
    except (TypeError, ValueError, IndexError):
        return None
    if scale <= 0:
        return None
    if transform in (1, 3, 5, 7):
        width = height
    return int(math.ceil(width / scale))


def _right_edge_of_layout(logical_monitors, physical_monitors):
    right_edge = 0
    for logical_monitor in logical_monitors:
        logical_width = _logical_width(logical_monitor, physical_monitors)
        if logical_width is None:
            return None
        try:
            right_edge = max(right_edge, int(logical_monitor[0]) + logical_width)
        except (TypeError, ValueError, IndexError):
            return None
    return right_edge


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


def build_vkms_activation_config(state, connector, width, height, refresh, dbus=None):
    """Append a discovered VKMS connector while preserving Mutter's layout."""
    dbus = dbus or _dbus()
    try:
        _serial, physical_monitors, logical_monitors, _properties = state
    except (TypeError, ValueError):
        return None, {}, "Mutter returned an incomplete DisplayConfig state"
    if connector in logical_connector_names(state):
        return None, {}, f"{connector} is already active in Mutter's layout"
    selected, error = select_vkms_mode(state, connector, width, height, refresh)
    if selected is None:
        return None, {}, error
    scale = _supported_scale_for_mode(selected)
    if scale is None:
        return None, {}, f"Mutter did not report a supported scale for {connector}"
    right_edge = _right_edge_of_layout(logical_monitors, physical_monitors)
    if right_edge is None:
        return None, {}, "Mutter did not report enough current-mode data to preserve the layout"
    current_modes = _current_modes(physical_monitors)
    monitor_properties = _monitor_properties_by_connector(physical_monitors)
    if current_modes is None:
        return None, {}, "Mutter did not report current modes for existing displays"

    configs = []
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        if not connectors or any(name not in current_modes for name in connectors):
            return None, {}, "Mutter did not report current modes for every active display"
        try:
            target = {
                "x": int(logical_monitor[0]),
                "y": int(logical_monitor[1]),
                "scale": float(logical_monitor[2]),
                "transform": int(logical_monitor[3]),
                "primary": bool(logical_monitor[4]),
            }
        except (TypeError, ValueError, IndexError):
            return None, {}, "Mutter returned an invalid active logical-monitor layout"
        config = _logical_monitor_config(
            dbus, logical_monitor, current_modes, monitor_properties, target
        )
        if config is None:
            return None, {}, "Mutter rejected an existing display's current mode or scale"
        configs.append(config)

    selected_mode_id = str(selected[0])
    virtual_config = _monitor_config(
        dbus, connector, selected_mode_id, monitor_properties.get(connector, {})
    )
    values = [
        _typed(dbus, "Int32", right_edge),
        _typed(dbus, "Int32", 0),
        _typed(dbus, "Double", scale),
        _typed(dbus, "UInt32", 0),
        _typed(dbus, "Boolean", False),
        dbus.Array([virtual_config], signature="(ssa{sv})")
        if hasattr(dbus, "Array") else [virtual_config],
    ]
    configs.append(
        dbus.Struct(values, signature="iiduba(ssa{sv})")
        if hasattr(dbus, "Struct") else tuple(values)
    )
    payload = (
        dbus.Array(configs, signature="(iiduba(ssa{sv}))")
        if hasattr(dbus, "Array") else configs
    )
    details = {
        "name": connector,
        "width": int(selected[1]),
        "height": int(selected[2]),
        "refresh_rate": float(selected[3]),
        "mode_id": selected_mode_id,
        "scale": scale,
        "x": right_edge,
        "y": 0,
    }
    return payload, details, ""


def is_monitor_logically_active(state, connector):
    return str(connector) in logical_connector_names(state)


def _new_vkms_connectors(state, before_identities):
    before_identities = dict(before_identities or {})
    connectors = []
    for monitor in state[1] if len(state) > 1 else ():
        connector = _connector_name(monitor)
        if (
            _is_vkms_connector(connector)
            and before_identities.get(connector) != _monitor_identity(monitor)
        ):
            connectors.append(connector)
    return connectors


def _build_layout_without_connectors(state, excluded_connectors, dbus):
    """Preserve every active logical monitor except owned VKMS entries."""
    try:
        _serial, physical_monitors, logical_monitors, _properties = state
    except (TypeError, ValueError):
        return None, "Mutter returned an incomplete DisplayConfig state"
    excluded_connectors = set(excluded_connectors)
    current_modes = _current_modes(physical_monitors)
    monitor_properties = _monitor_properties_by_connector(physical_monitors)
    if current_modes is None:
        return None, "Mutter did not report current modes for active displays"
    configs = []
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        excluded = [name for name in connectors if name in excluded_connectors]
        if excluded:
            if len(connectors) != len(excluded):
                return None, "Refusing to remove a VKMS connector mirrored with a real display"
            continue
        if not connectors or any(name not in current_modes for name in connectors):
            return None, "Mutter did not report current modes for every preserved display"
        try:
            target = {
                "x": int(logical_monitor[0]),
                "y": int(logical_monitor[1]),
                "scale": float(logical_monitor[2]),
                "transform": int(logical_monitor[3]),
                "primary": bool(logical_monitor[4]),
            }
        except (TypeError, ValueError, IndexError):
            return None, "Mutter returned an invalid active logical-monitor layout"
        config = _logical_monitor_config(
            dbus, logical_monitor, current_modes, monitor_properties, target
        )
        if config is None:
            return None, "Mutter rejected a preserved display's current mode or scale"
        configs.append(config)
    if not configs:
        return None, "Refusing to remove every active logical monitor"
    return (
        dbus.Array(configs, signature="(iiduba(ssa{sv}))")
        if hasattr(dbus, "Array") else configs,
        "",
    )


def remove_new_vkms_monitors_from_layout(
    before_identities,
    display_config=None,
    dbus=None,
    attempts=VKMS_ACTIVATION_ATTEMPTS,
    delay=WAIT_DELAY,
):
    """Remove only new Monitorize VKMS logical monitors from Mutter's session layout.

    Mutter can publish the disconnected connector late during device teardown,
    so this intentionally polls briefly after configfs removal as well.
    """
    try:
        dbus = dbus or _dbus()
        display_config = display_config or display_config_interface(dbus=dbus)
        for _attempt in range(attempts):
            state = _mutter_state(display_config)
            targets = [
                connector for connector in _new_vkms_connectors(state, before_identities)
                if is_monitor_logically_active(state, connector)
            ]
            if not targets:
                time.sleep(delay)
                continue
            payload, error = _build_layout_without_connectors(state, targets, dbus)
            if payload is None:
                log.warning("Could not remove stale GNOME VKMS layout: %s", error)
                return False
            try:
                display_config.ApplyMonitorsConfig(
                    _typed(dbus, "UInt32", int(state[0])),
                    _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
                    payload,
                    _variant_dict(
                        dbus,
                        _allowed_properties(state[3], GLOBAL_CONFIG_PROPERTY_KEYS),
                    ),
                )
            except Exception as exc:
                if "serial" in str(exc).lower():
                    continue
                log.warning("Could not apply GNOME VKMS cleanup layout: %s", exc)
                return False
            for _verify in range(attempts):
                current = _mutter_state(display_config)
                if not any(is_monitor_logically_active(current, name) for name in targets):
                    log.info("Removed GNOME VKMS layout entries: %s", ", ".join(targets))
                    return True
                time.sleep(delay)
            return False
        return False
    except Exception as exc:
        log.debug("Failed to remove GNOME VKMS layout: %s", exc)
        return False


def activate_discovered_vkms_monitor(
    before_identities,
    width,
    height,
    refresh,
    display_config=None,
    dbus=None,
    discovery_attempts=VKMS_DISCOVERY_ATTEMPTS,
    activation_attempts=VKMS_ACTIVATION_ATTEMPTS,
    delay=WAIT_DELAY,
):
    """Discover and temporarily activate a configfs VKMS connector in Mutter."""
    try:
        dbus = dbus or _dbus()
        display_config = display_config or display_config_interface(dbus=dbus)
        connector, state, error = wait_for_new_vkms_connector(
            before_identities, width, height, display_config,
            attempts=discovery_attempts, delay=delay,
        )
        if not connector:
            return False, {}, error
        log.info("Discovered GNOME VKMS connector %s", connector)

        for apply_attempt in range(VKMS_APPLY_RETRIES):
            if apply_attempt:
                state = _mutter_state(display_config)
            payload, details, error = build_vkms_activation_config(
                state, connector, width, height, refresh, dbus
            )
            if payload is None:
                return False, {}, error
            log.info(
                "Applying temporary GNOME VKMS layout: %s mode=%s scale=%s position=(%s,%s)",
                connector, details["mode_id"], details["scale"], details["x"], details["y"],
            )
            try:
                display_config.ApplyMonitorsConfig(
                    _typed(dbus, "UInt32", int(state[0])),
                    _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
                    payload,
                    _variant_dict(
                        dbus,
                        _allowed_properties(state[3], GLOBAL_CONFIG_PROPERTY_KEYS),
                    ),
                )
            except Exception as exc:
                if apply_attempt + 1 < VKMS_APPLY_RETRIES and "serial" in str(exc).lower():
                    log.info("Mutter DisplayConfig serial changed; rebuilding VKMS layout")
                    continue
                return False, {}, f"Could not apply GNOME VKMS layout for {connector}: {exc}"

            for _attempt in range(activation_attempts):
                current = _mutter_state(display_config)
                mode = active_output_modes(current).get(connector)
                if (
                    is_monitor_logically_active(current, connector)
                    and mode
                    and mode["width"] == details["width"]
                    and mode["height"] == details["height"]
                    and abs(mode["refresh_rate"] - details["refresh_rate"])
                    <= REFRESH_RATE_TOLERANCE_HZ
                ):
                    return True, details, (
                        f"Mutter activated {connector} at {details['width']}x"
                        f"{details['height']}@{details['refresh_rate']:g}Hz"
                    )
                time.sleep(delay)
            return False, {}, (
                f"Mutter discovered {connector} but did not activate it in the "
                f"logical monitor layout within {activation_attempts * delay:g} seconds"
            )
        return False, {}, "Mutter DisplayConfig serial changed repeatedly during VKMS activation"
    except Exception as exc:
        return False, {}, f"Could not activate GNOME VKMS output: {exc}"


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


def configure_existing_output_mode(
    connector,
    width,
    height,
    target_refresh=60.0,
    attempts=WAIT_ATTEMPTS,
    delay=WAIT_DELAY,
):
    """Select a standard mode on one active Mutter-managed DRM output."""
    try:
        dbus = _dbus()
        display_config = display_config_interface(dbus=dbus)
        state = _mutter_state(display_config)
        serial, physical_monitors, logical_monitors, properties = state
        target_monitor = next(
            (monitor for monitor in physical_monitors if _connector_name(monitor) == connector),
            None,
        )
        if target_monitor is None:
            return False, {}, f"Mutter did not expose {connector}"
        selected, fell_back = _preferred_mode_for_size(
            target_monitor, width, height, target_refresh
        )
        if selected is None:
            return False, {}, f"Mutter reported no usable modes for {connector}"

        current_modes = _current_modes(physical_monitors)
        monitor_properties = _monitor_properties_by_connector(physical_monitors)
        if not current_modes:
            return False, {}, "Mutter did not report the current desktop modes"
        selected_scales = [float(scale) for scale in selected[5]]
        configs = []
        for logical in logical_monitors:
            connectors = _logical_connector_names(logical)
            monitor_configs = []
            scale = float(logical[2])
            if connector in connectors and not _scale_supported(scale, selected_scales):
                if not _scale_supported(1.0, selected_scales):
                    return False, {}, "Mutter's selected VKMS mode does not support the current scale"
                scale = 1.0
            for name in connectors:
                mode_id = str(selected[0]) if name == connector else current_modes[name]["id"]
                monitor_configs.append(
                    _monitor_config(dbus, name, mode_id, monitor_properties.get(name, {}))
                )
            values = [
                _typed(dbus, "Int32", int(logical[0])),
                _typed(dbus, "Int32", int(logical[1])),
                _typed(dbus, "Double", scale),
                _typed(dbus, "UInt32", int(logical[3])),
                _typed(dbus, "Boolean", bool(logical[4])),
                dbus.Array(monitor_configs, signature="(ssa{sv})"),
            ]
            configs.append(dbus.Struct(values, signature="iiduba(ssa{sv})"))
        display_config.ApplyMonitorsConfig(
            _typed(dbus, "UInt32", int(serial)),
            _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
            dbus.Array(configs, signature="(iiduba(ssa{sv}))"),
            _variant_dict(dbus, _allowed_properties(properties, GLOBAL_CONFIG_PROPERTY_KEYS)),
        )

        actual_width, actual_height = int(selected[1]), int(selected[2])
        actual_refresh = float(selected[3])
        for _attempt in range(attempts):
            current = active_output_modes(display_config=display_config).get(connector)
            if (
                current
                and current["width"] == actual_width
                and current["height"] == actual_height
                and abs(current["refresh_rate"] - actual_refresh) <= REFRESH_RATE_TOLERANCE_HZ
            ):
                details = {
                    "name": connector,
                    "width": actual_width,
                    "height": actual_height,
                    "refresh_rate": actual_refresh,
                    "mode_id": str(selected[0]),
                    "fell_back": fell_back,
                }
                actual = f"{actual_width}x{actual_height}@{actual_refresh:g}Hz"
                message = (
                    f"Mutter used preferred VKMS mode {actual}; requested {width}x{height} is unavailable"
                    if fell_back else f"Mutter applied VKMS mode {actual}"
                )
                return True, details, message
            time.sleep(delay)
        return False, {}, "Mutter did not activate the selected VKMS mode"
    except Exception as exc:
        return False, {}, f"Could not configure GNOME VKMS output: {exc}"


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


def map_sunshine_gnome_peripherals(state=None, connector=None):
    """Register Sunshine uinput devices (0xBEEF:0xDEAD) to the virtual monitor's EDID in GNOME."""
    try:
        if state is None:
            state = _mutter_state()
        if not connector:
            connector = virtual_connector_from_state(state)
        if not connector:
            return False
        info = monitor_info_from_state(state, connector)
        if not info:
            return False
        edid = [str(info["vendor"]), str(info["product"]), str(info["serial"])]
        if not all(edid):
            return False
        from gi.repository import Gio
        touch = Gio.Settings.new_with_path(
            "org.gnome.desktop.peripherals.touchscreen",
            "/org/gnome/desktop/peripherals/touchscreens/beef:dead/",
        )
        touch.set_strv("output", edid)

        tablet = Gio.Settings.new_with_path(
            "org.gnome.desktop.peripherals.tablet",
            "/org/gnome/desktop/peripherals/tablets/beef:dead/",
        )
        tablet.set_strv("output", edid)
        tablet.set_string("mapping", "absolute")
        log.info("Mapped Sunshine input devices (beef:dead) to GNOME output %s (%s)", connector, edid)
        return True
    except Exception as exc:
        log.debug("Failed to map Sunshine input devices to GNOME output: %s", exc)
        return False
