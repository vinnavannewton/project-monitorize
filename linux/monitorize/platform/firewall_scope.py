"""Read-only firewalld network scope discovery and change detection."""
import hashlib
import json
import os
import re
import shutil
import subprocess

ZONE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}')
PORTS = ('50084/tcp', '50089/tcp', '50090/tcp', '50110/tcp',
         '51084/tcp', '51089/tcp', '51090/tcp', '51110/tcp',
         '50098-50110/udp', '51098-51110/udp', '5353/udp')


def execute(command):
    return subprocess.run(command, stdin=subprocess.DEVNULL, text=True, capture_output=True,
                          timeout=5, check=False, env={**os.environ, 'LC_ALL': 'C'})


def output(command, run):
    result = run(command)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Could not inspect firewall networks.')
    return result.stdout


def snapshot(executable, run=execute):
    zones = []
    running = False
    if executable:
        state = run([executable, '--state'])
        if state.returncode not in (0, 252):
            raise RuntimeError(state.stderr.strip() or state.stdout.strip() or 'Could not inspect firewalld status.')
        running = state.returncode == 0
        if running:
            active = output([executable, '--get-active-zones'], run)
            for line in active.splitlines():
                if not line.strip():
                    continue
                if not line[0].isspace():
                    name = line.split()[0]
                    if not ZONE.fullmatch(name):
                        raise ValueError('Unsupported firewalld zone name.')
                    zones.append({'name': name, 'interfaces': [], 'sources': [], 'default': False})
                elif zones:
                    field, separator, text = line.strip().partition(':')
                    if separator and field in ('interfaces', 'sources'):
                        zones[-1][field] = sorted(text.split())
            if not zones:
                name = output([executable, '--get-default-zone'], run).strip()
                if not ZONE.fullmatch(name):
                    raise ValueError('Could not identify the default firewalld zone.')
                zones = [{'name': name, 'interfaces': [], 'sources': [], 'default': True}]
    zones.sort(key=lambda entry: entry['name'])
    value = {'running': running, 'zones': zones}
    value['fingerprint'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def choose_zones(scope, selected=None, fingerprint=None):
    if fingerprint and fingerprint != scope['fingerprint']:
        raise RuntimeError('Firewall networks changed during authorization. Refresh network selection and retry.')
    if not scope['running']:
        if selected:
            raise RuntimeError('The selected firewalld networks are no longer available. Refresh setup.')
        return []
    available = {entry['name'] for entry in scope['zones']}
    if selected is None:
        if fingerprint or len(available) != 1:
            raise RuntimeError('Choose the network zones to allow in system setup before authorizing firewall access.')
        return sorted(available)
    if not isinstance(selected, (list, tuple)) or not selected:
        raise ValueError('Select at least one firewall network zone.')
    if (any(not isinstance(name, str) or not ZONE.fullmatch(name) for name in selected)
            or not set(selected).issubset(available)):
        raise ValueError('A selected firewall network is no longer available. Refresh setup.')
    return sorted(set(selected))


def evidence(scope, selected):
    return {'firewalld_running': scope['running'], 'zones': list(selected),
            'observed_zones': [entry['name'] for entry in scope['zones']],
            'fingerprint': scope['fingerprint']}


def assess(scope, saved, executable=None, run=execute):
    """A previous success does not prove access on a newly introduced network."""
    if saved is None:
        return {'needs_attention': False, 'scope_current': None, 'message': ''}
    if (not isinstance(saved, dict) or type(saved.get('firewalld_running')) is not bool
            or not isinstance(saved.get('zones'), list) or not isinstance(saved.get('observed_zones'), list)
            or any(not isinstance(name, str) or not ZONE.fullmatch(name)
                   for name in saved['zones'] + saved['observed_zones'])
            or (saved['firewalld_running'] and not saved['zones'])):
        return {'needs_attention': True, 'scope_current': False,
                'message': 'Firewall access needs verification with the updated system setup.'}
    if saved['firewalld_running'] != scope['running']:
        return {'needs_attention': True, 'scope_current': False,
                'message': 'The active firewall changed. Review network access in system setup.'}
    active = {entry['name'] for entry in scope['zones']}
    introduced = active - set(saved['observed_zones'])
    if introduced:
        return {'needs_attention': True, 'scope_current': False,
                'message': 'New firewall networks need your choice: ' + ', '.join(sorted(introduced)) + '.'}
    if scope['running'] and executable:
        for zone in saved['zones']:
            if not isinstance(zone, str) or not ZONE.fullmatch(zone):
                raise ValueError('Invalid saved firewall network scope.')
            for persistent in (False, True):
                options = ['--permanent'] if persistent else []
                ports = output([executable, *options, '--zone=' + zone, '--list-ports'], run).split()
                if not covers_ports(ports):
                    return {'needs_attention': True, 'scope_current': False,
                            'message': 'Monitorize firewall rules changed in ' + zone + '. Review system setup.'}
    return {'needs_attention': False, 'scope_current': True, 'message': ''}


def covers_ports(ports):
    intervals = {'tcp': [], 'udp': []}
    for port in ports:
        try:
            number, protocol = port.split('/')
            parts = number.split('-')
            low, high = int(parts[0]), int(parts[-1])
            if len(parts) not in (1, 2) or not 1 <= low <= high <= 65535:
                continue
            intervals[protocol].append((low, high))
        except (ValueError, KeyError):
            continue
    for port in PORTS:
        number, protocol = port.split('/')
        parts = number.split('-')
        for value in range(int(parts[0]), int(parts[-1]) + 1):
            if not any(low <= value <= high for low, high in intervals[protocol]):
                return False
    return True


def saved_scope():
    from monitorize.platform.setup_state import read_receipt
    receipt = read_receipt(os.getuid())
    if not receipt:
        return None
    action = {**receipt['history'], **receipt['actions']}.get('firewall')
    if not action:
        return None
    if action.get('state') != 'succeeded':
        return {}
    return action.get('evidence', {})


def discover():
    try:
        executable = shutil.which('firewall-cmd')
        current = snapshot(executable)
        return {**current, **assess(current, saved_scope(), executable), 'available': True}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {'available': False, 'running': False, 'zones': [], 'fingerprint': '',
                'needs_attention': True, 'scope_current': False, 'message': str(exc)}
