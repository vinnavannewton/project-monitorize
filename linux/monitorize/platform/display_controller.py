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
        try:
            subprocess.run(["hyprctl", "output", "remove", output], capture_output=True)
        except Exception:
            pass
        if slot == "additional":
            self.additional_output = None
        else:
            self.created_output = None

    @staticmethod
    def sway_version_supported():
        """Return whether the running Sway can remove virtual outputs."""
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_version", "-r"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode != 0:
                return False
            version = json.loads(result.stdout).get("human_readable", "")
            match = re.search(r"(\d+)\.(\d+)", version)
            return bool(match and tuple(map(int, match.groups())) >= (1, 8))
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def sway_outputs():
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_outputs", "-r"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            pass
        return []

    @staticmethod
    def _sway_headless_outputs(outputs):
        return {
            str(output.get("name", "")) for output in outputs
            if str(output.get("name", "")).startswith("HEADLESS-")
        }

    @staticmethod
    def _sway_right_edge(outputs):
        edges = []
        for output in outputs:
            if not output.get("active"):
                continue
            rect = output.get("rect", {})
            edges.append(int(rect.get("x", 0)) + int(rect.get("width", 0)))
        return max(edges, default=0)

    def _wait_for_new_sway_output(self, old, timeout_s=2.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            outputs = self.sway_outputs()
            created = sorted(self._sway_headless_outputs(outputs) - old)
            if len(created) == 1:
                return created[0], outputs
            time.sleep(0.1)
        return "", []

    def _wait_for_sway_output_ready(self, output_name, width, height,
                                    timeout_s=2.0, poll_interval_s=0.1):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for output in self.sway_outputs():
                mode = output.get("current_mode") or {}
                if (output.get("name") == output_name and output.get("active")
                        and mode.get("width") == width and mode.get("height") == height):
                    return True
            time.sleep(poll_interval_s)
        return False

    def prepare_sway(self, width, height, fps, slot="primary"):
        if not self.sway_version_supported():
            return "", "Sway 1.8 or newer is required for virtual outputs"

        existing_outputs = self.sway_outputs()
        old = self._sway_headless_outputs(existing_outputs)
        result = subprocess.run(["swaymsg", "create_output"], capture_output=True)
        if result.returncode != 0:
            return "", "Sway could not create a virtual output"

        output, observed_outputs = self._wait_for_new_sway_output(old)
        if not output:
            return "", "Sway did not expose one new virtual output"

        mode = f"{width}x{height}@{fps}Hz"
        configured = subprocess.run(
            ["swaymsg", "output", output, "mode", "--custom", mode,
             "pos", str(self._sway_right_edge(observed_outputs)), "0", "scale", "1"],
            capture_output=True,
        )
        if configured.returncode != 0:
            subprocess.run(["swaymsg", "output", output, "unplug"], capture_output=True)
            return "", f"Sway could not configure {output}"
        if not self._wait_for_sway_output_ready(output, width, height):
            subprocess.run(["swaymsg", "output", output, "unplug"], capture_output=True)
            return "", f"Sway did not activate {output} at the requested resolution"

        if slot == "additional":
            self.additional_output = output
        else:
            self.created_output = output
        return output, ""

    def remove_sway_output(self, slot="primary"):
        output = self.additional_output if slot == "additional" else self.created_output
        if not output or self.de != "sway":
            return
        try:
            subprocess.run(["swaymsg", "output", output, "unplug"], capture_output=True)
        except OSError:
            pass
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
        if self.de == "sway":
            self.remove_sway_output("additional")
            self.remove_sway_output("primary")
            return
        self.remove_hyprland_output("additional")
        self.remove_hyprland_output("primary")
