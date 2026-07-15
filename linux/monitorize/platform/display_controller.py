"""Compositor-specific virtual output management."""

import json
import re
import subprocess
import time


class DisplayController:
    def __init__(self, de):
        self.de = de
        self.created_output = None

    def headless_monitors(self):
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "all", "-j"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return [
                    item.get("name") for item in json.loads(result.stdout)
                    if item.get("name", "").startswith("HEADLESS")
                ]
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "all"],
                capture_output=True, text=True,
            )
            return list(set(re.findall(r"\bHEADLESS-\d+\b", result.stdout)))
        except Exception:
            return []

    def prepare_hyprland(self, width, height, fps):
        old = set(self.headless_monitors())
        subprocess.run(["hyprctl", "output", "create", "headless"], capture_output=True)
        self.created_output = next(iter(set(self.headless_monitors()) - old), "HEADLESS-1")
        mode = f"{width}x{height}@{fps}"
        subprocess.run(
            ["hyprctl", "keyword", "monitor", f"{self.created_output},{mode},auto,1"],
            capture_output=True,
        )
        subprocess.run(
            ["hyprctl", "eval", f"hl.monitor({{ output = '{self.created_output}', mode = '{mode}', position = 'auto', scale = 1.0 }})"],
            capture_output=True,
        )
        return self.created_output, ""

    def wait_for_headless_ready(self, output_name, width, height,
                                timeout_s=2.0, poll_interval_s=0.1):
        """Poll hyprctl until *output_name* appears with the expected resolution.

        Returns True if the output was detected with the correct mode before
        *timeout_s* elapsed, False otherwise.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ["hyprctl", "monitors", "all", "-j"],
                    capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0:
                    for mon in json.loads(result.stdout):
                        if mon.get("name") == output_name:
                            if (mon.get("width", 0) == width
                                    and mon.get("height", 0) == height):
                                return True
            except Exception:
                pass
            time.sleep(poll_interval_s)
        return False

    def cleanup(self):
        if not self.created_output:
            return
        if self.de == "hyprland":
            subprocess.run(
                ["hyprctl", "output", "remove", self.created_output],
                capture_output=True,
            )
        self.created_output = None
