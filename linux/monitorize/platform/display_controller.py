"""Compositor-specific virtual output management."""

import json
import re
import subprocess


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

    def cleanup(self):
        if not self.created_output:
            return
        if self.de == "hyprland":
            subprocess.run(
                ["hyprctl", "output", "remove", self.created_output],
                capture_output=True,
            )
        self.created_output = None
