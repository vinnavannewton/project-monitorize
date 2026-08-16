"""Sunshine service helper functions.

Detects, launches, and checks the status of the Sunshine GameStream server.
"""

import os
import shutil
import socket
import subprocess
import webbrowser


SUNSHINE_HTTPS_PORT = 47990
SUNSHINE_HTTP_PORT = 47989
SUNSHINE_WEB_URL = "https://localhost:47990"


def is_sunshine_running(timeout: float = 0.5) -> bool:
    """Check whether Sunshine is already running by checking if its web port is listening."""
    for port in (SUNSHINE_HTTPS_PORT, SUNSHINE_HTTP_PORT):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except OSError:
            pass

    
    try:
        res = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except (FileNotFoundError, OSError):
        pass

    return False


def get_sunshine_candidates() -> list[list[str]]:
    """Return an ordered list of candidate commands to launch Sunshine on this system."""
    candidates: list[list[str]] = []

    
    sunshine_bin = shutil.which("sunshine")
    if sunshine_bin:
        candidates.append([sunshine_bin])

    
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(
                ["systemctl", "--user", "cat", "sunshine.service"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                candidates.append(["systemctl", "--user", "start", "sunshine"])
        except (FileNotFoundError, OSError):
            pass

    
    if shutil.which("flatpak"):
        try:
            res = subprocess.run(
                ["flatpak", "info", "dev.lizardbyte.sunshine"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                candidates.append(["flatpak", "run", "dev.lizardbyte.sunshine"])
        except (FileNotFoundError, OSError):
            pass

    
    for common_path in (
        "/usr/bin/sunshine",
        "/usr/local/bin/sunshine",
        "/opt/sunshine/sunshine",
        "/var/lib/flatpak/exports/bin/dev.lizardbyte.sunshine",
        os.path.expanduser("~/.local/share/flatpak/exports/bin/dev.lizardbyte.sunshine"),
        os.path.expanduser("~/.local/bin/sunshine"),
    ):
        if os.path.isfile(common_path) and os.access(common_path, os.X_OK):
            if [common_path] not in candidates:
                candidates.append([common_path])

    return candidates


def find_sunshine_command() -> list[str] | None:
    """Find the first available command to start Sunshine."""
    candidates = get_sunshine_candidates()
    return candidates[0] if candidates else None


def start_sunshine() -> tuple[bool, str]:
    """Start Sunshine if not already running, trying each candidate in order."""
    if is_sunshine_running():
        return True, "Sunshine is already running."

    candidates = get_sunshine_candidates()
    if not candidates:
        return False, "Sunshine not found. Please start Sunshine or verify it is installed."

    errors = []
    for cmd in candidates:
        try:
            if cmd[0] == "systemctl":
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    return True, "Sunshine service started via systemd."
                errors.append(f"systemctl: {res.stderr.strip() or 'failed'}")
            else:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True, f"Launched Sunshine process ({cmd[0]})."
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{cmd[0]}: {exc}")

    return False, f"Failed to start Sunshine ({'; '.join(errors)})"


def open_sunshine_dashboard() -> bool:
    """Open Sunshine Web UI in the default browser."""
    try:
        return webbrowser.open(SUNSHINE_WEB_URL)
    except Exception:
        return False
