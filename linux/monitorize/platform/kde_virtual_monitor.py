"""KDE portal virtual monitor helpers."""

import json
import subprocess
import time

from monitorize.config.settings import load_kde_virtual_layout, save_kde_virtual_layout


KSCREEN_QUERY = ["kscreen-doctor", "-j"]
KSCREEN_ATTEMPTS = 40
KSCREEN_RETRY_DELAY = 0.1


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


def active_kde_output_names():
    return {
        str(output.get("name"))
        for output in kde_outputs()
        if output.get("connected") and output.get("enabled") and output.get("name")
    }


def _new_portal_virtual_output(baseline_names, outputs):
    candidates = [
        output
        for output in outputs
        if output.get("connected")
        and output.get("enabled")
        and str(output.get("name", "")).lower().startswith("virtual-")
        and str(output.get("name")) not in baseline_names
        and output.get("primary") is not True
        and output.get("priority") != 1
    ]
    return candidates[0] if len(candidates) == 1 else None


def _matching_mode(output, width, height, fps):
    tolerance = max(1.0, fps * 0.01)
    return next(
        (
            mode
            for mode in output.get("modes", [])
            if mode.get("size", {}).get("width") == width
            and mode.get("size", {}).get("height") == height
            and abs(float(mode.get("refreshRate", 0)) - fps) <= tolerance
        ),
        None,
    )


def _rounded_cvt_width(width):
    remainder = width % 8
    return width - remainder if remainder else width


def _compatible_mode(output, width, height, fps):
    mode = _matching_mode(output, width, height, fps)
    if mode:
        return mode, False

    rounded_width = _rounded_cvt_width(width)
    if rounded_width == width:
        return None, False

    mode = _matching_mode(output, rounded_width, height, fps)
    if mode:
        return mode, True
    return None, False


def _find_output(name):
    return next(
        (
            output
            for output in kde_outputs()
            if output.get("connected")
            and output.get("enabled")
            and output.get("name") == name
        ),
        None,
    )


def _wait_for(getter, attempts, delay):
    for _attempt in range(attempts):
        value = getter()
        if value:
            return value
        time.sleep(delay)
    return None


def _output_with_mode(output_name, width, height, fps, active=False):
    output = _find_output(output_name)
    mode, rounded = _compatible_mode(output or {}, width, height, fps)
    if not output or not mode:
        return None
    if active and str(output.get("currentModeId")) != str(mode.get("id")):
        return None
    return output, mode, rounded


def _mode_summary(output):
    modes = []
    for mode in output.get("modes", []):
        size = mode.get("size", {})
        width = size.get("width")
        height = size.get("height")
        refresh = mode.get("refreshRate")
        mode_id = mode.get("id", "")
        if width and height and refresh:
            modes.append(f"{mode_id}:{width}x{height}@{refresh}")
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


def _output_pos(output):
    pos = output.get("pos", {})
    return int(float(pos.get("x", 0))), int(float(pos.get("y", 0)))


def _output_width(output):
    size = output.get("size", {})
    scale = float(output.get("scale", 1) or 1)
    return int(float(size.get("width", 0)) / scale)


def _default_position(outputs):
    output = next(
        (
            item for item in outputs
            if item.get("connected") and item.get("enabled")
            and (item.get("primary") or item.get("priority") == 1)
        ),
        None,
    )
    if not output:
        return None
    x, y = _output_pos(output)
    width = _output_width(output)
    return (x + width, y) if width else None


def _rotation_arg(value):
    rotations = {
        "1": "none", "2": "left", "4": "inverted", "8": "right",
        "none": "none", "left": "left", "right": "right", "inverted": "inverted",
    }
    return rotations.get(str(value).strip().lower(), "")


def _position_virtual_output(output_name, outputs):
    layout = load_kde_virtual_layout("primary")
    position = layout["position"] or _default_position(outputs)
    if position:
        _run_kscreen(f"output.{output_name}.position.{position[0]},{position[1]}")
    rotation = _rotation_arg(layout["rotation"])
    if rotation:
        _run_kscreen(f"output.{output_name}.rotation.{rotation}")


def save_current_virtual_layout(slot="primary", output_name=""):
    if not output_name:
        return
    output = next(
        (
            item for item in kde_outputs()
            if item.get("connected") and item.get("enabled")
            and str(item.get("name", "")).lower().startswith("virtual-")
            and (not output_name or item.get("name") == output_name)
        ),
        None,
    )
    if output:
        save_kde_virtual_layout("primary", *_output_pos(output), output.get("rotation", ""))


def configure_portal_virtual_output(
    baseline_names,
    width,
    height,
    fps,
    attempts=KSCREEN_ATTEMPTS,
    delay=KSCREEN_RETRY_DELAY,
):
    """Apply and verify a KDE portal virtual-output mode without touching scale."""
    baseline_names = set(baseline_names or ())
    output = _wait_for(
        lambda: _new_portal_virtual_output(baseline_names, kde_outputs()),
        attempts,
        delay,
    )
    if not output:
        return False, "", "KDE portal virtual output could not be identified safely"

    output_name = str(output["name"])
    mode, rounded = _compatible_mode(output, width, height, fps)
    if not mode:
        found = None
        errors = []
        for timing in ("full", "reduced"):
            error = _run_kscreen(
                (
                    f"output.{output_name}.addCustomMode."
                    f"{width}.{height}.{fps * 1000}.{timing}"
                )
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
            output = _find_output(output_name) or output
            details = f"exposed modes: {_mode_summary(output)}"
            if errors:
                details = f"{details}; registration errors: {'; '.join(errors)}"
            return (
                False,
                output_name,
                f"KDE did not expose the registered custom mode ({details})",
            )
        output, mode, rounded = found

    mode_id = str(mode.get("id", ""))
    if not mode_id:
        return False, output_name, "KDE returned a custom mode without an ID"

    if str(output.get("currentModeId")) != mode_id:
        error = _run_kscreen(f"output.{output_name}.mode.{mode_id}")
        if error:
            return False, output_name, f"Could not select KDE custom mode: {error}"

    if not _wait_for(
        lambda: _output_with_mode(
            output_name, width, height, fps, active=True
        ),
        attempts,
        delay,
    ):
        return False, output_name, "KDE did not activate the requested custom mode"
    _position_virtual_output(output_name, kde_outputs())

    size = mode.get("size", {})
    actual_width = int(size.get("width", width))
    actual_height = int(size.get("height", height))
    actual_refresh = float(mode.get("refreshRate", fps))
    mode_text = f"{actual_width}x{actual_height}@{actual_refresh:g}"
    if rounded:
        mode_text = f"{mode_text} (rounded from {width}x{height}@{fps})"

    return (
        True,
        output_name,
        (
            f"Configured {output_name} to {mode_text} "
            "while preserving KDE scale"
        ),
    )
