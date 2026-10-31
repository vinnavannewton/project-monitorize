"""Out-of-process VKMS health and user-owned source update adapter."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time

SOURCE_RECORD = Path('/usr/lib/monitorize-vkms/source-checkout')
VERSION = Path('/usr/lib/monitorize-vkms/VERSION')
REBOOT_RECORD = Path('/var/lib/monitorize-vkms/reboot-required.json')
HOST_ACTIVATION = Path('/usr/share/dbus-1/system-services/io.github.vinnavannewton.MonitorizeVkms1.service')
OFFICIAL_REMOTES = {
    'https://github.com/vinnavannewton/monitorize-vkms',
    'https://github.com/vinnavannewton/monitorize-vkms.git',
    'git@github.com:vinnavannewton/monitorize-vkms.git',
    'ssh://git@github.com/vinnavannewton/monitorize-vkms.git',
}
STATES = {'not_installed', 'working', 'needs_attention', 'restart_required', 'could_not_verify'}


def result_object(stdout):
    try:
        value = json.loads(stdout.strip())
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    raise ValueError('VKMS did not return a valid response.')


def verify():
    """Never use the interactive display/capability APIs for passive checks."""
    installed = None
    try:
        executable = shutil.which('monitorize-vkms', path='/usr/local/bin:/usr/bin:/bin')
        installed = bool(executable or VERSION.parent.exists()
                         or Path('/sys/module/monitorize_vkms').exists())
        if not installed:
            module = subprocess.run(['modinfo', '-n', 'monitorize_vkms'], capture_output=True,
                                    text=True, check=False, timeout=3)
            installed = module.returncode == 0
        if not installed:
            return dict(installed=False, state='not_installed', message='Monitorize VKMS not installed', version='')
        if not executable:
            raise ValueError('The VKMS installation is incomplete: its system CLI is missing.')
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}
        environment['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
        completed = subprocess.run([executable, 'verify', '--json'], capture_output=True,
                                   text=True, check=False, timeout=20, env=environment)
        if completed.returncode == 2 and 'verify' in completed.stderr:
            # Older source installations expose doctor, but not the shared verifier.
            completed = subprocess.run([executable, 'doctor', '--json'], capture_output=True,
                                       text=True, check=False, timeout=10, env=environment)
            legacy = result_object(completed.stdout)
            if type(legacy.get('all_passed')) is not bool or not isinstance(legacy.get('checks'), list):
                raise ValueError('The installed VKMS diagnostics returned an unsupported response.')
            checks = [check for check in legacy['checks'] if check.get('name') not in (
                'Desktop compositor detected', 'Create backend is enabled', 'Live removal backend is enabled')]
            service = subprocess.run(['systemctl', 'show', 'monitorize-vkms.service', '--property=ActiveState', '--value'],
                                     capture_output=True, text=True, check=False, timeout=3, env=environment)
            module = subprocess.run(['modinfo', '-F', 'vermagic', 'monitorize_vkms'],
                                    capture_output=True, text=True, check=False, timeout=3, env=environment)
            service_state = service.stdout.strip()
            ready = service_state == 'active' or (service_state == 'inactive' and HOST_ACTIVATION.is_file())
            checks.extend([
                dict(name='Host service ready', status='PASS' if ready else 'FAIL', detail=service_state),
                dict(name='Running kernel supported', status='PASS' if module.returncode == 0 and module.stdout.split()[:1] == [os.uname().release] else 'FAIL', detail=module.stdout.strip())])
            passed = all(check.get('status') == 'PASS' for check in checks)
            value = dict(installed=True, state='working' if passed else 'needs_attention',
                         checks=checks, message='Verified — working' if passed else 'Needs attention')
        else:
            value = result_object(completed.stdout)
            if value.get('schema_version') != 1 or value.get('state') not in STATES:
                raise ValueError('The installed VKMS verifier returned an unsupported response.')
        # Independent markers win over a stale CLI claiming the component is absent.
        if value.get('installed') is False and installed:
            raise ValueError('VKMS files exist but the installation is incomplete.')
        value['installed'] = True
        value['version'] = VERSION.read_text().strip() if VERSION.is_file() else value.get('version', '')
        if REBOOT_RECORD.is_file():
            marker = json.loads(REBOOT_RECORD.read_text())
            if marker.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip():
                value.update(state='restart_required', message='System restart required', reboot_required=True)
        details = [str(value.get('detail', ''))]
        details.extend(f"{check.get('name', '')}: {check.get('detail', '')}" for check in value.get('checks', [])
                       if check.get('status') != 'PASS')
        value['details'] = '\n'.join(item for item in details if item)
        return value
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return dict(installed=installed, state='could_not_verify', message='Could not verify installation',
                    details=str(error), version='')


def source_checkout():
    try:
        info = SOURCE_RECORD.lstat()
    except FileNotFoundError:
        raise ValueError('VKMS has no recorded source checkout. Rerun its source installer to record the location.') from None
    if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise ValueError('The recorded VKMS source location is unsafe.')
    try:
        checkout = Path(SOURCE_RECORD.read_text().strip()).resolve(strict=True)
    except FileNotFoundError:
        raise ValueError('The recorded VKMS checkout is missing or moved. Restore its location before updating.') from None
    if not checkout.is_dir() or checkout.stat().st_uid != os.getuid():
        raise ValueError('The recorded VKMS checkout must belong to your user.')
    return checkout


def git(checkout, *args, timeout=15):
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes'}
    result = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True,
                            text=True, check=False, timeout=timeout, env=environment)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or 'Git failed.').strip()[-2048:])
    return result.stdout.strip()


def prepare_update(log=lambda text: None):
    """Fetch as the normal user before requesting session interruption or root access."""
    if os.geteuid() == 0:
        raise ValueError('Run Monitorize as your normal desktop user.')
    checkout = source_checkout()
    if (Path(git(checkout, 'rev-parse', '--show-toplevel')).resolve() != checkout
            or git(checkout, 'remote', 'get-url', 'origin') not in OFFICIAL_REMOTES
            or git(checkout, 'branch', '--show-current') != 'main'):
        raise ValueError('Update requires the official Monitorize VKMS main checkout.')
    if git(checkout, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('The VKMS checkout has local changes. Save them before updating.')
    log('Fetching Monitorize VKMS…')
    git(checkout, 'fetch', 'origin', 'main', timeout=120)
    if git(checkout, 'rev-list', '--count', 'FETCH_HEAD..HEAD') != '0':
        raise ValueError('The VKMS checkout has local commits. Resolve them before updating.')
    git(checkout, 'merge', '--ff-only', 'FETCH_HEAD', timeout=30)
    revision = git(checkout, 'rev-parse', 'HEAD')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid VKMS source revision.')
    if not (checkout / 'monitorize_vkms/managed_install.py').is_file():
        raise ValueError('This VKMS source version does not support graphical updates. Update it using its README first.')
    log('Source ready. Preparing installation…')
    return dict(checkout=str(checkout), revision=revision)


def install_update(prepared, log=lambda text: None):
    """No timeout on authorized installation; the process owns rollback/completion."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        return dict(success=False, message='Polkit is required for graphical updates.')
    command = [pkexec, '/usr/bin/python3', '-I', '-B',
               str(Path(prepared['checkout']) / 'monitorize_vkms/managed_install.py'),
               'install', '--revision', prepared['revision']]
    from monitorize.platform.polkit_agent import agent_missing, start_agent
    for attempt in range(2):
        lines = ''
        started = False
        process = subprocess.Popen(command, cwd=prepared['checkout'], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        for line in process.stdout:
            started = started or 'management_started' in line
            lines = (lines + line)[-65536:]
            try:log(line.rstrip())
            except RuntimeError:pass
        code = process.wait()
        if code == 127 and not started and attempt == 0 and agent_missing(lines) and start_agent():
            time.sleep(.5)
            continue
        if code == 126:
            return dict(success=False, cancelled=True, message='VKMS update cancelled.')
        try:
            value = result_object(lines)
            if type(value.get('success')) is not bool:
                raise ValueError('Missing update result.')
            if code:
                value['success'] = False
            return value
        except ValueError:
            return dict(success=False, outcome_unknown=started or code not in (126, 127),
                        message='VKMS update failed. Review the log before retrying.')
