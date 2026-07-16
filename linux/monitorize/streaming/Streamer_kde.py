
"""KDE Plasma PipeWire to H.264 TCP streamer."""

import os
import sys

from monitorize.streaming.pipeline_builder import get_encoder
from monitorize.streaming.kde_native_streamer import run_native_streamer
from monitorize.streaming.portal_streamer import run_portal_streamer


width = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
height = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
fps = int(sys.argv[3]) if len(sys.argv) > 3 else 60
bitrate = int(sys.argv[4]) if len(sys.argv) > 4 else 8000
mode = sys.argv[5] if len(sys.argv) > 5 else "usb"
port_override = int(sys.argv[6]) if len(sys.argv) > 6 else None
server_mode = mode == "wifi"
try:
    source_type = int(os.environ.get("MONITORIZE_PORTAL_SOURCE_TYPE", "1"))
except ValueError:
    source_type = 1
selector_hint = os.environ.get(
    "MONITORIZE_PORTAL_SELECTOR_HINT",
    "Select 'TabletDisplay' in the picker.",
)
encoder = get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu"))
host = os.environ.get("MONITORIZE_HOST", "0.0.0.0" if server_mode else "127.0.0.1")
port = int(os.environ.get(
    "MONITORIZE_PORT",
    port_override or (7110 if server_mode else 7112),
))
virtual_slot = os.environ.get("MONITORIZE_KDE_VIRTUAL_SLOT", "").strip()

if virtual_slot:
    sys.exit(run_native_streamer(
        virtual_slot,
        width,
        height,
        fps,
        bitrate,
        mode,
        port,
        encoder,
        host,
    ))

sys.exit(run_portal_streamer(
    "KDE",
    selector_hint,
    width,
    height,
    fps,
    bitrate,
    mode,
    port,
    encoder,
    host,
    source_type=source_type,
))
