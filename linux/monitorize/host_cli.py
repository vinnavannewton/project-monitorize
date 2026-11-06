"""Terminal interface for the Monitorize host."""
import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
import warnings


def _json_option(parser):
    parser.add_argument('--json', action='store_true', help='write a machine-readable result')


def _parser():
    parser = argparse.ArgumentParser(prog='monitorize', description='Configure and control the Monitorize host.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name, description in (
        ('start', 'start hosting in the selected session'),
        ('stop', 'stop hosting'),
        ('reload', 'validate and apply monitorize.json'),
        ('status', 'report hosting and configuration state'),
    ):
        command = commands.add_parser(name, help=description)
        _json_option(command)
        if name == 'reload':
            command.add_argument('--restart', action='store_true', help='allow a restart that disconnects clients')

    config = commands.add_parser('config', help='locate or initialize monitorize.json')
    config.add_argument('action', choices=('path', 'init'))

    setup = commands.add_parser('setup', help='initialize settings and configure host permissions')
    _json_option(setup)
    setup.add_argument('--status', action='store_true', help='inspect host setup')
    setup.add_argument('--input', action='store_true', help='authorize input access')
    setup.add_argument('--firewall', action='store_true', help='authorize Monitorize traffic')
    setup.add_argument('--zone', action='append', metavar='ZONE', help='explicit firewalld zone to authorize')
    setup.add_argument('--vkms', action='store_true', help='authorize passwordless VKMS creation')
    setup.add_argument('--kms', action='store_true', help='authorize KMS capture for the bundled engine')

    boot = commands.add_parser('boot', help='configure or control login-screen hosting')
    _json_option(boot)
    boot.add_argument('action', choices=(
        'status', 'enable', 'enable-next-boot', 'disable', 'start', 'stop',
        'login-virtual-on', 'login-virtual-off', 'timeout', 'recover', 'recover-disable',
    ))
    boot.add_argument('--seconds', type=int, help='display idle timeout in seconds')

    wifi = commands.add_parser('boot-wifi', help='configure a saved Wi-Fi profile for pre-login use')
    _json_option(wifi)
    wifi.add_argument('action', choices=('status', 'configure'))
    wifi.add_argument('--profile', metavar='UUID', help='choose the saved network from boot-wifi status')
    wifi.add_argument('--password-stdin', action='store_true', help='read the Wi-Fi password from standard input')

    recovery = commands.add_parser('recover', help='recover a host dependency')
    _json_option(recovery)
    recovery.add_argument('target', choices=('network', 'vkms', 'restart-vkms', 'repair-boot', 'boot-controller'))

    vkms = commands.add_parser('vkms', help='manage Monitorize passwordless display authorization')
    _json_option(vkms)
    vkms.add_argument('action', choices=('enable', 'disable'))

    preset = commands.add_parser('preset', help='list or launch saved display presets')
    _json_option(preset)
    preset.add_argument('action', choices=('list', 'launch'))
    preset.add_argument('name', nargs='?', help='saved preset name')

    pair = commands.add_parser('pair', help='approve a pending Moonlight pairing request')
    _json_option(pair)
    pair.add_argument('--instance', type=int, choices=(1, 2), help='choose the Monitorize display')
    pair.add_argument('--request', metavar='ID', help='select a request ID shown by --list')
    mode = pair.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='show pending requests without submitting a PIN')
    mode.add_argument('--pin-stdin', action='store_true', help='read the PIN from standard input')
    return parser


def _boot_context():
    """Read service ownership without a graphical session or admin authorization."""
    from monitorize.remote_login.settings import SETTINGS, read_settings
    if not SETTINGS.exists():
        return None
    try:
        settings = read_settings()
    except ValueError:
        return None
    if not settings.enabled or settings.uid != os.getuid():
        return None
    return settings


def _boot_rpc(method, *args):
    try:
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        from monitorize.remote_login.dbus_service import NAME, PATH, INTERFACE
    except ImportError as exc:
        raise RuntimeError('The system D-Bus bindings required for remote-login control are unavailable.') from exc

    try:
        bus = dbus.SystemBus(private=True, mainloop=NULL_MAIN_LOOP)
        try:
            owner = bus.get_name_owner(NAME)
            if bus.get_unix_user(owner) != 0:
                raise RuntimeError('The remote-login service is not running under its trusted system account.')
            proxy = bus.get_object(owner, PATH, introspect=False)
            call = proxy.get_dbus_method(method, INTERFACE)
            result = json.loads(str(call(*args, timeout=10)))
        finally:
            bus.close()
    except dbus.DBusException as exc:
        raise RuntimeError('Remote-login control is unavailable. Check monitorize boot status and the installed host service.') from exc
    if not isinstance(result, dict) or result.get('uid') != os.getuid():
        raise RuntimeError('The running remote-login service does not match this account.')
    return result


def _boot_runtime(action):
    from monitorize.platform.sunshine_ports import get_sunshine_port
    from monitorize.platform.utils import get_local_ip

    if not _boot_context():
        raise RuntimeError('Enable remote login for this account with monitorize boot enable.')
    if action == 'status':
        status = _boot_rpc('GetStatus')
    else:
        status = _boot_rpc('SetBootPaused', action == 'stop')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if action == 'start' and (status.get('boot_listening') or status.get('startup_error')):
                break
            if action == 'stop' and status.get('state') == 'greeter_stopped':
                break
            if status.get('session') or status.get('state') == 'error':
                break
            time.sleep(.2)
            status = _boot_rpc('GetStatus')
        if action == 'start' and not status.get('boot_listening'):
            raise RuntimeError(status.get('startup_error') or 'The login-screen listener did not become ready. Check monitorize boot status.')
        if action == 'stop' and status.get('state') != 'greeter_stopped':
            raise RuntimeError(status.get('startup_error') or 'The login-screen listener has not stopped. Check monitorize boot status.')

    listening = status.get('boot_listening') is True
    return {
        **status,
        'controller': 'boot',
        'remote_login': True,
        'instances': [1] if listening else [],
        'listeners': [{'instance': 1, 'address': get_local_ip(), 'port': get_sunshine_port(1)}] if listening else [],
    }


def _active_boot_status():
    if not _boot_context():
        return None
    try:
        status = _boot_runtime('status')
    except RuntimeError:
        return None
    if not status.get('session'):
        return status
    return None


def _control(action, *, restart=False):
    from monitorize.desktop.host_control import request

    if action in ('start', 'stop', 'status'):
        boot = _active_boot_status()
        if boot is not None:
            return boot if action == 'status' else _boot_runtime(action)
    return request(action, restart=restart)


def _read_secret(from_stdin, *, prompt, limit, stdin_option):
    if from_stdin:
        value = sys.stdin.readline(limit + 2).removesuffix('\n').removesuffix('\r')
    elif sys.stdin.isatty():
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                value = getpass.getpass(prompt)
            except getpass.GetPassWarning as exc:
                raise ValueError(f'Cannot hide terminal input. Supply the secret with {stdin_option}.') from exc
    else:
        raise ValueError(f'Use an SSH terminal for the prompt, or supply the secret with {stdin_option}.')
    if not value or len(value) > limit:
        raise ValueError('The supplied value is missing or too long.')
    return value


def _pair(args):
    from monitorize.platform.pairing_api import resolve_hosts, valid_id

    if args.request and not valid_id(args.request):
        raise ValueError('Invalid pairing request ID. Use monitorize pair --list.')
    status = _control('status')
    instances = [i for i in status.get('instances', []) if i in (1, 2)]
    if args.instance is not None:
        instances = [i for i in instances if i == args.instance]
    if not instances:
        raise RuntimeError('No selected Monitorize listener is ready. Check monitorize status.')
    remote = status.get('remote_login') is True
    requests = []
    try:
        for host in resolve_hosts(instances, remote, external=True):
            requests.extend((host, row) for row in host.pending())
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError('The selected Sunshine pairing API is unavailable. Check monitorize status.') from exc
    if args.list:
        return {'pairings': [row for _, row in requests]}
    if args.request:
        requests = [(host, row) for host, row in requests if row['id'] == args.request]
    if len(requests) != 1:
        message = ('Multiple clients are waiting. Use monitorize pair --list, then --request ID.'
                   if requests else 'No matching client is waiting. Request a PIN in Moonlight and try again.')
        raise ValueError(message)

    host, row = requests[0]
    pin = _read_secret(args.pin_stdin, prompt='Moonlight PIN: ', limit=4, stdin_option='--pin-stdin')
    if not re.fullmatch(r'[0-9]{4}', pin):
        raise ValueError('Enter the four-digit PIN shown in Moonlight.')
    # Recheck the active host and certificate after the operator enters the PIN.
    current = _control('status')
    if (host.instance not in current.get('instances', []) or
            (current.get('remote_login') is True) != remote or
            host not in resolve_hosts([host.instance], remote, external=True)):
        raise RuntimeError('Sunshine stopped or changed identity. Request a new PIN in Moonlight.')
    try:
        success, message = host.approve(row['id'], pin, row['name'])
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError('Could not finish pairing. Request a new PIN in Moonlight and retry.') from exc
    return {'success': success, 'message': message}


def _setup(args):
    from monitorize.config.host_config import initialize
    from monitorize.platform.host_setup import apply_setup, setup_status

    selected = args.input or args.firewall or args.vkms or args.kms
    if args.zone and not args.firewall:
        raise ValueError('--zone requires --firewall.')
    if args.status:
        if selected:
            raise ValueError('--status cannot be combined with setup changes.')
        return setup_status()

    path = initialize()
    if not selected:
        return {
            **setup_status(), 'config': str(path),
            'message': ('Configuration initialized. Select --input, --firewall, --vkms or --kms '
                        'to authorize the corresponding host changes. Use monitorize setup --help for options.'),
        }
    return {**apply_setup(input_access=args.input, firewall=args.firewall, firewall_zones=args.zone,
                          vkms=args.vkms, kms_binary=True if args.kms else None), 'config': str(path)}


def _boot_wifi(args):
    from monitorize.platform.host_setup import apply_boot_wifi, boot_wifi_status

    if args.action == 'status' and (args.profile or args.password_stdin):
        raise ValueError('Profile and password options require boot-wifi configure.')
    status = boot_wifi_status()
    if args.action == 'status' or status.get('success') is False:
        return status
    if not args.profile:
        raise ValueError('Select a profile with --profile UUID. Use monitorize boot-wifi status to list profiles.')
    profile = next((row for row in status.get('profiles', []) if row['uuid'] == args.profile), None)
    if not profile or not profile['eligible']:
        raise ValueError('Select an eligible saved Wi-Fi profile from monitorize boot-wifi status.')
    password = _read_secret(args.password_stdin, prompt='Wi-Fi password: ', limit=64,
                            stdin_option='--password-stdin')
    return apply_boot_wifi(profile['uuid'], profile['version'], password)


def _dispatch(args):
    if args.command == 'config':
        from monitorize.config.host_config import initialize
        return initialize()
    if args.command == 'setup':
        return _setup(args)
    if args.command == 'boot':
        from monitorize.platform.host_setup import apply_boot, boot_status
        if (args.action == 'timeout') != (args.seconds is not None):
            raise ValueError('Use --seconds SECONDS with boot timeout only.')
        if args.action == 'status':
            return boot_status()
        if args.action in ('start', 'stop'):
            return _boot_runtime(args.action)
        return apply_boot(args.action, seconds=args.seconds)
    if args.command == 'boot-wifi':
        return _boot_wifi(args)
    if args.command == 'recover':
        from monitorize.platform.host_setup import recover_setup
        return recover_setup(args.target)
    if args.command == 'vkms':
        from monitorize.platform.host_setup import apply_vkms
        return apply_vkms(args.action == 'enable')
    if args.command == 'preset':
        if (args.action == 'launch') != (args.name is not None):
            raise ValueError('Provide a preset name with preset launch.')
        if args.action == 'list':
            from monitorize.config.settings import load_presets
            return {'presets': [preset['name'] for preset in load_presets()]}
        from monitorize.desktop.host_control import request
        return request('preset', preset=args.name)
    if args.command == 'pair':
        return _pair(args)
    return _control(args.command, restart=getattr(args, 'restart', False))


def _print_result(result, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if not isinstance(result, dict):
        print(result)
        return
    if result.get('message'):
        print(result['message'])
    if 'profiles' in result:
        print(result.get('status', {}).get('message', 'Saved Wi-Fi profiles'))
        for row in result['profiles']:
            print(f"{row['uuid']}  {row['name']}  {row['reason'] or row['configuration']}")
        return
    if 'presets' in result:
        print('\n'.join(result['presets']) if result['presets'] else 'No display presets are saved.')
        return
    if 'pairings' in result:
        for row in result['pairings']:
            print(f"Display {row['instance']}  {row['id']}  {row['name']}  {row['address']}")
        if not result['pairings']:
            print('No clients are waiting to pair.')
        return
    for key in ('state', 'build', 'profile', 'config', 'saved_revision', 'running_revision', 'restart_pending', 'active_preset', 'startup_error'):
        if result.get(key) is not None and result[key] != '':
            print(f"{key.replace('_', ' ').capitalize()}  {result[key]}")
    for key in ('input_message', 'live_message'):
        if result.get(key) and result[key] != result.get('message'):
            print(result[key])
    for name, action in result.get('actions', {}).items():
        if isinstance(action, dict) and action.get('message') and action['message'] != result.get('message'):
            print(f"{name.upper()}  {action['message']}")
    for component in ('kms', 'vkms'):
        details = result.get(component)
        if isinstance(details, dict) and details.get('message'):
            print(f"{component.upper()}  {details['message']}")
    if 'terminal_authorization' in result:
        print(f"Terminal authorization  {'available' if result['terminal_authorization'] else 'unavailable'}")
    if isinstance(result.get('firewall'), dict):
        firewall = result['firewall']
        zones = [zone['name'] for zone in firewall.get('zones', [])]
        if zones:
            print('Firewalld zones  ' + ', '.join(zones))
        elif firewall.get('message'):
            print(firewall['message'])
    for listener in result.get('listeners', []):
        print(f"Display {listener['instance']}  {listener['address']}  port {listener['port']}")
    if result.get('runtime_message'):
        print(result['runtime_message'])
    if result.get('runtime'):
        runtime = result['runtime']
        print(f"Remote-login state  {runtime.get('state', 'unknown')}")
    if not any(key in result for key in ('message', 'state', 'pairings', 'runtime', 'input_message')):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv):
    args = _parser().parse_args(argv)
    as_json = getattr(args, 'json', False)
    try:
        result = _dispatch(args)
    except (EOFError, KeyboardInterrupt):
        print('Monitorize operation cancelled.', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        message = str(error)
        if as_json:
            _print_result({'success': False, 'message': message}, True)
        else:
            print(f'Monitorize: {message}', file=sys.stderr)
        return 1

    _print_result(str(result) if isinstance(result, Path) else result, as_json)
    return 1 if isinstance(result, dict) and result.get('success') is False else 0
