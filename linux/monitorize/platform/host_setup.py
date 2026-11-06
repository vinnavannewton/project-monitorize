"""Terminal client for installed Monitorize authorization and setup helpers."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import subprocess

from monitorize.platform import firewall_scope, system_setup
from monitorize.platform.polkit_agent import agent_missing
from monitorize.platform.remote_login_host import _constant, _read


_PKEXEC = Path('/usr/bin/pkexec')
_TERMINAL_ENTRY = Path('/usr/libexec/monitorize/remote-login/entry.py')
_POLICY = Path('/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy')
_BOOT_ACTIONS = {
    'enable', 'enable-next-boot', 'disable', 'recover', 'recover-disable',
    'login-virtual-on', 'login-virtual-off', 'timeout',
}


def _failure(message, **details):
    return {'success': False, 'message': message, **details}


def _host_only():
    if Path('/.flatpak-info').is_file():
        return _failure('Run this administrator action with Monitorize installed on the host. The Flatpak cannot access the host setup helpers.')
    if os.getuid() == 0:
        return _failure('Run Monitorize as the account that will stream and authorize the administrator action when prompted.')
    return None


def _installed_setup():
    return _read(system_setup.HELPER, Path('/'), 0).decode()


def _terminal_protocol():
    try:
        return (os.access(_TERMINAL_ENTRY, os.X_OK)
                and _constant(_installed_setup(), 'TERMINAL_SETUP_PROTOCOL') == 1
                and _constant(_read(_TERMINAL_ENTRY, Path('/'), 0).decode(), 'TERMINAL_SETUP_PROTOCOL') == 1
                and b'io.github.vinnavannewton.monitorize.terminal-setup' in _read(_POLICY, Path('/'), 0))
    except (OSError, ValueError, SyntaxError):
        return False


def _run_installed(helper, arguments, *, request_id=None, terminal=False):
    error = _host_only()
    if error:
        return error
    try:
        _read(helper, Path('/'), 0)
    except (OSError, ValueError):
        return _failure('Install the matching trusted Monitorize host setup helper before running this action.')
    if not os.access(_PKEXEC, os.X_OK):
        return _failure('Install Polkit (pkexec) on the host before running this action.')
    if terminal and not _terminal_protocol():
        return _failure('Install the Monitorize host helper and Polkit policy with terminal setup support.')
    # pkexec registers a textual agent on the controlling TTY when the SSH
    # session has no agent. Helper output remains a separate JSON receipt.
    if terminal:
        command = [str(_PKEXEC), str(_TERMINAL_ENTRY), '--terminal', *arguments]
    else:
        command = [str(_PKEXEC), str(helper), *arguments]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except KeyboardInterrupt:
        return _failure('Setup was interrupted. Refresh setup status before retrying.', outcome_unknown=True)
    except OSError as exc:
        return _failure(f'Could not start the administrator setup helper: {exc}')
    if result.returncode == 127 and not result.stdout.strip():
        if agent_missing(result.stderr):
            return _failure('Could not open terminal authentication. Connect with an SSH TTY (ssh -t) and retry.')
        return _failure('Administrator authorization failed. Check the host Polkit policy and try again.')
    return system_setup.parse_system_setup_result(
        result.returncode, result.stdout, result.stderr, request_id,
    )


def _setup_arguments(command, error):
    if error:
        return None, error
    # Existing GUI builders may offer a source bootstrap. The terminal client
    # only launches the trusted installed helper, never an interpreter/checkout.
    if str(system_setup.HELPER) not in command:
        return None, _failure('Install or update the trusted Monitorize setup helper on the host first.')
    return command[command.index(str(system_setup.HELPER)) + 1:], None


def setup_status():
    from monitorize.platform.sunshine_service import get_sunshine_kms_setup_status
    from monitorize.platform.vkms_management import verify

    blocked = _host_only()
    status = system_setup.get_system_setup_status()
    if blocked:
        status.update(available=False, message=blocked['message'])
    else:
        try:
            trusted = _constant(_installed_setup(), 'FIRST_RUN_SETUP_PROTOCOL') == 2
        except (OSError, ValueError, SyntaxError):
            trusted = False
        if not trusted:
            status.update(available=False, message='Install the matching trusted Monitorize host setup helper first.')
    return {**status, 'firewall': firewall_scope.discover(),
            'kms': get_sunshine_kms_setup_status(), 'vkms': verify(),
            'terminal_authorization': not blocked and _terminal_protocol()}


def apply_setup(*, input_access=False, firewall=False, firewall_zones=None,
                vkms=False, kms_binary=None):
    blocked = _host_only()
    if blocked:
        return blocked
    if firewall_zones is not None and not firewall:
        return _failure('Select firewall setup before supplying firewall zones.')
    selected = input_access or firewall or vkms
    if not selected and not kms_binary:
        return _failure('Choose input, firewall, VKMS or KMS setup.')

    actions = {}
    response = {}
    if selected:
        scope = firewall_scope.discover() if firewall else None
        if scope and not scope['available']:
            return _failure(scope['message'] or 'Could not verify firewall networks.')
        try:
            zones = firewall_scope.choose_zones(scope, firewall_zones) if scope else None
        except (RuntimeError, ValueError) as exc:
            return _failure(str(exc))
        arguments, error = _setup_arguments(*system_setup.system_setup_command(
            input_access, firewall, vkms, firewall_zones=zones,
            firewall_context=scope['fingerprint'] if scope else None,
        ))
        if error:
            return error
        request_id = arguments[arguments.index('--request-id') + 1]
        response = _run_installed(system_setup.HELPER, arguments, request_id=request_id)
        actions.update(response.get('actions', {}))
        if not response.get('success'):
            return response
    if kms_binary:
        if kms_binary is True:
            from monitorize.platform.sunshine_service import find_sunshine_command
            command = find_sunshine_command()
            if not command:
                kms = _failure('Install the bundled Monitorize Sunshine engine first.')
                return {**response, **kms, 'partial': bool(actions), 'actions': actions}
            kms_binary = command[0]
        arguments, error = _setup_arguments(*system_setup.kms_setup_command(kms_binary))
        kms = error or _run_installed(system_setup.HELPER, arguments, terminal=True)
        partial = bool(actions) and not kms.get('success')
        actions['kms'] = kms
        response.update(kms, actions=actions, partial=partial)
    if response.get('success') and (input_access or firewall):
        from monitorize.config.settings import save_general_settings
        save_general_settings(system_setup_decided=True)
    return response


def apply_vkms(enabled):
    return _run_installed(system_setup.VKMS_AUTHORIZATION_HELPER,
                          ['enable' if enabled else 'disable'])


def _system_bus():
    import dbus
    from dbus.mainloop import NULL_MAIN_LOOP
    return dbus.SystemBus(private=True, mainloop=NULL_MAIN_LOOP)


def _remote_runtime():
    from monitorize.remote_login.dbus_service import INTERFACE, NAME, PATH

    bus = _system_bus()
    try:
        owner = bus.get_name_owner(NAME)
        proxy = bus.get_object(owner, PATH, introspect=False)
        payload = json.loads(proxy.get_dbus_method('GetStatus', INTERFACE)(timeout=5))
        if not isinstance(payload, dict) or payload.get('uid') != os.getuid():
            raise ValueError('Remote login belongs to another local account.')
        return payload
    finally:
        bus.close()


def boot_status():
    blocked = _host_only()
    status = system_setup.remote_login_setup_status()
    if blocked:
        status.update(available=False, live_available=False, message=blocked['message'])
    status['terminal_authorization'] = not blocked and _terminal_protocol()
    if status.get('enabled') and not blocked:
        try:
            status['runtime'] = _remote_runtime()
        except Exception:
            status['runtime_message'] = 'The enrolled remote-login coordinator is not reachable.'
    return status


def _has_graphical_session():
    from monitorize.remote_login.dbus_service import SystemTransport
    from monitorize.remote_login.logind import LogindSessions

    bus = _system_bus()
    try:
        snapshot = LogindSessions(SystemTransport(bus)).snapshot()
        return any(session['uid'] == os.getuid() and session['active']
                   and not session['remote'] and session['type'] in ('x11', 'wayland')
                   for session in snapshot['sessions'])
    finally:
        bus.close()


def _boot_request(action, seconds=None):
    options = ['--remote-login', action]
    if seconds is not None:
        options.extend(['--display-idle-timeout', str(seconds)])
    result = _run_installed(system_setup.HELPER, options, terminal=True)
    if result.get('success') and result.get('action') != action:
        return _failure('The setup helper did not confirm the requested startup action. Refresh boot status.',
                        outcome_unknown=True)
    return result


def _prepare_boot_controller():
    from monitorize.desktop.remote_login_controller import (
        configure, controller_snapshot, restore_controller_snapshot,
    )
    from monitorize.platform.sunshine_service import (
        ensure_sunshine_tray_disabled, get_sunshine_config_path,
    )

    if not Path(get_sunshine_config_path(1)).is_file():
        ensure_sunshine_tray_disabled(1)
    snapshot = controller_snapshot()
    try:
        configure(True)
    except Exception:
        restore_controller_snapshot(snapshot)
        raise
    return snapshot


def apply_boot(action, *, seconds=None):
    blocked = _host_only()
    if blocked:
        return blocked
    if action not in _BOOT_ACTIONS:
        return _failure('Unsupported remote-login setup action.')
    if action == 'timeout':
        from monitorize.remote_login.settings import validate_idle_timeout
        try:
            seconds = validate_idle_timeout(seconds)
        except ValueError as exc:
            return _failure(str(exc))
    elif seconds is not None:
        return _failure('A timeout value is valid only for the boot timeout action.')
    if not _terminal_protocol():
        return _failure('Install the Monitorize host helper and Polkit policy with terminal setup support.')

    enabling = action in ('enable', 'enable-next-boot')
    snapshot = None
    if enabling:
        status = system_setup.remote_login_setup_status()
        if not status['available'] or not status['supported']:
            return _failure(status['message'])
        if action == 'enable':
            try:
                live = _has_graphical_session()
            except Exception:
                return _failure('Could not verify the current desktop session. Use next-boot enrollment if appropriate.')
            if live and not status['live_available']:
                return _failure(status['live_message'])
            action = 'enable-live' if live else 'enable'
        else:
            action = 'enable'
        try:
            snapshot = _prepare_boot_controller()
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return _failure(f'Could not prepare the desktop controller: {exc}')

    result = _boot_request(action, seconds)
    if enabling:
        if not result.get('success') and not result.get('configured') and not result.get('outcome_unknown'):
            if not system_setup.remote_login_setup_status().get('enabled'):
                from monitorize.desktop.remote_login_controller import restore_controller_snapshot
                try:
                    restore_controller_snapshot(snapshot)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    return {**result, 'partial': True, 'message': f"{result['message']} Controller restoration failed: {exc}"}
        if result.get('success'):
            result.update(configured=True, controller_configured=True)
    elif action == 'timeout' and result.get('success'):
        from monitorize.config.settings import save_display_idle_timeout
        try:
            save_display_idle_timeout(seconds)
        except (OSError, ValueError) as exc:
            return {**result, 'success': False, 'partial': True,
                    'message': f'Boot timeout was saved. The desktop timeout could not be saved: {exc}'}
    elif action in ('disable', 'recover-disable') and result.get('success'):
        from monitorize.desktop.remote_login_controller import configure
        try:
            configure(False)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return {**result, 'success': False, 'partial': True,
                    'message': f'Boot startup was disabled. Desktop startup restoration failed: {exc}'}
    return result


def recover_setup(action):
    blocked = _host_only()
    if blocked:
        return blocked
    if action == 'repair-boot':
        from monitorize.platform.remote_login_repair import repair_remote_login
        return repair_remote_login(terminal=True)
    if action == 'boot-controller':
        from monitorize.desktop.remote_login_controller import restore_managed
        try:
            enabled = system_setup.remote_login_setup_status()['enabled']
            return restore_managed(enabled)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return _failure(f'Could not restore the managed desktop controller: {exc}')
    options = {
        'network': '--recover-networkmanager',
        'vkms': '--recover-host',
        'restart-vkms': '--restart-host',
    }
    if action not in options:
        return _failure('Unsupported system recovery action.')
    return _run_installed(system_setup.HELPER, [options[action]], terminal=True)


@contextmanager
def _terminal_agent():
    """Register an agent only for this CLI's direct NetworkManager request."""
    with open('/dev/tty', 'r+b', buffering=0) as terminal:
        read_fd, write_fd = os.pipe()
        process = None
        try:
            try:
                process = subprocess.Popen(
                    ['/usr/bin/pkttyagent', '--fallback', '--process', str(os.getpid()),
                     '--notify-fd', str(write_fd)],
                    stdin=terminal, stdout=terminal, stderr=terminal, pass_fds=(write_fd,),
                )
            finally:
                os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready or os.read(read_fd, 1) or process.poll() is not None:
                raise RuntimeError('Could not register terminal authentication with Polkit.')
            yield
        finally:
            os.close(read_fd)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def boot_wifi_status():
    from monitorize.platform.networkmanager_boot_wifi import WifiOperations

    blocked = _host_only()
    if blocked:
        return blocked
    return WifiOperations().discover()


def apply_boot_wifi(uuid, version, password):
    from monitorize.platform.networkmanager_boot_wifi import WifiOperations

    blocked = _host_only()
    if blocked:
        return blocked
    try:
        with _terminal_agent():
            result = WifiOperations().configure(uuid, version, password)
    except KeyboardInterrupt:
        return _failure('Wi-Fi setup was interrupted. Refresh profile status before retrying.', outcome_unknown=True)
    except (OSError, RuntimeError):
        return _failure('Terminal authentication is unavailable. Install Polkit and connect with an SSH TTY (ssh -t).')
    if result.get('code') == 'authorization':
        result['message'] = 'NetworkManager authorization was denied or cancelled in the terminal.'
    return result
