"""KDE KRFB virtual monitor compatibility helpers."""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


KRFB_VIRTUAL_MONITOR_APP_ID = "org.kde.krfb-virtualmonitor"
KRFB_VIRTUAL_MONITOR_COMPAT_SOURCE_ID = "org.kde.krfb.virtualmonitor"
MONITORIZE_ALIAS_MARKER = "X-Monitorize-CompatibilityAlias=true"
KDE_KRFB_BROKEN_VERSION = (6, 7, 0)
KSCREEN_QUERY = ["kscreen-doctor", "-j"]
KSCREEN_ATTEMPTS = 20
KSCREEN_RETRY_DELAY = 0.1


def _xdg_data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _xdg_data_dirs():
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(item) for item in raw.split(":") if item]


def _desktop_file_path(data_dir, app_id):
    return data_dir / "applications" / f"{app_id}.desktop"


def _find_desktop_file(app_id):
    candidates = [_desktop_file_path(_xdg_data_home(), app_id)]
    candidates.extend(_desktop_file_path(path, app_id) for path in _xdg_data_dirs())
    return next((path for path in candidates if path.exists()), None)


def _fallback_desktop_entry():
    executable = shutil.which("krfb-virtualmonitor") or "/usr/bin/krfb-virtualmonitor"
    return (
        "# KDE Config File\n"
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Exec={executable}\n"
        "Icon=krfb\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "Name=KRFB Virtual Monitor\n"
        "Comment=Remote Virtual Monitor\n"
        "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1\n"
        f"{MONITORIZE_ALIAS_MARKER}\n"
    )


def _compat_desktop_entry_content():
    source = _find_desktop_file(KRFB_VIRTUAL_MONITOR_COMPAT_SOURCE_ID)
    if source:
        content = source.read_text(encoding="utf-8", errors="replace")
        if MONITORIZE_ALIAS_MARKER not in content:
            content = content.rstrip() + f"\n{MONITORIZE_ALIAS_MARKER}\n"
        return content
    return _fallback_desktop_entry()


def _refresh_kde_service_cache():
    command = shutil.which("kbuildsycoca6") or shutil.which("kbuildsycoca5")
    if not command:
        return
    try:
        subprocess.run([command], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def parse_kde_version(raw):
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", raw or "")
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def detect_kde_version():
    for command in (["kwin_wayland", "--version"], ["plasmashell", "--version"]):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        version = parse_kde_version(f"{result.stdout}\n{result.stderr}")
        if version is not None:
            return version
    return None


def portal_virtual_source_available():
    try:
        import dbus
        bus = dbus.SessionBus()
        desktop = bus.get_object(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
        )
        props = dbus.Interface(desktop, "org.freedesktop.DBus.Properties")
        source_types = int(
            props.Get(
                "org.freedesktop.portal.ScreenCast",
                "AvailableSourceTypes",
            )
        )
        return bool(source_types & 4)
    except Exception:
        return False


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


def should_use_legacy_krfb(log_warning=None):
    if os.environ.get("MONITORIZE_KDE_FORCE_PORTAL") == "1":
        return False

    version = detect_kde_version()
    if os.environ.get("MONITORIZE_KDE_USE_KRFB") == "1":
        if version is not None and version >= KDE_KRFB_BROKEN_VERSION and log_warning:
            log_warning(
                "MONITORIZE_KDE_USE_KRFB=1 forced legacy KRFB on KDE 6.7+; "
                "krfb-virtualmonitor may fail with KDE screencast protocol errors."
            )
        return True

    if version is not None:
        return version < KDE_KRFB_BROKEN_VERSION

    return not portal_virtual_source_available()


def ensure_krfb_virtualmonitor_desktop_entry():
    """Work around KRFB packages whose app ID and desktop filename differ.

    Fedora krfb 26.04.2 installs org.kde.krfb.virtualmonitor.desktop, while the
    helper registers as org.kde.krfb-virtualmonitor. KDE's portal rejects the
    helper when it cannot resolve that exact app ID, so create a user-local
    alias desktop entry before launching it.
    """
    if _find_desktop_file(KRFB_VIRTUAL_MONITOR_APP_ID):
        return None

    destination = _desktop_file_path(_xdg_data_home(), KRFB_VIRTUAL_MONITOR_APP_ID)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_compat_desktop_entry_content(), encoding="utf-8")
        destination.chmod(0o644)
    except OSError as exc:
        return f"Could not create KDE KRFB desktop alias: {exc}"

    _refresh_kde_service_cache()
    return f"Created KDE KRFB desktop alias: {destination}"
