"""KDE portal virtual monitor helpers."""

import json
import subprocess
import time


KSCREEN_QUERY = ["kscreen-doctor", "-j"]
KSCREEN_ATTEMPTS = 20
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
    mode = _matching_mode(output or {}, width, height, fps)
    if not output or not mode:
        return None
    if active and str(output.get("currentModeId")) != str(mode.get("id")):
        return None
    return output, mode


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
    mode = _matching_mode(output, width, height, fps)
    if not mode:
        error = _run_kscreen(
            (
                f"output.{output_name}.addCustomMode."
                f"{width}.{height}.{fps * 1000}.full"
            )
        )
        if error:
            return False, output_name, f"Could not register KDE custom mode: {error}"
        found = _wait_for(
            lambda: _output_with_mode(output_name, width, height, fps),
            attempts,
            delay,
        )
        if not found:
            return False, output_name, "KDE did not expose the registered custom mode"
        output, mode = found

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

    return (
        True,
        output_name,
        (
            f"Configured {output_name} to {width}x{height}@{fps} "
            "while preserving KDE scale and rotation"
        ),
    )
