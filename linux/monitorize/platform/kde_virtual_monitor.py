"""Exact KWin virtual-output mode configuration."""

import json
import subprocess
import time


KSCREEN_QUERY = ["kscreen-doctor", "-j"]
KSCREEN_ATTEMPTS = 40
KSCREEN_RETRY_DELAY = 0.1
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
    try:
        result = subprocess.run(
            KSCREEN_QUERY,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return json.loads(result.stdout).get("outputs", [])
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return []


def _active(output):
    return output.get("connected", True) and output.get("enabled", True)


def find_kde_output(output_name):
    return next(
        (
            output for output in kde_outputs()
            if _active(output) and output.get("name") == output_name
        ),
        None,
    )


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


def _run_kscreen(setting):
    try:
        result = subprocess.run(
            ["kscreen-doctor", setting],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode == 0:
        return ""
    return result.stderr.strip() or result.stdout.strip() or "unknown error"


def configure_native_virtual_output(
    output_name,
    width,
    height,
    fps,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Select and verify a mode on one exact KWin-created output."""
    output = _wait_for(
        lambda: find_kde_output(output_name), attempts, delay
    )
    if not output:
        return False, {}, f"KWin did not expose {output_name} to KScreen"

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
    if str(output.get("currentModeId")) != mode_id:
        error = _run_kscreen(f"output.{output_selector}.mode.{mode_id}")
        if error:
            return False, {}, f"Could not select KDE mode: {error}"

    active = _wait_for(
        lambda: _output_with_mode(
            output_name, width, height, fps, active=True
        ),
        attempts,
        delay,
    )
    if not active:
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
    return True, details, message
