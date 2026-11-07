"""Monitorize Linux application module entrypoint."""

import sys


def _remote_controller_required():
    import os
    from monitorize.remote_login.settings import SETTINGS, read_settings
    if not SETTINGS.exists():
        return False
    try:
        settings = read_settings()
        return settings.enabled and settings.uid == os.getuid()
    except ValueError:
        return False


def _use_light_tray(argv):
    if '--host-controller' in argv or _remote_controller_required():
        return False
    tray_requested = '--tray-agent' in argv or '--start-in-tray' in argv
    if not tray_requested or '--launch-preset' in argv:
        return False
    if '--restore-from-tray' in argv:
        return True
    from monitorize.config.settings import load_general_settings
    return load_general_settings().get('streaming_backend', 'sunshine') == 'none'


def main():
    argv = sys.argv[1:]
    if argv and (argv[0] in ('-h', '--help') or not argv[0].startswith('-')):
        from monitorize.host_cli import main as cli_main
        return cli_main(argv)
    from monitorize.config import host_config
    try:
        if not host_config.config_path().exists():
            host_config.initialize()
    except (OSError, ValueError) as error:
        print(f"Monitorize: Could not initialize monitorize.json: {error}", file=sys.stderr)
    light_tray = _use_light_tray(sys.argv)
    if light_tray:
        from monitorize.desktop.tray_agent import main as app_main
    else:
        if '--tray-agent' in sys.argv and '--start-in-tray' not in sys.argv:
            sys.argv.append('--start-in-tray')
        from monitorize.desktop.main_window import main as app_main
    app_main()


if __name__ == "__main__":
    raise SystemExit(main())
