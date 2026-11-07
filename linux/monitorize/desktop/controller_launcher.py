"""Wait for the user manager's graphical connection before importing Qt."""
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time


DISPLAY_ENVIRONMENT = ('WAYLAND_DISPLAY', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR',
                       'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP', 'DBUS_SESSION_BUS_ADDRESS',
                       'XDG_SESSION_DESKTOP', 'DESKTOP_SESSION', 'SWAYSOCK',
                       'HYPRLAND_INSTANCE_SIGNATURE', 'NIRI_SOCKET')


def manager_environment():
    result = subprocess.run(['systemctl', '--user', 'show-environment'],
                            capture_output=True, text=True, timeout=3, check=True)
    return {key: value for line in result.stdout.splitlines() if '=' in line
            for key, value in [line.split('=', 1)] if key in DISPLAY_ENVIRONMENT}


def _socket_ready(path, *, allow_root=False):
    try:
        info = path.stat()
        owners=(os.getuid(),0) if allow_root else (os.getuid(),)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid not in owners:return False
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(.2);peer.connect(str(path))
        return True
    except OSError:return False


def wait_for_graphical_session(environment=None, *, timeout=60,
                               manager_environment=manager_environment,
                               clock=time.monotonic, sleep=time.sleep):
    environment = dict(os.environ if environment is None else environment)
    deadline = clock() + timeout
    while True:
        try:environment.update(manager_environment())
        except (OSError, subprocess.SubprocessError):pass
        wayland = environment.get('WAYLAND_DISPLAY', '')
        runtime = environment.get('XDG_RUNTIME_DIR', '')
        if wayland and runtime:
            path = Path(wayland) if wayland.startswith('/') else Path(runtime)/wayland
            if _socket_ready(path):
                environment['QT_QPA_PLATFORM']='wayland'
                return environment
        display = re.fullmatch(r':(\d+)(?:\.\d+)?', environment.get('DISPLAY',''))
        if environment.get('XDG_SESSION_TYPE') != 'wayland' and display:
            if _socket_ready(Path('/tmp/.X11-unix')/('X'+display[1]),allow_root=True):
                environment['QT_QPA_PLATFORM']='xcb'
                return environment
        if clock() >= deadline:
            raise RuntimeError('Monitorize is waiting for a usable graphical session. Check the user manager display environment.')
        sleep(min(1, deadline-clock()))


def require_local_graphical_session(*, host=()):
    """Verify the selected logind display before honoring user-manager display sockets."""
    def query(*arguments):
        result = subprocess.run([*host, 'loginctl', *arguments],
                                capture_output=True, text=True, timeout=3, check=True)
        return result.stdout.strip()

    try:
        uid = str(os.getuid())
        session = query('show-user', uid, '--property=Display', '--value')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', session):
            raise ValueError()
        properties = ('Active', 'Remote', 'Type', 'Class', 'Seat', 'User')
        output = query('show-session', session, *(f'--property={name}' for name in properties))
        values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if (values.get('User') == uid and values.get('Active') == 'yes'
                and values.get('Remote') == 'no' and values.get('Seat')
                and values.get('Type') in ('wayland', 'x11')
                and values.get('Class') in ('user', 'user-early', 'user-incomplete')):
            return
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    raise RuntimeError('No active local graphical session belongs to this user. Use boot remote login before desktop login.')


def main():
    host_controller = '--host-controller' in sys.argv
    try:
        environment = None
        if host_controller:
            require_local_graphical_session()
            environment = {key: value for key, value in os.environ.items()
                           if key not in DISPLAY_ENVIRONMENT or key == 'XDG_RUNTIME_DIR'}
        environment = wait_for_graphical_session(environment, timeout=8 if host_controller else 60)
    except RuntimeError as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    flags = ['--start-in-tray']
    if host_controller:
        flags.append('--host-controller')
    os.execvpe(sys.executable, [sys.executable, '-m', 'monitorize', *flags], environment)


if __name__ == '__main__':
    raise SystemExit(main())
