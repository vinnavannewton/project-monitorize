
"""
Monitorize GUI — Utility functions.
"""

import os
import subprocess



LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def detect_desktop_environment() -> str:
    """
    Return "kde", "gnome", "hyprland", "sway", or "" (unknown) based on
    environment variables.  Checks XDG_CURRENT_DESKTOP, DESKTOP_SESSION,
    and the Hyprland/Sway-specific vars; case-insensitive.
    """
    xdg   = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION",      "").lower()
    
    hypr  = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    sway  = os.environ.get("SWAYSOCK", "")
    combined = xdg + " " + dsess

    if hypr or "hyprland" in combined:
        return "hyprland"
    if sway or "sway" in combined:
        return "sway"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    return ""


def get_local_ip():
    import socket

    try:
        addresses = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=1,
        ).splitlines()
        for address in addresses:
            fields = address.split()
            if fields[1].startswith(("wl", "wlan")):
                return fields[3].split("/")[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP
