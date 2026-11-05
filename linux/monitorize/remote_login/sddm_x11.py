"""Read-only output selection for a verified SDDM Xorg greeter."""
import os
from pathlib import Path
import pwd
import re
import socket
import struct
import subprocess

from .login_primary import validate_target, verify_drm


def select_output(report):
    if len(report) > 65536:
        raise ValueError('SDDM X11 output report is too large.')
    outputs = []
    current = None
    for line in report.splitlines():
        if line and not line[0].isspace():
            match = re.match(r'^([\w-]+) connected (primary )?(\d+)x(\d+)[+-]\d+[+-]\d+(?: |$)', line)
            current = None
            if match:
                name, primary, width, height = match.groups()
                current = (bool(primary), name, int(width), int(height), [])
                outputs.append(current)
        elif current:
            rates = re.findall(r'(\d+(?:\.\d+)?)\*[+ ]?(?:\s|$)', line)
            current[-1].extend(rates)
    primary = [output for output in outputs if output[0]]
    candidates = primary or outputs
    if len(candidates) != 1 or len(candidates[0][-1]) != 1:
        raise ValueError('SDDM X11 has no uniquely identifiable active primary display.')
    _, name, width, height, rates = candidates[0]
    return validate_target(dict(output_name=name, capture='kms', width=width,
                                height=height, fps=float(rates[0])))


def greeter_environment(context):
    from .daemon import require_installed

    uid = context['greeter_uid']
    session = context['session']
    account = pwd.getpwuid(uid)
    if (context.get('session_type') != 'x11' or uid == 0 or account.pw_name != 'sddm'
            or not re.fullmatch(r'[A-Za-z0-9]+', session)):
        raise PermissionError('A verified local SDDM X11 greeter is required.')
    scope = 'session-' + session + '.scope'
    matches = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            executable = (process / 'exe').resolve(strict=True)
            if executable.name not in ('sddm-greeter', 'sddm-greeter-qt6'):
                continue
            if not any(scope in line.split(':', 2)[-1].split('/')
                       for line in (process / 'cgroup').read_text().splitlines()):
                continue
            require_installed(executable)
            with (process / 'environ').open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                continue
            env = dict(item.split(b'=', 1) for item in raw.split(b'\0') if b'=' in item)
            display = env.get(b'DISPLAY', b'').decode('ascii')
            authority = env.get(b'XAUTHORITY', b'').decode('utf-8')
        except (OSError, UnicodeError, ValueError):
            continue
        if re.fullmatch(r':[0-9]{1,5}', display) and Path(authority).is_absolute():
            matches.append((process.name, display, authority))
    if len(matches) != 1:
        raise PermissionError('Could not identify the X11 display of the active SDDM greeter.')
    pid, display, authority = matches[0]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect('/tmp/.X11-unix/X' + display[1:])
        server_pid, server_uid, _ = struct.unpack('3i', client.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    server = Path('/proc') / str(server_pid)
    executable = (server / 'exe').resolve(strict=True)
    if (server_uid != 0 or executable.name != 'Xorg'
            or not any('sddm.service' in line.split(':', 2)[-1].split('/')
                       for line in (server / 'cgroup').read_text().splitlines())):
        raise PermissionError('The greeter X11 socket does not belong to SDDM Xorg.')
    require_installed(executable)
    # Xauthority is opened only by the child as the unprivileged greeter account.
    return (pid, server_pid), dict(DISPLAY=display, XAUTHORITY=authority,
                                  HOME=account.pw_dir, PATH='/usr/bin:/bin', LC_ALL='C')


def operation(context, action, check):
    from .daemon import require_installed

    check()
    if action == 'idle':
        return {}
    if action != 'prepare':
        raise ValueError('Unknown SDDM X11 capture operation.')
    executable = '/usr/bin/xrandr'
    require_installed(executable)
    identity, environment = greeter_environment(context)
    account = pwd.getpwuid(context['greeter_uid'])

    def read_target():
        result = subprocess.run([executable, '--query'], env=environment,
                                user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode:
            raise RuntimeError('Could not query the SDDM X11 display as the greeter.')
        return select_output(result.stdout)

    target = read_target()
    connector = verify_drm(target)
    check()
    if (greeter_environment(context) != (identity, environment)
            or read_target() != target or verify_drm(target) != connector):
        raise PermissionError('The SDDM X11 greeter or display changed during verification.')
    check()
    return validate_target(dict(target, output_name=connector.name.split('-', 1)[1]))
