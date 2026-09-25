
"""
Monitorize GUI — Utility functions.
"""

import os
import subprocess


PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINUX_DIR = os.path.dirname(PACKAGE_DIR)
ASSETS_DIR = os.path.join(PACKAGE_DIR, "assets")
QML_DIR = os.path.join(PACKAGE_DIR, "qml")


def detect_desktop_environment() -> str:
    """
    Return "cosmic", "cinnamon", "kde", "gnome", "hyprland", "sway", or ""
    (unknown) based on environment variables. Checks XDG_CURRENT_DESKTOP,
    XDG_SESSION_DESKTOP, DESKTOP_SESSION,
    and compositor-specific variables; case-insensitive.
    """
    xdg   = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    dsess = os.environ.get("DESKTOP_SESSION",      "").lower()
    session_desktop = os.environ.get("XDG_SESSION_DESKTOP", "").lower()

    hypr  = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    sway = os.environ.get("SWAYSOCK", "")
    combined = xdg + " " + dsess

    if hypr or "hyprland" in combined:
        return "hyprland"
    if sway or "sway" in combined:
        return "sway"
    if "cosmic" in xdg:
        return "cosmic"
    if "cinnamon" in combined:
        return "cinnamon"
    if "kde" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    if "cosmic" in combined:
        return "cosmic"
    for name in ("hyprland", "sway", "cinnamon", "cosmic", "kde", "gnome"):
        if name in session_desktop:
            return name
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

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
