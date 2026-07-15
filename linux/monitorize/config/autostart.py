"""XDG autostart support for the Monitorize desktop app."""

from pathlib import Path
import os

from monitorize.platform.utils import LINUX_DIR, is_windows

APP_ID = "monitorize"
DESKTOP_FILE = f"{APP_ID}.desktop"
START_IN_TRAY_ARG = "--start-in-tray"
TRAY_AGENT_ARG = "--tray-agent"


def _xdg_config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_data_home():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def autostart_path():
    return _xdg_config_home() / "autostart" / DESKTOP_FILE


def installed_desktop_path():
    return _xdg_data_home() / "applications" / DESKTOP_FILE


def _desktop_quote(value):
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _fallback_entry():
    python = Path(LINUX_DIR) / "venv" / "bin" / "python3"
    return "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        "Name=Monitorize",
        "Comment=Linux to Android Display Bridge",
        f"Exec={_desktop_quote(python)} -m monitorize {TRAY_AGENT_ARG}",
        "Icon=monitorize",
        "Terminal=false",
        "Categories=Utility;System;",
        "StartupNotify=false",
        "StartupWMClass=monitorize",
        "X-GNOME-Autostart-enabled=true",
        f"Path={LINUX_DIR}",
        "",
    ))


def _value_for_key(content, key):
    prefix = f"{key}="
    for line in content.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _is_true(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def _tray_agent_exec(exec_value):
    exec_value = exec_value.replace(START_IN_TRAY_ARG, "").strip()
    if TRAY_AGENT_ARG in exec_value:
        return exec_value
    return f"{exec_value} {TRAY_AGENT_ARG}".strip()


def is_enabled():
    if is_windows():
        return False
    path = autostart_path()
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _is_true(_value_for_key(content, "Hidden")):
        return False
    if str(_value_for_key(content, "X-GNOME-Autostart-enabled")).lower() == "false":
        return False
    return True


def _autostart_content(source):
    if "Exec=" not in source:
        return _fallback_entry()

    lines = []
    found_startup_notify = False
    found_gnome_enabled = False
    for line in source.splitlines():
        if line.startswith("Exec="):
            exec_value = line[len("Exec="):].strip()
            lines.append(f"Exec={_tray_agent_exec(exec_value)}")
        elif line.startswith("StartupNotify="):
            lines.append("StartupNotify=false")
            found_startup_notify = True
        elif line.startswith("X-GNOME-Autostart-enabled="):
            lines.append("X-GNOME-Autostart-enabled=true")
            found_gnome_enabled = True
        elif line.startswith("Hidden="):
            continue
        else:
            lines.append(line)

    if not found_startup_notify:
        lines.append("StartupNotify=false")
    if not found_gnome_enabled:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines).rstrip() + "\n"


def enable():
    source_path = installed_desktop_path()
    if source_path.exists():
        source = source_path.read_text(encoding="utf-8")
        content = _autostart_content(source)
    else:
        content = _fallback_entry()

    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def disable():
    try:
        autostart_path().unlink()
    except FileNotFoundError:
        pass


def set_enabled(enabled):
    if is_windows():
        return "Autostart is not available on Windows yet."
    try:
        if enabled:
            enable()
        else:
            disable()
    except OSError as exc:
        return f"Could not update startup setting: {exc}"
    return ""
