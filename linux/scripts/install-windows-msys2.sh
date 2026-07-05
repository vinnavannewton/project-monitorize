#!/usr/bin/env bash
# Monitorize Windows receiver developer setup for MSYS2 UCRT64.
#
# Run from the "UCRT64" MSYS2 shell:
#   cd /c/path/to/Monitorize-windows/linux/scripts
#   ./install-windows-msys2.sh

set -euo pipefail

if [[ "${MSYSTEM:-}" != "UCRT64" ]]; then
    echo "Run this from the MSYS2 UCRT64 shell, not ${MSYSTEM:-an unknown shell}." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UCRT_PREFIX="mingw-w64-ucrt-x86_64"

pacman -S --needed \
    "${UCRT_PREFIX}-python" \
    "${UCRT_PREFIX}-python-pip" \
    "${UCRT_PREFIX}-python-pyqt6" \
    "${UCRT_PREFIX}-python-gobject" \
    "${UCRT_PREFIX}-gstreamer" \
    "${UCRT_PREFIX}-gst-plugins-base" \
    "${UCRT_PREFIX}-gst-plugins-good" \
    "${UCRT_PREFIX}-gst-plugins-bad" \
    "${UCRT_PREFIX}-gst-plugins-ugly" \
    "${UCRT_PREFIX}-gst-libav"

python -m pip install --upgrade pip
python -m pip install -r "${PROJECT_DIR}/requirements-windows.txt"

PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python - <<'PY'
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst

Gst.init(None)
missing = [
    name for name in ("d3d11videosink", "avdec_h264", "tcpclientsrc", "h264parse")
    if Gst.ElementFactory.find(name) is None
]
if missing:
    raise SystemExit("Missing required GStreamer elements: " + ", ".join(missing))

print("Monitorize Windows receiver dependencies are installed.")
print("Run with:")
print(f"  cd {PROJECT_DIR}")
print("  PYTHONPATH=. python -m monitorize")
PY
