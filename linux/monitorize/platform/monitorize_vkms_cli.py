"""Integration client adapter for the standalone monitorize-vkms CLI."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 25.0
DEFAULT_STATUS_TIMEOUT = 5.0
DEFAULT_REMOVE_TIMEOUT = 15.0


MONITORIZE_VKMS_NOT_INSTALLED = "MONITORIZE_VKMS_NOT_INSTALLED"
MONITORIZE_VKMS_PROTOCOL_ERROR = "MONITORIZE_VKMS_PROTOCOL_ERROR"


class MonitorizeVkmsError(RuntimeError):
    """Base error for all monitorize-vkms interactions."""


class VkmsCliNotFoundError(MonitorizeVkmsError):
    """The monitorize-vkms executable is not installed or not in PATH."""

    error_code = MONITORIZE_VKMS_NOT_INSTALLED

    def __init__(
        self,
        message: str = (
            "VKMS Experimental requires the standalone monitorize-vkms package. "
            "Install it from https://github.com/vinnavannewton/monitorize-vkms."
        ),
    ):
        super().__init__(message)


class VkmsProtocolError(MonitorizeVkmsError):
    """The CLI returned malformed, unexpected, or invalid JSON output."""

    error_code = MONITORIZE_VKMS_PROTOCOL_ERROR


class VkmsCommandError(MonitorizeVkmsError):
    """The CLI returned a command failure."""

    def __init__(
        self,
        message: str,
        error_type: str = "unknown_error",
        returncode: int = 1,
        raw_response: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.returncode = returncode
        self.raw_response = raw_response or {}


def format_mode(width: int, height: int, fps: float) -> str:
    """Format dimensions and refresh rate into standard dot-decimal mode string.

    Examples:
        format_mode(2340, 1080, 60) -> "2340x1080@60"
        format_mode(1920, 1080, 59.94) -> "1920x1080@59.94"
    """
    w = int(width)
    h = int(height)
    f = float(fps)
    if f.is_integer() or abs(f - round(f)) < 0.0001:
        fps_str = str(int(round(f)))
    else:
        # Avoid locale-dependent formatting or trailing precision noise
        fps_str = f"{f:.4f}".rstrip("0").rstrip(".")
    return f"{w}x{h}@{fps_str}"


def _translate_error_message(error_type: str, raw_message: str) -> str:
    """Translate machine error_type / error_code into user-friendly diagnostic text."""
    code = error_type.upper()
    lower_type = error_type.lower()
    lower_msg = raw_message.lower()

    if code == "TOPOLOGY_NOT_READY" or "topology" in lower_msg or "bootstrap" in lower_msg:
        return "Monitorize VKMS is installed but not ready. Reboot once after installation."
    if lower_type == "compositor_error":
        return f"Could not activate virtual display in desktop layout: {raw_message}"
    if code == "DISPLAY_ALREADY_ACTIVE" or "already active" in lower_msg or "active virtual display" in lower_msg:
        return "A Monitorize VKMS display is already active."
    if code == "UNSUPPORTED_COMPOSITOR" or "unsupported compositor" in lower_msg:
        return "VKMS Experimental cannot activate a display on this compositor yet."
    if code == "DRM_MODE_READY" or "drm_mode_ready" in lower_msg or "drm mode" in lower_msg:
        return f"VKMS virtual display was created but DRM mode did not become ready: {raw_message}"
    if lower_type in ("polkit_denied", "authorization_denied") or "authorization" in lower_msg or "polkit" in lower_msg:
        return "Administrator authorization was denied or cancelled."
    if lower_type in ("invalid_mode", "mode_error") or code == "INVALID_MODE":
        return f"Invalid virtual display resolution or refresh rate: {raw_message}"
    if lower_type in ("edid_error", "mode_unsupported") or code == "EDID_ERROR":
        return f"The requested mode cannot be represented safely: {raw_message}"
    if lower_type == "drm_error":
        return f"DRM connector error: {raw_message}"
    if lower_type == "helper_error":
        return f"VKMS helper error: {raw_message}"

    return raw_message or f"VKMS operation failed ({error_type})."


class MonitorizeVkmsClient:
    """Client for interacting with the standalone monitorize-vkms tool."""

    def __init__(
        self,
        executable: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.explicit_executable = Path(executable) if executable else None
        self.timeout = timeout

    def find_executable(self) -> Path | None:
        if self.explicit_executable:
            if self.explicit_executable.is_file() and os.access(
                self.explicit_executable, os.X_OK
            ):
                return self.explicit_executable
            return None
        found = shutil.which("monitorize-vkms")
        return Path(found) if found else None

    def is_available(self) -> bool:
        return self.find_executable() is not None

    def _run_cli(
        self, args: list[str], timeout: float | None = None
    ) -> dict[str, Any]:
        executable = self.find_executable()
        if not executable:
            raise VkmsCliNotFoundError(
                "VKMS Experimental requires the standalone monitorize-vkms package. "
                "Install it from https://github.com/vinnavannewton/monitorize-vkms."
            )

        cmd = [str(executable), *args]
        effective_timeout = timeout if timeout is not None else self.timeout
        log.debug("[VKMS] Running CLI command: %s (timeout=%.1fs)", cmd, effective_timeout)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.error("[VKMS] CLI command %s timed out after %.1fs", cmd, effective_timeout)
            raise MonitorizeVkmsError(
                f"The monitorize-vkms operation timed out after {effective_timeout:g}s waiting for display readiness."
            ) from exc
        except OSError as exc:
            log.error("[VKMS] Failed to spawn %s: %s", cmd, exc)
            raise MonitorizeVkmsError(f"Failed to execute monitorize-vkms: {exc}") from exc

        # Copy any stderr to logs
        if res.stderr.strip():
            log.debug("[VKMS] CLI stderr: %s", res.stderr.strip())

        raw_stdout = res.stdout.strip()
        if not raw_stdout:
            raise VkmsProtocolError(
                f"monitorize-vkms returned empty stdout with exit code {res.returncode}. "
                f"Stderr: {res.stderr.strip() or 'none'}"
            )

        # Parse JSON from stdout (find valid JSON object)
        parsed: dict[str, Any] | None = None
        for line in reversed(raw_stdout.splitlines()):
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
            except (json.JSONDecodeError, TypeError):
                continue

        if not parsed:
            try:
                parsed = json.loads(raw_stdout)
            except json.JSONDecodeError as exc:
                raise VkmsProtocolError(
                    f"monitorize-vkms output is not valid JSON: {raw_stdout[:200]!r}"
                ) from exc

        if not isinstance(parsed, dict):
            raise VkmsProtocolError(
                f"monitorize-vkms returned unexpected JSON type ({type(parsed).__name__}): {parsed!r}"
            )

        # Check success
        is_success = bool(parsed.get("success", parsed.get("ok", True)))
        if res.returncode != 0 or not is_success:
            error_type = str(parsed.get("error_code") or parsed.get("error_type") or "command_failed")
            raw_message = str(parsed.get("message") or res.stderr.strip() or "")
            friendly_message = _translate_error_message(error_type, raw_message)
            log.warning(
                "[VKMS] CLI command failed (rc=%d, error_type=%s): %s",
                res.returncode,
                error_type,
                raw_message,
            )
            raise VkmsCommandError(
                message=friendly_message,
                error_type=error_type,
                returncode=res.returncode,
                raw_response=parsed,
            )

        return parsed

    def get_status(self) -> dict[str, Any]:
        """Fetch system status from monitorize-vkms status --json."""
        return self._run_cli(["status", "--json"], timeout=DEFAULT_STATUS_TIMEOUT)

    def check_capability(self) -> str:
        """Query driver capability without requiring root or pkexec."""
        if not self.is_available():
            return "unsupported"
        try:
            status = self.get_status()
            mod_loaded = status.get("kernel_module", {}).get("loaded", False)
            topo_enabled = status.get("topology", {}).get("device_enabled", False)
            if mod_loaded and topo_enabled:
                return "supported"
            return "unsupported"
        except Exception as exc:
            log.debug("[VKMS] Capability check query failed: %s", exc)
            return "check_failed"

    def create_display(
        self, width: int, height: int, fps: float, timeout: float | None = None
    ) -> dict[str, Any]:
        """Create a virtual display with requested resolution and refresh rate."""
        mode_str = format_mode(width, height, fps)
        log.info("[VKMS] Requesting display: %s", mode_str)

        data = self._run_cli(["create", mode_str, "--json"], timeout=timeout)

        connector = str(data.get("drm_connector") or "")
        if not connector:
            raise VkmsProtocolError(
                f"monitorize-vkms create succeeded but did not return 'drm_connector': {data!r}"
            )

        actual_width = int(data.get("width") or width)
        actual_height = int(data.get("height") or height)
        actual_fps = float(data.get("refresh_rate") or fps)
        card = str(data.get("drm_card") or "")

        log.info(
            "[VKMS] monitorize-vkms created %s on %s at %dx%d@%gHz",
            connector,
            card or "DRM",
            actual_width,
            actual_height,
            actual_fps,
        )

        return {
            "name": connector,
            "width": actual_width,
            "height": actual_height,
            "fps": actual_fps,
            "card": card,
            "status": str(data.get("status") or "active"),
            "raw": data,
        }

    def remove_display(
        self,
        connector: str | None = None,
        timeout: float = DEFAULT_REMOVE_TIMEOUT,
    ) -> dict[str, Any]:
        """Remove active virtual display using monitorize-vkms remove --json."""
        if connector:
            log.info("[VKMS] Removing %s through monitorize-vkms", connector)
        else:
            log.info("[VKMS] Removing virtual display through monitorize-vkms")
        try:
            return self._run_cli(["remove", "--json"], timeout=timeout)
        except VkmsCommandError as exc:
            log.warning("[VKMS] CLI remove returned error: %s", exc)
            return {"success": False, "message": str(exc)}
        except Exception as exc:
            log.warning("[VKMS] CLI remove failed: %s", exc)
            return {"success": False, "message": str(exc)}
