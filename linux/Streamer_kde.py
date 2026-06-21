
"""KDE Plasma PipeWire to H.264 TCP streamer."""

import os
import sys

from pipeline_builder import get_encoder
from portal_streamer import run_portal_streamer
from gui.kde_virtual_monitor import (
    active_kde_output_names,
    configure_portal_virtual_output,
)


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
baseline_names = active_kde_output_names() if source_type == 4 else set()
prepare_stream = None
if source_type == 4:
    prepare_stream = lambda: configure_portal_virtual_output(
        baseline_names,
        width,
        height,
        fps,
    )

sys.exit(run_portal_streamer(
    "KDE",
    selector_hint,
    width,
    height,
    fps,
    bitrate,
    mode,
    int(os.environ.get(
        "MONITORIZE_PORT",
        port_override or (7110 if server_mode else 7112),
    )),
    get_encoder(os.environ.get("MONITORIZE_ENCODER", "cpu")),
    os.environ.get("MONITORIZE_HOST", "0.0.0.0" if server_mode else "127.0.0.1"),
    source_type=source_type,
    prepare_stream=prepare_stream,
))
