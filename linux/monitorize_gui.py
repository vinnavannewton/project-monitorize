
"""Monitorize GUI launcher."""

import sys

if __name__ == "__main__":
    light_tray = (
        "--tray-agent" in sys.argv
        or ("--start-in-tray" in sys.argv and "--launch-preset" not in sys.argv)
    )
    if light_tray:
        from gui.tray_agent import main
    else:
        from gui.main_window import main
    main()
