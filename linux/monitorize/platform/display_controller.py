"""Compositor-specific virtual output management."""

import json
import re
import subprocess
import time


class DisplayController:
    def __init__(self, de):
        self.de = de
        self.created_output = None
        self.additional_output = None

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

    def prepare_hyprland(self, width, height, fps, slot="primary"):
        old = set(self.headless_monitors())
        result = subprocess.run(
            ["hyprctl", "output", "create", "headless"], capture_output=True
        )
        if result.returncode != 0:
            return "", "Hyprland could not create a headless output"
        deadline = time.monotonic() + 2.0
        created = []
        while time.monotonic() < deadline:
            created = sorted(set(self.headless_monitors()) - old)
            if len(created) == 1:
                break
            time.sleep(0.1)
        if len(created) != 1:
            return "", "Hyprland did not expose one new headless output"
        output = created[0]
        mode = f"{width}x{height}@{fps}"
        configured = subprocess.run(
            ["hyprctl", "keyword", "monitor", f"{output},{mode},auto,1"],
            capture_output=True,
        )
        if configured.returncode != 0:
            subprocess.run(["hyprctl", "output", "remove", output], capture_output=True)
            return "", f"Hyprland could not configure {output}"
        configured = subprocess.run(
            ["hyprctl", "eval", f"hl.monitor({{ output = '{output}', mode = '{mode}', position = 'auto', scale = 1.0 }})"],
            capture_output=True,
        )
        if configured.returncode != 0:
            subprocess.run(["hyprctl", "output", "remove", output], capture_output=True)
            return "", f"Hyprland could not configure {output}"
        if slot == "additional":
            self.additional_output = output
        else:
            self.created_output = output
        return output, ""

    def remove_hyprland_output(self, slot="primary"):
        output = self.additional_output if slot == "additional" else self.created_output
        if not output or self.de != "hyprland":
            return
        subprocess.run(["hyprctl", "output", "remove", output], capture_output=True)
        if slot == "additional":
            self.additional_output = None
        else:
            self.created_output = None

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
        self.remove_hyprland_output("additional")
        self.remove_hyprland_output("primary")
