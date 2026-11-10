"""Read-only output selection for a verified Xorg greeter."""
import os
from pathlib import Path
import pwd
import re
import socket
import struct
import subprocess

from .login_primary import validate_target, verify_drm


def select_output(report, *, name=None):
    if len(report) > 65536:
        raise ValueError('X11 greeter output report is too large.')
    outputs = []
    current = None
    for line in report.splitlines():
        if line and not line[0].isspace():
            match = re.match(r'^([\w-]+) connected (primary )?(\d+)x(\d+)[+-]\d+[+-]\d+(?: |$)', line)
            current = None
            if match:
                output_name, primary, width, height = match.groups()
                current = (bool(primary), output_name, int(width), int(height), [], [])
                outputs.append(current)
        elif current:
            rates = re.findall(r'(\d+(?:\.\d+)?)\*[+ ]?(?:\s|$)', line)
            current[4].extend(rates)
            connector = re.fullmatch(r'\s+CONNECTOR_ID:\s+([1-9][0-9]*)\s*', line)
            if connector:
                current[5].append(int(connector[1]))
    primary = [output for output in outputs if output[0]]
    candidates = [output for output in outputs if output[1] == name] if name else primary or outputs
    if len(candidates) != 1 or len(candidates[0][4]) != 1 or len(candidates[0][5]) > 1:
        if name:
            raise ValueError('The selected Mirror output has no unique active X11 mode.')
        raise ValueError('X11 greeter has no uniquely identifiable active primary display.')
    _, name, width, height, rates, connector_ids = candidates[0]
    target = validate_target(dict(output_name=name, capture='kms', width=width,
                                  height=height, fps=float(rates[0])))
    if connector_ids:
        target['connector_id'] = connector_ids[0]
    return target


def greeter_environment(context):
    from .daemon import require_installed
    from .display_managers import require_manager

    manager = require_manager(context.get('manager', 'sddm.service'))
    greeters = {
        'sddm.service': ('sddm', ('sddm-greeter', 'sddm-greeter-qt6')),
        'lightdm.service': ('lightdm', ('slick-greeter',)),
    }
    if manager['display_server'] != 'x11' or manager['manager'] not in greeters:
        raise PermissionError('A verified local X11 greeter is required.')
    service_unit = manager['service_unit']
    account_name, executables = greeters[manager['manager']]
    uid = context['greeter_uid']
    session = context['session']
    account = pwd.getpwuid(uid)
    if (context.get('session_type') != 'x11' or uid == 0 or account.pw_name != account_name
            or not re.fullmatch(r'[A-Za-z0-9]+', session)):
        raise PermissionError('A verified local X11 greeter is required.')
    scope = 'session-' + session + '.scope'
    matches = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            executable = (process / 'exe').resolve(strict=True)
            if executable.name not in executables:
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
        raise PermissionError('Could not identify the X11 display of the active greeter.')
    pid, display, authority = matches[0]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect('/tmp/.X11-unix/X' + display[1:])
        server_pid, server_uid, _ = struct.unpack('3i', client.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    server = Path('/proc') / str(server_pid)
    executable = (server / 'exe').resolve(strict=True)
    is_xorg = executable.name == 'Xorg' or (
        service_unit == 'display-manager-legacy.service' and executable == Path('/usr/bin/Xorg.bin')
    )
    if (server_uid != 0 or not is_xorg
            or not any(service_unit in line.split(':', 2)[-1].split('/')
                       for line in (server / 'cgroup').read_text().splitlines())):
        raise PermissionError('The greeter X11 socket does not belong to the display manager\'s Xorg server.')
    require_installed(executable)
    # Xauthority is opened only by the child as the unprivileged greeter account.
    return (pid, server_pid), dict(DISPLAY=display, XAUTHORITY=authority,
                                  HOME=account.pw_dir, PATH='/usr/bin:/bin', LC_ALL='C')


def desktop_mirror_target(uid, session, descriptor, session_environment, check):
    """Resolve the requested output in the active Cinnamon X11 session."""
    from .daemon import require_installed
    from .session_context import desktop_context

    def environment():
        active, values = session_environment(uid)
        context = desktop_context(values)
        if (active != session or context.get('session_type') != 'x11'
                or context['display'] != descriptor['display']):
            raise PermissionError('The Cinnamon X11 session changed.')
        return values

    check()
    values = environment()
    account = pwd.getpwuid(uid)
    env = dict(DISPLAY=descriptor['display'], HOME=account.pw_dir, PATH='/usr/bin:/bin', LC_ALL='C')
    if values.get('XAUTHORITY'):
        env['XAUTHORITY'] = values['XAUTHORITY']
    executable = '/usr/bin/xrandr'
    require_installed(executable)
    result = subprocess.run([executable, '--query', '--prop'], env=env,
                            user=uid, group=account.pw_gid, extra_groups=[],
                            capture_output=True, text=True, timeout=3, check=True)
    target = select_output(result.stdout, name=descriptor['mirror_output'])
    connector = verify_drm(target)
    name = connector.name.split('-', 1)[1]
    if name == descriptor['output_name']:
        raise PermissionError('Mirror cannot capture the reserved remote-login display.')
    if environment().get('XAUTHORITY') != values.get('XAUTHORITY'):
        raise PermissionError('The Cinnamon X11 session authority changed.')
    check()
    return validate_target(dict(target, output_name=name))


def operation(context, action, check):
    from .daemon import require_installed

    check()
    if action == 'idle':
        return {}
    if action != 'prepare':
        raise ValueError('Unknown X11 greeter capture operation.')
    executable = '/usr/bin/xrandr'
    require_installed(executable)
    identity, environment = greeter_environment(context)
    account = pwd.getpwuid(context['greeter_uid'])

    def read_target():
        result = subprocess.run([executable, '--query', '--prop'], env=environment,
                                user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode:
            raise RuntimeError('Could not query the X11 greeter display as the greeter.')
        return select_output(result.stdout)

    target = read_target()
    connector = verify_drm(target)
    check()
    if (greeter_environment(context) != (identity, environment)
            or read_target() != target or verify_drm(target) != connector):
        raise PermissionError('The X11 greeter or display changed during verification.')
    check()
    return validate_target(dict(target, output_name=connector.name.split('-', 1)[1]))
