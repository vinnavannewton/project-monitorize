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


def pair_moonlight_pin(pin: str, name: str = "Monitorize Display") -> tuple[bool, str]:
    """Submit a 4-digit Moonlight pairing PIN to Sunshine's local API.

    Returns:
        tuple[bool, str]: (success, status_message)
    """
    clean_pin = str(pin or "").strip()
    if not (len(clean_pin) == 4 and clean_pin.isdigit()):
        return False, "PIN must be exactly 4 digits."

    import json
    import ssl
    import urllib.request

    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    payload = json.dumps({"pin": clean_pin, "name": name}).encode("utf-8")
    req = urllib.request.Request(
        f"{SUNSHINE_WEB_URL}/api/pin",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") is True:
                return True, "Paired successfully! Moonlight is now unlocked."
            else:
                return False, data.get("error", "Pairing failed. Make sure Moonlight is asking for a PIN.")
    except urllib.error.HTTPError as exc:
        try:
            err_data = json.loads(exc.read().decode("utf-8"))
            return False, err_data.get("error", f"Pairing error ({exc.code})")
        except Exception:
            return False, f"Pairing failed with HTTP error {exc.code}."
    except Exception as exc:
        return False, f"Could not connect to Sunshine API: {exc}"


def get_sunshine_config_path() -> str:
    """Return the absolute path to the active sunshine.conf file."""
    config_dir = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(config_dir, "sunshine", "sunshine.conf")


def set_sunshine_output_name(output_name: str) -> tuple[bool, str]:
    """Configure Sunshine to capture a specific virtual display output name.

    Updates sunshine.conf and notifies Sunshine's REST API if running.
    """
    clean_name = str(output_name or "").strip()
    config_path = get_sunshine_config_path()
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    
    lines = []
    found = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("output_name"):
                        lines.append(f"output_name = {clean_name}\n")
                        found = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found:
        lines.append(f"output_name = {clean_name}\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    
    if is_sunshine_running():
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        payload = json.dumps({"output_name": clean_name}).encode("utf-8")
        req = urllib.request.Request(
            f"{SUNSHINE_WEB_URL}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

        
        restart_sunshine()

    return True, f"Configured Sunshine output_name = {clean_name}"


def restart_sunshine() -> tuple[bool, str]:
    """Request Sunshine to restart its process and stream capture via local REST API."""
    if not is_sunshine_running():
        return start_sunshine()

    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        f"{SUNSHINE_WEB_URL}/api/restart",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=4.0) as _resp:
            return True, "Sunshine restarted successfully."
    except Exception as exc:
        return False, f"Could not restart Sunshine via API: {exc}"


def clear_sunshine_output_name() -> tuple[bool, str]:
    """Reset Sunshine output_name configuration back to default."""
    return set_sunshine_output_name("")


