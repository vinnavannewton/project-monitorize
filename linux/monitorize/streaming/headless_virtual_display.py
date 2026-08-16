"""Headless virtual display manager for external capture backends (e.g. Sunshine).

Creates and holds a virtual monitor active in the compositor (KWin/Hyprland)
without starting any GStreamer, encoding, or RTP streaming processes.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time

from monitorize.platform.kde_virtual_monitor import (
    configure_native_virtual_output,
    virtual_slot,
    wait_for_output_absent,
)
from monitorize.streaming.kde_native_streamer import find_helper, _read_helper_event, _stop_helper


def _emit_event(event: dict):
    print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)


def run_kde_headless(slot, width, height, fps):
    slot_info = virtual_slot(slot)
    output_name = slot_info["output_name"]

    if not wait_for_output_absent(output_name):
        print(f"[ERROR] {output_name} is already active; stop existing session", flush=True)
        return 1

    helper_path = find_helper()
    if not helper_path:
        print("[ERROR] KDE native helper is missing. Re-run the Monitorize installer.", flush=True)
        return 1

    helper = subprocess.Popen(
        [
            helper_path,
            slot_info["base_name"],
            slot_info["description"],
            str(width),
            str(height),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def cleanup(*_args):
        _stop_helper(helper)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        owner = _read_helper_event(helper, "owner_ready")
        if owner.get("name") != output_name:
            raise RuntimeError(f"KWin helper created unexpected output {owner.get('name')}")

        ok, actual, message = configure_native_virtual_output(
            output_name, width, height, fps
        )
        if not ok:
            raise RuntimeError(message)

        print(f"[Headless] {message}", flush=True)
        _emit_event({
            "type": "headless_ready",
            "name": output_name,
            "width": actual.get("width", width),
            "height": actual.get("height", height),
            "fps": actual.get("refresh_rate", fps),
            "backend": "Sunshine",
        })

        print(
            f"[Headless] Virtual display {output_name} is active. "
            "Ready for Sunshine / Moonlight streaming.",
            flush=True,
        )

        
        while helper.poll() is None:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    except (BrokenPipeError, OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] KDE headless virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()


def run_hyprland_headless(width, height, fps):
    cmd = ["hyprctl", "output", "create", "headless", f"{width}x{height}@{fps}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Hyprland headless output creation failed: {res.stderr}", flush=True)
        return 1

    output_name = res.stdout.strip() or "HEADLESS-1"
    print(f"[Headless] Created Hyprland output: {output_name}", flush=True)
    _emit_event({
        "type": "headless_ready",
        "name": output_name,
        "width": width,
        "height": height,
        "fps": fps,
        "backend": "Sunshine",
    })

    def cleanup(*_args):
        subprocess.run(["hyprctl", "output", "remove", output_name], capture_output=True)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                line = sys.stdin.readline()
                if not line or line.strip() == "quit":
                    break
        return 0
    finally:
        cleanup()


def main():
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 1080
    fps = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    slot = sys.argv[4] if len(sys.argv) > 4 else "primary"
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if "kde" in de or "plasma" in de:
        sys.exit(run_kde_headless(slot, width, height, fps))
    elif "hyprland" in de:
        sys.exit(run_hyprland_headless(width, height, fps))
    else:
        
        sys.exit(run_kde_headless(slot, width, height, fps))


if __name__ == "__main__":
    main()
