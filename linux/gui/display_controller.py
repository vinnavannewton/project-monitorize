"""Compositor-specific virtual output management."""

import json
import re
import shutil
import subprocess

from gui.settings import load_sway_output, save_sway_output


def sway_outputs():
    try:
        result = subprocess.run(
            ["swaymsg", "-t", "get_outputs", "-r"],
            capture_output=True, text=True,
        )
        return json.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, ValueError):
        return []


def prepare_sway_output(width, height, fps, saved=""):
    outputs = sway_outputs()
    names = {output.get("name") for output in outputs}
    output_name = ""
    if saved:
        subprocess.run(
            ["swaymsg", "output", saved, "enable"],
            capture_output=True, text=True,
        )
        outputs = sway_outputs()
        if saved in {output.get("name") for output in outputs}:
            output_name = saved
    if not output_name:
        created = subprocess.run(
            ["swaymsg", "create_output"], capture_output=True, text=True
        )
        if created.returncode != 0:
            return "", created.stderr.strip()
        output_name = next((
            output.get("name") for output in sway_outputs()
            if output.get("name") not in names
        ), "")
        if not output_name:
            return "", "Sway created no detectable output."
    right = max((
        output.get("rect", {}).get("x", 0)
        + output.get("rect", {}).get("width", 0)
        for output in outputs
        if output.get("active") and output.get("name") != output_name
    ), default=0)
    for command in (
        ["output", output_name, "enable"],
        ["output", output_name, "custom_mode", f"{width}x{height}@{fps}Hz"],
        ["output", output_name, "scale", "1"],
        ["output", output_name, "pos", str(right), "0"],
    ):
        result = subprocess.run(["swaymsg", *command], capture_output=True, text=True)
        if result.returncode != 0:
            return "", result.stderr.strip()
    return output_name, ""


def disable_sway_output(output):
    return subprocess.run(
        ["swaymsg", "output", output, "disable"], capture_output=True
    ).returncode == 0


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

    def prepare_sway(self, width, height, fps):
        if shutil.which("swaymsg") is None:
            return "", "Sway support requires swaymsg"
        saved = load_sway_output()
        output, error = prepare_sway_output(width, height, fps, saved)
        if output and output != saved:
            save_sway_output(output)
        self.created_output = output or None
        return output, error

    def sway_mirror_output(self):
        outputs = sway_outputs()
        target = next(
            (output for output in outputs if output.get("focused")),
            next((output for output in outputs if output.get("active")), None),
        )
        return target.get("name", "") if target else ""

    def cleanup(self):
        if not self.created_output:
            return
        if self.de == "hyprland":
            subprocess.run(
                ["hyprctl", "output", "remove", self.created_output],
                capture_output=True,
            )
        elif self.de == "sway":
            disable_sway_output(self.created_output)
        self.created_output = None

