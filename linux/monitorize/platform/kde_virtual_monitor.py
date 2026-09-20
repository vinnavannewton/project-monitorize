"""Exact KWin virtual-output mode configuration."""

import json
import math
import os
import subprocess
import time


KSCREEN_QUERY = ["kscreen-doctor", "-j"]
KSCREEN_ATTEMPTS = 40
KSCREEN_RETRY_DELAY = 0.1
KSCREEN_QUERY_ATTEMPTS = 10
KSCREEN_QUERY_DELAY = 0.2
KSCREEN_QUERY_TIMEOUT = 5
_LAST_KSCREEN_QUERY_ERROR = ""
KDE_VIRTUAL_SLOTS = {
    "primary": {
        "base_name": "Monitorize-1",
        "output_name": "Virtual-Monitorize-1",
        "description": "Monitorize Display 1",
    },
    "additional": {
        "base_name": "Monitorize-2",
        "output_name": "Virtual-Monitorize-2",
        "description": "Monitorize Display 2",
    },
}


def virtual_slot(slot):
    try:
        return KDE_VIRTUAL_SLOTS[slot]
    except KeyError as exc:
        raise ValueError(f"Unknown KDE virtual display slot: {slot}") from exc


def kde_outputs():
    global _LAST_KSCREEN_QUERY_ERROR
    try:
        result = subprocess.run(
            _kscreen_command(*KSCREEN_QUERY[1:]),
            capture_output=True,
            text=True,
            timeout=KSCREEN_QUERY_TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        _LAST_KSCREEN_QUERY_ERROR = str(exc)
        return []
    if result.returncode != 0:
        _LAST_KSCREEN_QUERY_ERROR = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"kscreen-doctor exited with code {result.returncode}"
        )
        return []
    try:
        outputs = json.loads(result.stdout).get("outputs", [])
    except (TypeError, ValueError) as exc:
        _LAST_KSCREEN_QUERY_ERROR = f"invalid kscreen-doctor JSON: {exc}"
        return []
    if not isinstance(outputs, list) or not outputs:
        _LAST_KSCREEN_QUERY_ERROR = "kscreen-doctor returned no outputs"
        return []
    _LAST_KSCREEN_QUERY_ERROR = ""
    return outputs


def kde_output_query_error():
    return _LAST_KSCREEN_QUERY_ERROR or "unknown host KScreen error"


def wait_for_kde_outputs(
    attempts=KSCREEN_QUERY_ATTEMPTS,
    delay=KSCREEN_QUERY_DELAY,
):
    """Retry the host inventory because KScreen can be briefly unavailable."""
    return _wait_for(kde_outputs, attempts, delay)


def _active(output):
    return output.get("connected", True) and output.get("enabled", True)


def active_output_modes(outputs=None):
    """Return current native modes for active KScreen outputs by connector."""
    result = {}
    for output in kde_outputs() if outputs is None else outputs:
        name = str(output.get("name") or "")
        if not name or not _active(output):
            continue
        current_mode_id = str(output.get("currentModeId") or "")
        current_mode = next(
            (
                mode
                for mode in output.get("modes", [])
                if str(mode.get("id")) == current_mode_id
            ),
            None,
        )
        size = (current_mode or output).get("size") or {}
        width = int(size.get("width") or 0)
        height = int(size.get("height") or 0)
        if width > 0 and height > 0:
            result[name] = {
                "width": width,
                "height": height,
                "refresh_rate": float((current_mode or {}).get("refreshRate") or 0),
            }
    return result


def _find_kde_output(outputs, output_name):
    return next(
        (
            output for output in outputs
            if _active(output) and output.get("name") == output_name
        ),
        None,
    )


def find_kde_output(output_name):
    return _find_kde_output(kde_outputs(), output_name)


def _output_snapshot(output_name):
    outputs = kde_outputs()
    output = _find_kde_output(outputs, output_name)
    return (output, outputs) if output else None


def output_is_active(output_name):
    return find_kde_output(output_name) is not None


def wait_for_output_absent(output_name, attempts=20, delay=KSCREEN_RETRY_DELAY):
    for _attempt in range(attempts):
        if not output_is_active(output_name):
            return True
        time.sleep(delay)
    return False


def _wait_for(getter, attempts, delay):
    for _attempt in range(attempts):
        value = getter()
        if value:
            return value
        time.sleep(delay)
    return None


def _matching_mode(output, width, height, fps):
    tolerance = max(0.1, fps * 0.01)
    return next(
        (
            mode for mode in output.get("modes", [])
            if mode.get("size", {}).get("width") == width
            and mode.get("size", {}).get("height") == height
            and abs(float(mode.get("refreshRate", 0)) - fps) <= tolerance
        ),
        None,
    )


def _compatible_mode(output, width, height, fps):
    exact = _matching_mode(output, width, height, fps)
    if exact:
        return exact, False
    rounded_width = width - (width % 8)
    if rounded_width == width:
        return None, False
    return _matching_mode(output, rounded_width, height, fps), True


def _output_with_mode(output_name, width, height, fps, active=False):
    output = find_kde_output(output_name)
    if not output:
        return None
    mode, rounded = _compatible_mode(output, width, height, fps)
    if not mode:
        return None
    if active and str(output.get("currentModeId")) != str(mode.get("id")):
        return None
    return output, mode, rounded


def _mode_summary(output):
    modes = []
    for mode in output.get("modes", []):
        size = mode.get("size", {})
        if size.get("width") and size.get("height") and mode.get("refreshRate"):
            modes.append(
                f"{mode.get('id', '')}:{size['width']}x{size['height']}"
                f"@{mode['refreshRate']}"
            )
    return ", ".join(modes) or "none"


def _run_kscreen(*settings):
    try:
        result = subprocess.run(
            _kscreen_command(*settings),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode == 0:
        return ""
    return result.stderr.strip() or result.stdout.strip() or "unknown error"


def _kscreen_command(*arguments):
    command = ["kscreen-doctor", *arguments]
    if os.path.isfile("/.flatpak-info"):
        return ["flatpak-spawn", "--host", "--directory=/", *command]
    return command


def _has_replication_source(output):
    source = output.get("replicationSource")
    return source not in (None, "", 0, "0")


def _position(output):
    position = output.get("pos") or {}
    return int(position.get("x") or 0), int(position.get("y") or 0)


def _logical_width(output):
    size = output.get("size") or {}
    width = float(size.get("width") or 0)
    scale = float(output.get("scale") or 1)
    if width <= 0 or scale <= 0:
        return 0
    return math.ceil(width / scale)


def _extension_position(output, outputs):
    """Return a safe position when KWin restored mirroring or an exact overlap."""
    current = _position(output)
    output_id = output.get("id")
    output_name = output.get("name")
    others = [
        candidate for candidate in outputs
        if _active(candidate)
        and not (
            candidate.get("id") == output_id
            and candidate.get("name") == output_name
        )
    ]
    overlaps = any(_position(candidate) == current for candidate in others)
    if not others or not (_has_replication_source(output) or overlaps):
        return None

    right_edge = max(
        _position(candidate)[0] + _logical_width(candidate)
        for candidate in others
    )
    return right_edge, 0


def _runtime_output_key(output):
    output_id = output.get("id")
    if output_id is not None:
        return "id", str(output_id)
    return (
        "fallback",
        str(output.get("name") or ""),
        str(output.get("uuid") or ""),
    )


def _find_runtime_output(outputs, target):
    target_key = _runtime_output_key(target)
    return next(
        (
            output for output in outputs
            if _runtime_output_key(output) == target_key
        ),
        None,
    )


def wait_for_new_kde_output(
    before_outputs,
    mapping_id="",
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Find exactly one output added by a portal Start request."""
    before_keys = {_runtime_output_key(output) for output in before_outputs}
    mapping_id = str(mapping_id or "").strip()
    expected_names = {mapping_id, f"Virtual-{mapping_id}"} if mapping_id else set()

    def added_snapshot():
        outputs = kde_outputs()
        added = [
            output for output in outputs
            if _active(output) and _runtime_output_key(output) not in before_keys
        ]
        named = [
            output for output in added
            if str(output.get("name") or "") in expected_names
        ]
        if len(named) == 1:
            return named[0], outputs
        if len(added) == 1:
            return added[0], outputs
        return None

    return _wait_for(added_snapshot, attempts, delay)


def repair_kde_extended_topology(
    output,
    observed_outputs,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Clear mirroring and repair an exact overlap on one existing output."""
    output_id = output.get("id")
    output_name = str(output.get("name") or "")
    output_selector = str(output_id if output_id is not None else output_name)
    was_mirrored = _has_replication_source(output)
    repair_position = _extension_position(output, observed_outputs)
    settings = [f"output.{output_selector}.mirror.none"]
    if repair_position is not None:
        x, y = repair_position
        settings.append(f"output.{output_selector}.position.{x},{y}")

    error = _run_kscreen(*settings)
    if error:
        return False, {}, f"Could not repair KDE virtual output: {error}"

    def repaired_snapshot():
        outputs = kde_outputs()
        current = _find_runtime_output(outputs, output)
        if not current or _has_replication_source(current):
            return None
        if repair_position is not None and _position(current) != repair_position:
            return None
        return current, outputs

    repaired = _wait_for(repaired_snapshot, attempts, delay)
    if not repaired:
        current = _find_runtime_output(kde_outputs(), output)
        if current and _has_replication_source(current):
            return False, {}, "KDE did not clear mirroring on the portal output"
        if current and repair_position is not None:
            return False, {}, "KDE did not separate the overlapping portal output"
        return False, {}, "The KDE portal output disappeared during topology repair"

    current, _outputs = repaired
    details = {
        "id": current.get("id"),
        "name": str(current.get("name") or output_name),
        "position": _position(current),
        "position_repaired": repair_position is not None,
        "was_mirrored": was_mirrored,
    }
    return True, details, "KDE portal output uses an extended layout"


def wait_for_kde_output_absent(
    output,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Wait until one exact runtime output has been removed."""
    return bool(
        _wait_for(
            lambda: not _find_runtime_output(kde_outputs(), output),
            attempts,
            delay,
        )
    )


def _configured_output(output_name, width, height, fps, repair_position):
    configured = _output_with_mode(
        output_name, width, height, fps, active=True
    )
    if not configured or _has_replication_source(configured[0]):
        return None
    if repair_position is not None and _position(configured[0]) != repair_position:
        return None
    return configured


def configure_native_virtual_output(
    output_name,
    width,
    height,
    fps,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Select and verify a mode on one exact KWin-created output."""
    snapshot = _wait_for(lambda: _output_snapshot(output_name), attempts, delay)
    if not snapshot:
        return False, {}, f"KWin did not expose {output_name} to KScreen"
    output, observed_outputs = snapshot

    output_id = output.get("id")
    output_selector = str(output_id if output_id is not None else output_name)
    output_uuid = str(output.get("uuid") or "").strip()

    mode, rounded = _compatible_mode(output, width, height, fps)
    if not mode:
        found = None
        errors = []
        for timing in ("reduced", "full"):
            error = _run_kscreen(
                f"output.{output_selector}.addCustomMode."
                f"{width}.{height}.{fps * 1000}.{timing}"
            )
            if error:
                errors.append(f"{timing}: {error}")
                continue
            found = _wait_for(
                lambda: _output_with_mode(output_name, width, height, fps),
                attempts,
                delay,
            )
            if found:
                break
        if not found:
            output = find_kde_output(output_name) or output
            details = f"exposed modes: {_mode_summary(output)}"
            if errors:
                details += f"; registration errors: {'; '.join(errors)}"
            return False, {}, f"KDE did not expose the requested mode ({details})"
        output, mode, rounded = found

    mode_id = str(mode.get("id") or "")
    if not mode_id:
        return False, {}, "KDE returned a mode without an ID"
    repair_position = _extension_position(output, observed_outputs)
    settings = [
        f"output.{output_selector}.mirror.none",
        f"output.{output_selector}.mode.{mode_id}",
    ]
    if repair_position is not None:
        x, y = repair_position
        settings.append(f"output.{output_selector}.position.{x},{y}")
    error = _run_kscreen(*settings)
    if error:
        return False, {}, f"Could not configure KDE virtual output: {error}"

    active = _wait_for(
        lambda: _configured_output(
            output_name, width, height, fps, repair_position
        ),
        attempts,
        delay,
    )
    if not active:
        output = find_kde_output(output_name)
        if output and _has_replication_source(output):
            return False, {}, "KDE did not clear mirroring on the virtual output"
        if output and repair_position is not None:
            if _position(output) != repair_position:
                return False, {}, "KDE did not separate the overlapping virtual output"
        return False, {}, "KDE did not activate the selected mode"
    output, mode, rounded = active

    size = mode.get("size", {})
    actual_width = int(size.get("width", width))
    actual_height = int(size.get("height", height))
    actual_refresh = float(mode.get("refreshRate", fps))
    details = {
        "name": output_name,
        "uuid": output_uuid,
        "selector": output_selector,
        "width": actual_width,
        "height": actual_height,
        "refresh_rate": actual_refresh,
        "mode_id": mode_id,
        "topology_repaired": repair_position is not None,
        "rounded": bool(
            rounded
            or actual_width != width
            or actual_height != height
            or abs(actual_refresh - fps) > 0.01
        ),
    }
    actual = f"{actual_width}x{actual_height}@{actual_refresh:g}"
    requested = f"{width}x{height}@{fps}"
    message = (
        f"KWin applied {actual} (requested {requested})"
        if details["rounded"] else f"KWin applied {actual}"
    )
    if details["topology_repaired"]:
        message += " and restored an extended layout"
    return True, details, message


def _preferred_existing_mode(output, width, height, target_refresh=60.0):
    candidates = []
    for mode in output.get("modes", []):
        size = mode.get("size") or {}
        if int(size.get("width") or 0) != int(width):
            continue
        if int(size.get("height") or 0) != int(height):
            continue
        candidates.append(mode)
    if not candidates:
        preferred_id = str(output.get("preferredModeId") or output.get("currentModeId") or "")
        return next(
            (mode for mode in output.get("modes", []) if str(mode.get("id")) == preferred_id),
            None,
        ), True
    return min(
        candidates,
        key=lambda mode: (
            abs(float(mode.get("refreshRate") or 0) - float(target_refresh)),
            str(mode.get("id") or ""),
        ),
    ), False


def configure_existing_output_mode(
    output_name,
    width,
    height,
    target_refresh=60.0,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Select a standard mode on an existing DRM output without creating one."""
    output = _wait_for(lambda: find_kde_output(output_name), attempts, delay)
    if not output:
        return False, {}, f"KWin did not expose {output_name} to KScreen"
    mode, fell_back = _preferred_existing_mode(
        output, width, height, target_refresh
    )
    if not mode:
        return False, {}, f"KDE reported no usable modes for {output_name}"
    size = mode.get("size") or {}
    mode_id = str(mode.get("id") or "")
    if not mode_id:
        return False, {}, f"KDE returned a mode without an ID for {output_name}"
    selector = str(output.get("id") if output.get("id") is not None else output_name)
    error = _run_kscreen(
        f"output.{selector}.mirror.none",
        f"output.{selector}.mode.{mode_id}",
    )
    if error:
        return False, {}, f"Could not configure KDE VKMS output: {error}"

    def active_mode():
        current = find_kde_output(output_name)
        if current and str(current.get("currentModeId") or "") == mode_id:
            return current
        return None

    if not _wait_for(active_mode, attempts, delay):
        return False, {}, "KDE did not activate the selected VKMS mode"
    actual_width = int(size.get("width") or width)
    actual_height = int(size.get("height") or height)
    actual_refresh = float(mode.get("refreshRate") or target_refresh)
    details = {
        "name": output_name,
        "width": actual_width,
        "height": actual_height,
        "refresh_rate": actual_refresh,
        "mode_id": mode_id,
        "fell_back": fell_back,
    }
    actual = f"{actual_width}x{actual_height}@{actual_refresh:g}Hz"
    message = (
        f"KDE used preferred VKMS mode {actual}; requested {width}x{height} is unavailable"
        if fell_back else f"KDE applied VKMS mode {actual}"
    )
    return True, details, message
