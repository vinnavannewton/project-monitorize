#!/usr/bin/python3 -I
"""Restricted experimental greeter capture; standalone standard-library runner.

Installed as root-owned code but executed as the selected non-root account.
No desktop handoff or normal profile mutation is implemented here.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time

SETTINGS = Path('/etc/monitorize-login-capture.json')
ENGINE = Path('/usr/local/libexec/monitorize-login-capture/sunshine')
ASSETS = Path('/usr/local/share/monitorize-login-capture/assets')


def session_phase(sessions):
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise ValueError('Could not verify logind sessions; refusing capture.')
    local = [s for s in sessions if s.get('seat') == 'seat0']
    # Any account's user session stops this experiment, including a locked one.
    if any(s.get('class') in ('user', 'user-early', 'user-incomplete') for s in local):
        return 'user'
    if any(s.get('class') == 'greeter' for s in local):
        return 'greeter'
    return 'waiting'


def read_phase():
    result = subprocess.run(['/usr/bin/loginctl', 'list-sessions', '--json=short'],
                            capture_output=True, text=True, check=True, timeout=3)
    return session_phase(json.loads(result.stdout))


def validate_identity(raw):
    try:
        data = json.loads(raw)
        root = data['root']
        if not isinstance(root['uniqueid'], str) or not root['uniqueid'].strip():
            raise ValueError('Missing host UUID.')
        if not any(key in root for key in ('named_devices', 'devices')):
            raise ValueError('No saved paired-client state.')
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('A valid existing paired Sunshine identity is required.') from exc
    return raw


def build_config(directory, hostname, encoder, adapter):
    if encoder not in ('vaapi', 'nvenc', 'software'):
        raise ValueError('Choose vaapi, nvenc or software for this experiment.')
    for value in (str(directory), hostname, adapter):
        if '\n' in value or '\r' in value:
            raise ValueError('Multiline configuration values are forbidden.')
    values = dict(capture='kms', encoder=encoder, adapter_name=adapter,
        sunshine_name=hostname, port='47989',
        file_state=str(directory/'identity/sunshine_state.json'),
        credentials_file=str(directory/'identity/sunshine_state.json'),
        cert=str(directory/'identity/cacert.pem'), pkey=str(directory/'identity/cakey.pem'),
        file_apps=str(directory/'apps.json'), log_path=str(directory/'logs/sunshine.log'),
        stream_audio='disabled', controller='disabled', keyboard='enabled', mouse='enabled',
        upnp='disabled', origin_web_ui_allowed='pc', min_log_level='2')
    return '\n'.join(f'{key} = {value}' for key, value in values.items()) + '\n'


def find_boot_output(root=Path('/sys/class/drm'),*,require_enabled=True):
    found=[]
    for path in root.glob('card*-Virtual-*'):
        parts = (path / 'device').resolve().parts
        if not any(parts[index:index + 2] == ('faux', 'monitorize')
                   for index in range(len(parts) - 1)):
            continue
        try:
            if (path/'status').read_text().strip() != 'connected':
                continue
            if require_enabled and (path/'enabled').read_text().strip() != 'enabled':
                continue
            with (path/'edid').open('rb') as source:
                edid=source.read(128)
            if (len(edid) == 128 and edid[:8] == b'\x00\xff\xff\xff\xff\xff\xff\x00'
                    and sum(edid) % 256 == 0 and edid[77:81] == b'mon1'):
                name=path.name.split('-',1)[1]
                if re.fullmatch(r'Virtual-[0-9]+', name):
                    found.append(name)
        except OSError:
            continue
    if len(found)>1:
        raise ValueError('Multiple Monitorize mon1 outputs; refusing ambiguous capture.')
    return found[0] if found else None


def require_root_file(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Expected root-owned, non-writable installed file: '+str(path))


def stop_engine(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def run_capture(directory, *, phase_reader=read_phase, output_finder=find_boot_output):
    if os.geteuid() == 0:
        raise ValueError('Boot capture must run as the configured non-root user.')
    # All configuration code is root-owned. State/identity/logs belong to the user.
    for path in (ENGINE, directory/'sunshine.conf', directory/'apps.json'):
        require_root_file(path)
    caps=subprocess.run(['/usr/sbin/getcap', '--', str(ENGINE)], capture_output=True,
                        text=True, timeout=3, check=True)
    if 'cap_sys_admin' not in caps.stdout:
        raise ValueError('Installed probe engine lacks KMS capability; rerun its setup.')
    if not os.access('/dev/uinput', os.R_OK | os.W_OK):
        raise ValueError('Probe service cannot access /dev/uinput.')
    descriptor=os.open(directory/'runtime/engine.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info=os.fstat(descriptor)
        if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Unsafe probe process lock.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline=time.monotonic()+45
        output=None
        while time.monotonic()<deadline:
            phase=phase_reader()
            if phase == 'user':
                print('A user is already logged in; boot capture remains stopped.', flush=True)
                return 0
            if phase == 'greeter':
                output=output_finder()
                if output:
                    break
            time.sleep(0.5)
        if not output:
            raise ValueError('No enabled mon1 greeter output within 45 seconds. Inspect greeter/VKMS logs.')
        # Empty user session environment prevents accidental portal/Wayland access.
        environment=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', HOME=str(directory),
            LANG='C.UTF-8', XDG_SEAT='seat0', XDG_CONFIG_HOME=str(directory),
            XDG_RUNTIME_DIR=str(directory/'runtime'), SUNSHINE_CONFIG_DIR=str(directory),
            SUNSHINE_ASSETS_DIR=str(ASSETS), MONITORIZE_REQUIRE_EXACT_OUTPUT='1',
            MONITORIZE_CAPTURE_OUTPUT=output)
        print('Starting experimental KMS greeter capture on '+output, flush=True)
        process=subprocess.Popen([str(ENGINE), str(directory/'sunshine.conf'),
                                  'output_name='+output], cwd=directory, env=environment,
                                 start_new_session=True)
        stopping=False
        def requested_stop(*_):
            nonlocal stopping
            stopping=True
        old={number: signal.signal(number, requested_stop) for number in (signal.SIGTERM,signal.SIGINT)}
        try:
            while not stopping and process.poll() is None:
                if phase_reader() != 'greeter':
                    print('Greeter ended or user login started; stopping boot stream. Desktop handoff is not installed yet.', flush=True)
                    break
                if output_finder() != output:
                    raise ValueError('Boot capture output disappeared or changed; stopping.')
                time.sleep(0.25)
            return process.returncode or 0
        finally:
            stop_engine(process)
            for number, handler in old.items():
                signal.signal(number, handler)
    finally:
        os.close(descriptor)


def main():
    require_root_file(SETTINGS)
    settings=json.loads(SETTINGS.read_text())
    uid=settings.get('uid')
    if type(uid) is not int or uid<=0 or uid != os.geteuid():
        raise ValueError('Probe account does not match its installed configuration.')
    return run_capture(Path('/var/lib/monitorize-login-capture')/str(uid))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('LOGIN CAPTURE PROBE ERROR: '+str(exc), flush=True)
        raise SystemExit(1)
