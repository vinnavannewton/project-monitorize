"""Restricted client for the packaged Monitorize system-setup helper."""

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path


HELPER = Path("/usr/libexec/monitorize/monitorize-system-setup")


def _input_ready() -> bool:
    try:
        group = grp.getgrnam("monitorize-input")
        username = pwd.getpwuid(os.getuid()).pw_name
        return os.getgid() == group.gr_gid or username in group.gr_mem
    except (KeyError, OSError):
        return False


def get_system_setup_status() -> dict[str, object]:
    """Return availability and current input permission without escalating."""
    return {
        "available": HELPER.is_file() and os.access(HELPER, os.X_OK),
        "input_ready": _input_ready(),
    }


def apply_system_setup(enable_input: bool, enable_firewall: bool) -> dict[str, object]:
    """Authorize and run only the selected packaged setup actions via polkit."""
    command, error = system_setup_command(enable_input, enable_firewall)
    if error:
        return error

    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "System setup timed out."}
    except OSError as exc:
        return {"success": False, "message": f"Could not start system setup: {exc}"}
    return parse_system_setup_result(result.returncode, result.stdout, result.stderr)


def system_setup_command(enable_input: bool, enable_firewall: bool):
    """Build the fixed privileged command without starting it."""
    status = get_system_setup_status()
    if not status["available"]:
        return None, {"success": False, "message": "System setup is available only from the RPM or DEB package."}
    if not enable_input and not enable_firewall:
        return None, {"success": False, "message": "Select at least one system setup action."}

    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None, {"success": False, "message": "Polkit (pkexec) is not installed."}

    command = [pkexec, str(HELPER)]
    if enable_input:
        command.append("--input")
    if enable_firewall:
        command.append("--firewall")

    return command, None


def parse_system_setup_result(returncode, stdout, stderr):
    """Apply the same response rules to synchronous and Qt process callers."""
    if returncode != 0:
        detail = stderr.strip() or "Authorization was cancelled or setup failed."
        return {"success": False, "message": detail}

    try:
        response = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "message": "System setup returned an invalid response."}
    if not isinstance(response, dict):
        return {"success": False, "message": "System setup returned an invalid response."}
    response["success"] = bool(response.get("success", False))
    return response
