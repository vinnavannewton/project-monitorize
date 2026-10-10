"""Explicit display ownership through the host Monitorize VKMS system service."""

import json
import math

from monitorize.platform.monitorize_vkms_cli import (
    MonitorizeVkmsError, VkmsCliNotFoundError, VkmsCommandError,
    VkmsProtocolError, _translate_error_message,
)

BUS_NAME = "io.github.vinnavannewton.MonitorizeVkms1"
OBJECT_PATH = "/io/github/vinnavannewton/MonitorizeVkms1"
DISPLAY_IDS = ("mon1", "mon2")
CREATE_TIMEOUT = 220.0  # Host Polkit (90s) plus helper (120s), including reply overhead.
REMOVE_TIMEOUT = 130.0


class MonitorizeVkmsClient:
    """A private bus connection retains the authenticated sender for each holder."""

    def __init__(self, bus=None):
        self._bus = bus

    def _interface(self):
        import dbus
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True)
        return dbus.Interface(self._bus.get_object(BUS_NAME, OBJECT_PATH, introspect=False), BUS_NAME)

    def _call(self, method, *args, timeout=10.0, partial=False):
        try:
            raw = getattr(self._interface(), method)(*args, timeout=timeout)
        except Exception as exc:
            name = exc.get_dbus_name() if hasattr(exc, "get_dbus_name") else ""
            if name in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"):
                raise VkmsCliNotFoundError("Install or upgrade monitorize-vkms on the host to use VKMS displays.") from exc
            message = str(exc)
            kind = "polkit_denied" if "polkit" in message.lower() or "denied" in message.lower() else "helper_error"
            raise VkmsCommandError(_translate_error_message(kind, message), kind,
                                   raw_response={"message": message}) from exc
        try:
            data = json.loads(str(raw))
        except (ValueError, TypeError) as exc:
            raise VkmsProtocolError("The host VKMS service returned invalid JSON.") from exc
        if not isinstance(data, dict) or type(data.get("success")) is not bool:
            raise VkmsProtocolError("The host VKMS service returned an invalid response.")
        if not data["success"] and not partial:
            raise VkmsCommandError(str(data.get("message") or "Host VKMS operation failed."),
                                   raw_response=data)
        return data

    def get_capabilities(self):
        return self._call("GetCapabilities")

    def is_available(self):
        try:
            self.get_capabilities()
            return True
        except MonitorizeVkmsError:
            return False

    def require_ready(self):
        data = self.get_capabilities()
        if type(data.get("api_version")) is not int or data["api_version"] != 1 or data.get("displays") != list(DISPLAY_IDS):
            raise VkmsProtocolError("Upgrade monitorize-vkms on the host for two-display support.")
        if data.get("topology_ready") is not True:
            raise VkmsCommandError(str(data.get("message") or "Reboot after upgrading monitorize-vkms."),
                                   "TOPOLOGY_NOT_READY", raw_response=data)
        if data.get("capability") != "supported":
            raise VkmsCommandError("The host VKMS module needs per-display EDID support. Upgrade and reboot.",
                                   "edid_error", raw_response=data)
        return data

    def get_status(self):
        return self._call("GetStatus")

    @staticmethod
    def _display(display):
        if display not in DISPLAY_IDS:
            raise ValueError("Display must be mon1 or mon2.")
        return display

    def create_display(self, width, height, fps, *, display):
        self._display(display)
        if (type(width) is not int or type(height) is not int or not 1 <= width <= 4095
                or not 1 <= height <= 4095 or not math.isfinite(float(fps)) or not 24 <= float(fps) <= 240):
            raise ValueError("Invalid VKMS display mode.")
        data = self._call("CreateDisplay", display, width, height, float(fps), timeout=CREATE_TIMEOUT)
        if (data.get("slot") != display or not isinstance(data.get("connector"), str) or not data["connector"]
                or not isinstance(data.get("card"), str) or not data["card"]
                or not isinstance(data.get("generation"), str) or not data["generation"]
                or type(data.get("created")) is not bool):
            raise VkmsProtocolError("The host VKMS create response is missing display ownership; use Remove virtual displays for recovery.")
        return {"name": data["connector"], "card": data["card"], "width": width, "height": height,
                "fps": float(fps), "display": display, "generation": data["generation"],
                "created": data["created"], "raw": data}

    def remove_display(self, display, generation):
        self._display(display)
        if not isinstance(generation, str) or not generation:
            raise ValueError("Automatic VKMS cleanup requires its generation.")
        return self._call("DestroyDisplay", display, generation, timeout=REMOVE_TIMEOUT)

    def remove_all(self):
        data = self._call("DestroyAll", timeout=REMOVE_TIMEOUT * 2, partial=True)
        results = data.get("results")
        if (not isinstance(results, dict) or set(results) != set(DISPLAY_IDS)
                or any(not isinstance(item, dict) for item in results.values())):
            raise VkmsProtocolError("The host VKMS service returned invalid removal results.")
        return data
