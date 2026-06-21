
"""Monitorize touch daemon compatibility entrypoint."""

import logging
import os
import sys

from input_bridge.daemon import InputDaemon
from input_bridge.geometry import Geometry, detect_de


def main():
    debug = "--debug" in sys.argv
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="[TouchDaemon] %(levelname)s %(message)s",
    )
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
    daemon = InputDaemon(
        width,
        height,
        wifi="--wifi" in sys.argv,
        stylus_features="--stylus-features" in sys.argv,
        stylus_only="--stylus-only" in sys.argv,
    )
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.close()



_DETECTED_DE = detect_de()


def _map_sway_uinput_devices(device_names):
    return Geometry(_DETECTED_DE, 2560, 1600).map_sway_devices(device_names)


if __name__ == "__main__":
    main()
