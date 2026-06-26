
"""Hyprland/Sway PipeWire to H.264 TCP streamer."""

import os
import sys

from monitorize.streaming.pipeline_builder import get_encoder
from monitorize.streaming.portal_streamer import run_portal_streamer


width = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
height = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
fps = int(sys.argv[3]) if len(sys.argv) > 3 else 60
bitrate = int(sys.argv[4]) if len(sys.argv) > 4 else 8000
mode = sys.argv[5] if len(sys.argv) > 5 else "usb"
server_mode = mode == "wifi"
compositor = "Sway" if os.environ.get("SWAYSOCK") else "Hyprland"
mirror = len(sys.argv) > 6 and sys.argv[6] == "mirror"

sys.exit(run_portal_streamer(
    compositor,
    "Select your primary monitor in the picker."
    if mirror else "Select the HEADLESS monitor in the picker.",
    width,
    height,
    fps,
    bitrate,
    mode,
    int(os.environ.get("MONITORIZE_PORT", 7110 if server_mode else 7112)),
    get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu")),
    os.environ.get("MONITORIZE_HOST", "0.0.0.0" if server_mode else "127.0.0.1"),
))
