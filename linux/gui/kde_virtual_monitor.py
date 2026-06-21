"""KDE KRFB virtual monitor compatibility helpers."""

import os
import re
import shutil
import subprocess
from pathlib import Path


KRFB_VIRTUAL_MONITOR_APP_ID = "org.kde.krfb-virtualmonitor"
KRFB_VIRTUAL_MONITOR_COMPAT_SOURCE_ID = "org.kde.krfb.virtualmonitor"
MONITORIZE_ALIAS_MARKER = "X-Monitorize-CompatibilityAlias=true"
KDE_KRFB_BROKEN_VERSION = (6, 7, 0)


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
