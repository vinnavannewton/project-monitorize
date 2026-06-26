"""Monitorize Linux application module entrypoint."""

import sys


def main():
    light_tray = (
        "--tray-agent" in sys.argv
        or ("--start-in-tray" in sys.argv and "--launch-preset" not in sys.argv)
    )
    if light_tray:
        from monitorize.desktop.tray_agent import main as app_main
    else:
        from monitorize.desktop.main_window import main as app_main
    app_main()


if __name__ == "__main__":
    main()
