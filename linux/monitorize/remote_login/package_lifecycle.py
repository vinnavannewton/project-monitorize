"""Recoverable native-package pause/resume; pairing and user profiles are untouched."""
import json
from pathlib import Path
import subprocess
import sys
import time

from .root_files import RootFiles

RECEIPT = 'var/lib/monitorize/remote-login-package.json'
CONFIG = 'etc/monitorize/remote-login.json'
SUPERVISOR = 'monitorize-remote-login.service'
BOOT = 'monitorize-vkms-boot-display.service'
RECOVERY = 'sudo /usr/bin/python3 -I /usr/libexec/monitorize/remote-login/package-guard.py'


def unit_names(uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('Invalid remote-login account.')
    return [SUPERVISOR, BOOT, *(f'monitorize-remote-login-{phase}@{uid}.service' for phase in ('boot', 'desktop'))]


class PackageLifecycle:
    def __init__(self, backend, *, root=Path('/'), owner_uid=0):
        self.backend = backend
        self.files = RootFiles(root, owner_uid)
        self.path = self.files._path(RECEIPT)

    def load(self):
        self.files._parents(self.path)
        previous = self.files._read(self.path)
        if previous is None:
            return None
        value = json.loads(previous[0])
        if (previous[1] & 0o077 or not isinstance(value, dict)
                or set(value) != {'uid', 'operation', 'boot_id', 'restart', 'masks', 'stage'}
                or value['operation'] not in ('upgrade', 'remove')
                or value['stage'] not in ('pausing', 'prepared')
                or type(value['restart']) is not bool or not isinstance(value['boot_id'], str)):
            raise ValueError('Unsafe package recovery receipt.')
        names = unit_names(value['uid'])
        if not isinstance(value['masks'], list) or any(name not in names for name in value['masks']):
            raise ValueError('Unsafe package recovery unit list.')
        return value

    def save(self, value):
        self.files.write(RECEIPT, json.dumps(value), 0o600)

    def prepare(self, operation, *, allow_disconnect=False, include_idle_services=False):
        if operation not in ('upgrade', 'remove'):
            raise ValueError('Invalid package operation.')
        value = self.load()
        if value is not None:
            if value['boot_id'] != self.backend.boot_id:
                raise RuntimeError('Package recovery belongs to a previous boot. Run '+RECOVERY+' --resume to clear expired maintenance, then retry.')
            if value['operation'] != operation:
                raise RuntimeError('A different package operation is pending. Run '+RECOVERY+' --resume first.')
            if value['stage'] == 'prepared' and operation == 'remove':
                self.backend.unmask(value['masks'])
                self.path.unlink()
                return
            if self.backend.connection_in_use(value['uid']) and not allow_disconnect:
                raise RuntimeError('Remote login became active during package recovery. Run '+RECOVERY+
                    ' --prepare '+operation+' --allow-disconnect, then retry the package command.')
        else:
            settings, protected = self.backend.state()
            if (not settings or not settings['enabled']) and not protected:
                # Explicit setup recovery also handles leftover active units after
                # an older disable operation. Ordinary package hooks stay silent.
                if (not include_idle_services or not settings
                        or not any(self.backend.active(name) for name in unit_names(settings['uid']))):
                    return
            if settings is None:
                raise RuntimeError('A protected display has no enrollment configuration; repair its ownership before retrying.')
            uid = settings['uid']
            names = unit_names(uid)
            if self.backend.connection_in_use(uid) and not allow_disconnect:
                raise RuntimeError('A remote-login client is connected, or its connection state is unavailable. '
                    'This '+operation+' will disconnect it. From a local terminal, run '+RECOVERY+
                    ' --prepare '+operation+' --allow-disconnect, then retry the package command. Pairing is retained.')
            value = dict(uid=uid, operation=operation, boot_id=self.backend.boot_id,
                         restart=self.backend.active(SUPERVISOR),
                         masks=[name for name in names if not self.backend.masked(name)], stage='pausing')
            self.save(value)  # Persist intent before the first system mutation.
        try:
            self.backend.pause(value['uid'], value['masks'], allow_disconnect=allow_disconnect)
            self.backend.release(value['uid'])
            if operation == 'remove':
                self.backend.disable(value['uid'], value['masks'])
            value['stage'] = 'prepared'
            self.save(value)
            if operation == 'remove':
                self.backend.unmask(value['masks'])
                self.path.unlink()
        except Exception as error:
            raise RuntimeError('Remote-login package cleanup failed: '+str(error)+
                '. Package replacement is blocked; pairing/settings are retained. Fix the cause and retry, '
                'or recover paused services with '+RECOVERY+' --resume.') from error

    def disable_enrollment(self,uid):
        """Explicit authorized disable, including stale upgrade maintenance.

        Publish disabled policy before releasing outputs so cleanup failure cannot
        restore boot startup. Never restart hosting while fulfilling an off request.
        """
        settings,_=self.backend.state()
        if settings is None or settings.get('uid')!=uid:
            raise PermissionError('Only the enrolled account can disable remote login.')
        value=self.load()
        if value is not None and value['uid']!=uid:
            raise PermissionError('Maintenance belongs to another account.')
        if value is not None and value['boot_id']!=self.backend.boot_id:
            self.path.unlink()  # Old runtime masks do not belong to this boot.
            value=None
        if value is None:
            value=dict(uid=uid,operation='remove',boot_id=self.backend.boot_id,restart=False,
                masks=[name for name in unit_names(uid) if not self.backend.masked(name)],stage='pausing')
        else:
            value.update(operation='remove',restart=False,stage='pausing')
        self.save(value)
        self.backend.pause(uid,value['masks'],allow_disconnect=True)
        self.backend.disable(uid,value['masks'])
        self.backend.release(uid)
        self.backend.unmask(value['masks'])
        self.path.unlink()

    def resume(self):
        value = self.load()
        if value is None:
            return
        if value['boot_id'] != self.backend.boot_id:
            # Runtime masks died with the old boot. Never unmask or replay unit
            # state now owned by this boot; only clear the expired admission gate.
            self.path.unlink()
            return
        try:
            # Restore ownership before allowing systemd to start capture again.
            restart = self.backend.restore(value['uid'], restart=value['restart'])
            self.backend.unmask(value['masks'])
            if restart:
                self.backend.start()
            self.path.unlink()
        except Exception as error:
            raise RuntimeError('Package files may already be installed, but remote-login recovery failed: '+str(error)+
                               '. Pairing is retained. Fix the cause, then run '+RECOVERY+' --resume.') from error


class InstalledBackend:
    def __init__(self):
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def run(self, *arguments):
        result = subprocess.run(['/usr/bin/systemctl', *arguments], capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError('systemctl '+' '.join(arguments)+' failed: '+result.stderr.strip()[:1024])
        return result.stdout.strip()

    def properties(self, name):
        return dict(line.split('=', 1) for line in self.run('show', name, '--property=ActiveState',
            '--property=LoadState', '--property=MainPID', '--property=ControlGroup').splitlines() if '=' in line)

    def active(self, name):
        return self.properties(name).get('ActiveState') not in ('inactive', 'failed')

    def masked(self, name):
        return self.properties(name).get('LoadState') == 'masked'

    def state(self):
        from .settings import SETTINGS, read_settings
        from .daemon import require_installed
        # No optional host dependency is needed when remote login was never used.
        settings = read_settings() if SETTINGS.exists() else None
        from dataclasses import asdict
        lease = Path('/run/monitorize-vkms/primary.json')
        protected = False
        if lease.exists():
            require_installed(lease)
            protected = json.loads(lease.read_text()).get('protected') is True
        return (asdict(settings) if settings else None), protected

    def connection_in_use(self, uid):
        from .settings import read_settings
        from .health import read_health
        settings = read_settings()
        for phase in ('boot', 'desktop'):
            values = self.properties(f'monitorize-remote-login-{phase}@{uid}.service')
            if values.get('ActiveState') in ('inactive', 'failed'):
                continue
            health = read_health(settings.directory/f'runtime/{phase}-health.json', uid=uid,
                                 pid=int(values.get('MainPID', '0')), boot_id=self.boot_id)
            if health.get('state') not in ('desktop_listening', 'disconnect_grace'):
                return True
        return False

    def wait_for_pause(self, uid):
        from .health import read_health
        from .settings import read_settings
        settings = read_settings()
        deadline = time.monotonic() + 4
        while True:
            pending = False
            for phase in ('boot', 'desktop'):
                values = self.properties(f'monitorize-remote-login-{phase}@{uid}.service')
                if values.get('ActiveState') in ('inactive', 'failed'):
                    continue
                health = read_health(settings.directory/f'runtime/{phase}-health.json', uid=uid,
                    pid=int(values.get('MainPID', '0')), boot_id=self.boot_id)
                if health.get('package_paused') is not True:
                    pending = True
                elif health.get('package_busy') is not False:
                    raise RuntimeError('A remote client connected during preparation; explicitly prepare with --allow-disconnect to continue')
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('The running remote-login engine did not acknowledge package maintenance; '
                    'disconnect its client and explicitly prepare with --allow-disconnect, or run --resume to cancel')
            time.sleep(.05)

    def pause(self, uid, masks, *, allow_disconnect=False):
        names = unit_names(uid)
        if masks:
            self.run('mask', '--runtime', *masks)
        if not allow_disconnect:
            self.wait_for_pause(uid)
        self.run('stop', *names)
        for name in names:
            values = self.properties(name)
            if values.get('ActiveState') not in ('inactive', 'failed'):
                raise RuntimeError(name+' has not stopped')
            group = values.get('ControlGroup', '')
            if group:
                if not group.startswith('/') or '..' in group.split('/'):
                    raise ValueError('Invalid service cgroup.')
                events = Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events'
                if events.exists() and any(line.split() == ['populated', '1'] for line in events.read_text().splitlines()):
                    raise RuntimeError(name+' still has running processes')
            if self.properties(name).get('LoadState') != 'masked':
                raise RuntimeError(name+' could not be inhibited')

    def host(self):
        from .daemon import require_installed
        sys.path.insert(0, '/usr/lib/monitorize-vkms')
        from monitorize_vkms import boot_display, boot_runtime, helper
        for module in (boot_display, boot_runtime, helper):
            require_installed(module.__file__)
        return boot_display, boot_runtime, helper

    def release(self, uid):
        # The host facade verifies current boot, exact connector, EDID, lifecycle
        # lock and capture quiescence. Never remove an ordinary desktop lease.
        _, protected = self.state()
        if not protected:
            return
        host, runtime, helper = self.host()
        lease = host.boot_output_status(helper.STATE_DIRECTORY)
        if lease['phase'] == 'desktop':
            from .enrollment import remove_reserved
            remove_reserved(uid, release=True)
        else:
            with host._transaction(enabled=False) as (_, runtime, settings, lease, connector):
                if settings['uid'] != uid or lease['phase'] != 'greeter':
                    raise PermissionError('Reservation owner changed.')
                context = runtime.greeter_context()
                if lease['session'] not in ('', context[1]):
                    raise PermissionError('Reservation belongs to another greeter.')
                with runtime.quiescent(uid):
                    if runtime.greeter_context() != context:
                        raise PermissionError('Greeter changed during package cleanup.')
                    runtime.idle_reserved(state=helper.STATE_DIRECTORY, connector=connector, lease=lease)
                    if runtime.greeter_context() != context:
                        raise PermissionError('Greeter changed during package cleanup.')
                    from monitorize_vkms.lease import remove_lease
                    remove_lease(helper.STATE_DIRECTORY, 'mon1')

    def disable(self, uid, masks):
        from .settings import read_settings
        from .enrollment import configure_disabled, Commands
        settings = read_settings()
        if settings.uid != uid:
            raise PermissionError('Enrollment owner changed.')
        files = RootFiles()
        previous = files._read(files._path(CONFIG))
        values = json.loads(previous[0])
        values['enabled'] = False
        # systemctl disable silently ignores runtime-masked unit fragments.
        # Publish disabled policy before unmasking our units, then remove their
        # persistent enablement. Any concurrent start now observes disabled policy.
        files.write(CONFIG, json.dumps(values), 0o600)
        self.unmask(masks)
        configure_disabled(values, files=files, commands=Commands())

    def restore(self, uid, *, restart):
        from .settings import read_settings
        settings = read_settings()
        if settings.uid != uid:
            raise PermissionError('Enrollment owner changed.')
        if not restart or not settings.enabled:
            return False
        # A failed idle handshake has not stopped the running listener. Cancel
        # admission gating without re-reserving its live output or disconnecting it.
        if self.active(SUPERVISOR):
            return True
        host, runtime, helper = self.host()
        if host.boot_output_status(helper.STATE_DIRECTORY) is not None:
            host.main()  # Validate an existing reservation without replacing it.
            return True
        sessions = runtime.local_sessions()
        if any(row.get('seat') == 'seat0' and row.get('class') not in ('greeter', 'manager') for row in sessions):
            import secrets
            host.reserve_desktop_installed(uid, runtime.desktop_context(uid), generation=secrets.token_hex(16))
        else:
            host.main()
        return True

    def unmask(self, masks):
        if masks:
            self.run('unmask', '--runtime', *masks)
        self.run('daemon-reload')

    def start(self):
        self.run('start', BOOT, SUPERVISOR)
