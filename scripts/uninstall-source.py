#!/usr/bin/env python3
"""Remove a source installation; keep or explicitly erase its application data.

No package installation/removal, broad process-name kills, or home-directory
scanning. Privileged cleanup is a fixed action in source-maintenance.py.
"""
import argparse
import errno
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import shlex
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = 'monitorize-remote-controller.service'
SYSTEM_TREES = (
    'usr/libexec/monitorize', 'usr/lib/monitorize-remote-login',
    'usr/share/monitorize', 'usr/local/libexec/monitorize-login-capture',
    'usr/local/share/monitorize-login-capture',
)
SYSTEM_FILES = (
    'usr/share/dbus-1/system.d/io.github.vinnavannewton.MonitorizeRemoteLogin1.conf',
    'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy',
    'usr/libexec/monitorize/monitorize-source-vkms-helper',
    'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.source-vkms.policy',
    'usr/lib/firewalld/services/monitorize.xml',
    'etc/ufw/applications.d/monitorize',
    'usr/lib/udev/rules.d/70-monitorize-uinput.rules',
    'usr/lib/modules-load.d/monitorize.conf',
    'usr/lib/sysusers.d/monitorize.conf',
)
SYSTEM_UNITS = (
    'monitorize-remote-login.service', 'monitorize-vkms-boot-display.service',
    'monitorize-remote-login-boot@.service', 'monitorize-remote-login-desktop@.service',
    'monitorize-remote-login-persistent@.service',
    'monitorize-login-capture.service', 'monitorize-remote-login-probe.service',
)


def existing(path):
    return path.exists() or path.is_symlink()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected an absolute cleanup path: ' + str(path))
    return path


def check_parents(path):
    """Never traverse a redirected ancestor when deleting a fixed child."""
    for parent in path.parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Cleanup ancestor is not a real directory: ' + str(parent))


def check_tree(path):
    check_parents(path)
    if not existing(path) or path.is_symlink() or not path.is_dir():
        return
    device = path.stat().st_dev
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in (*dirs, *files):
            child = Path(directory) / name
            if child.is_symlink():
                continue
            if child.stat().st_dev != device or child.is_mount():
                raise ValueError('Cleanup crosses a mount boundary: ' + str(child))
    if path.is_mount():
        raise ValueError('Cleanup target is a mount point: ' + str(path))


def erase(path):
    check_tree(path)
    if path.is_symlink() or (existing(path) and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)  # fd-based symlink-resistant implementation on Linux.


def xdg_roots(home, environment):
    defaults = {'CONFIG': home / '.config', 'DATA': home / '.local/share',
                'STATE': home / '.local/state', 'CACHE': home / '.cache'}
    # Include legacy hardcoded homes even when the current XDG roots differ.
    return {kind: list(dict.fromkeys((absolute(environment.get('XDG_' + kind + '_HOME') or default), default)))
            for kind, default in defaults.items()}


def user_targets(mode, *, home=None, environment=None, repository=ROOT):
    home = absolute(home or Path.home())
    roots = xdg_roots(home, os.environ if environment is None else environment)
    paths = [repository / 'linux/venv', repository / 'linux/build', repository / 'external/sunshine/build',
             repository / 'external/sunshine/node_modules']
    for config in roots['CONFIG']:
        paths.extend(config / relative for relative in (
            'autostart/monitorize.desktop', 'autostart/monitorize-udp.desktop',
            'systemd/user/' + CONTROLLER,
            'systemd/user/graphical-session.target.wants/' + CONTROLLER,
            'systemd/user/default.target.wants/' + CONTROLLER,
        ))
    for data in roots['DATA']:
        for app in ('monitorize', 'monitorize-udp'):
            paths.extend((data / 'applications' / (app + '.desktop'),
                          data / 'applications' / (app + '-kde-virtual-output.desktop')))
            for size in ('192x192', '512x512', 'scalable'):
                for extension in ('png', 'svg'):
                    paths.append(data / 'icons/hicolor' / size / 'apps' / (app + '.' + extension))
    for base in (repository / 'linux', repository / 'scripts', repository / 'packaging'):
        if base.is_dir():
            for directory, dirs, _ in os.walk(base, followlinks=False):
                dirs[:] = [name for name in dirs if name not in ('venv', 'build', '.git')]
                if '__pycache__' in dirs:
                    paths.append(Path(directory) / '__pycache__')
                    dirs.remove('__pycache__')
    if mode == 'all':
        paths.extend(base / 'monitorize' for bases in roots.values() for base in bases)
    return list(dict.fromkeys(paths))


def run(command, *, environment=None, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=environment)
    if result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(command) + '\n' + (result.stderr.strip() or result.stdout.strip()))
    return result.stdout.strip()


def process_matches(args, repository=ROOT, configs=()):
    """Match real argv elements, never substrings in unrelated shell commands."""
    if not args:
        return False
    if Path(args[0]).name.startswith('python'):
        for index, arg in enumerate(args[:-1]):
            if arg == '-m' and (args[index + 1] == 'monitorize' or args[index + 1].startswith('monitorize.')):
                return True
    binary = Path(args[0])
    if binary in (repository / 'linux/venv/bin/sunshine', repository / 'external/sunshine/build/sunshine',
                  repository / 'linux/venv/bin/monitorize-kde-virtual-output'):
        return True
    if binary.name == 'sunshine' and any(arg in configs for arg in args[1:]):
        return True
    return False


def processes(repository=ROOT):
    home = Path.home()
    roots = xdg_roots(home, os.environ)
    configs = [str(base / 'monitorize' / ('sunshine-' + str(i)) / 'sunshine.conf')
               for base in roots['CONFIG'] for i in (1, 2)]
    found = []
    for process in Path('/proc').glob('[0-9]*'):
        if int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            args = process.joinpath('cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if process_matches(args, repository, configs):
                found.append(int(process.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def request_quit(name):
    # Qt's relative QLocalServer names live in QDir::tempPath(). Check peer UID
    # as well as the socket entry: never send a command to another user.
    path = Path(tempfile.gettempdir()) / name
    if not existing(path):
        return
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Unexpected Monitorize socket owner: ' + str(path))
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(2)
        try:
            peer.connect(str(path))
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                return
            raise
        _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise ValueError('Monitorize IPC belongs to another user.')
        peer.sendall(b'quit')


def stop_desktop():
    request_quit('monitorize-tray-agent')
    request_quit('monitorize')
    deadline = time.monotonic() + 30
    while processes() and time.monotonic() < deadline:
        time.sleep(.1)
    if processes():
        raise RuntimeError('Monitorize processes are still running. Stop streams and Quit from the tray '
                           '(older versions may need this), resolve any GUI prompt, then retry. No application files were deleted.')


def system_targets(uid, mode, root=Path('/')):
    paths = [root / relative for relative in (*SYSTEM_TREES, *SYSTEM_FILES)]
    paths.extend(root / 'usr/lib/systemd/system' / name for name in SYSTEM_UNITS)
    paths.extend(root / 'etc/systemd/system' / name for name in ('monitorize-login-capture.service', 'monitorize-remote-login-probe.service'))
    for phase in ('boot', 'desktop'):
        paths.append(root / f'etc/systemd/system/monitorize-remote-login-{phase}@{uid}.service.d/10-device-groups.conf')
    if mode == 'all':
        paths.extend(root / relative for relative in ('etc/monitorize', 'var/lib/monitorize', 'etc/monitorize-login-capture.json',
                     f'var/lib/monitorize-login-capture/{uid}',
                     'run/monitorize-remote-login', f'run/monitorize/desktop-fallback/{uid}.json'))
    return list(dict.fromkeys(paths))


def source_system_present():
    return any(existing(path) for path in system_targets(os.getuid(), 'all'))


def check_system_parents(path):
    for parent in path.parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe system cleanup directory: ' + str(parent))


def package_check(paths, owner):
    for path in paths:
        check_tree(path)
        check_system_parents(path)
        entries = [path]
        if path.is_dir() and not path.is_symlink():
            entries += [Path(directory) / name for directory, dirs, files in os.walk(path, followlinks=False) for name in (*dirs, *files)]
        for entry in entries:
            if existing(entry) and (not entry.is_dir() or entry.is_symlink()) and owner(entry):
                raise ValueError('Package-owned file preserved: ' + str(entry) + '. Remove its package with the package manager.')


def module(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def check_system_account(uid, root=Path('/')):
    for relative in ('etc/monitorize/remote-login.json', 'etc/monitorize-login-capture.json'):
        settings = root / relative
        if not existing(settings):
            continue
        check_parents(settings)
        if settings.is_symlink():
            raise ValueError('Remote-login configuration is a symlink.')
        value = json.loads(settings.read_text())
        if value.get('uid') != uid:
            raise ValueError('System remote login belongs to another account; its configuration was preserved.')
    for relative in ('var/lib/monitorize/remote-login', 'var/lib/monitorize/remote-login-setup'):
        directory = root / relative
        check_parents(directory)
        if directory.is_symlink():
            raise ValueError('Unexpected remote-login state symlink.')
        if directory.is_dir():
            for child in directory.iterdir():
                identity = child.name.split('.', 1)[0]
                if identity.isdecimal() and int(identity) != uid:
                    raise ValueError('Saved remote-login data belongs to another account: ' + str(child))


def remove_firewall():
    firewall = shutil.which('firewall-cmd')
    if firewall:
        status = subprocess.run([firewall, '--state'], capture_output=True, text=True, timeout=10)
        if status.returncode not in (0, 252):
            raise RuntimeError('Cannot determine firewalld status; firewall cleanup is incomplete.')
        # Permanent configuration can be changed even while the daemon is down,
        # using its supported offline command.
        offline = shutil.which('firewall-offline-cmd')
        scopes = [([firewall, '--permanent'], True), ([firewall], False)] if status.returncode == 0 else []
        if status.returncode == 252:
            if not offline:
                raise RuntimeError('firewall-offline-cmd is required to clean inactive firewalld configuration.')
            scopes = [([offline], True)]
        for prefix, _ in scopes:
            for zone in run([*prefix, '--get-zones']).split():
                services = run([*prefix, '--zone=' + zone, '--list-services']).split()
                if 'monitorize' in services:
                    run([*prefix, '--zone=' + zone, '--remove-service=monitorize'])
    ufw = shutil.which('ufw')
    if ufw:
        # show added also sees stored rules when UFW is inactive. Only remove
        # the exact named rule generated by enable_firewall(), never raw ports.
        added = run([ufw, 'show', 'added'])
        for line in added.splitlines():
            try:
                words = shlex.split(line)
            except ValueError:
                continue
            if words == ['ufw', 'allow', 'Monitorize']:
                run([ufw, '--force', 'delete', 'allow', 'Monitorize'])
                break


def system_cleanup(uid, mode):
    """Only called after source-maintenance validates the Polkit caller/checkout."""
    sys.path.insert(0, str(ROOT / 'linux'))
    recovery = module('packaging/common/system_recovery.py', 'uninstall_recovery')
    paths = system_targets(uid, mode)
    check_system_account(uid)
    # Finish all provenance checks before stopping services or removing anything.
    package_check(paths, recovery.owner)
    from monitorize.remote_login.live_setup import setup_lock
    from monitorize.remote_login.package_lifecycle import InstalledBackend, PackageLifecycle
    with setup_lock():
        check_system_account(uid)
        backend = InstalledBackend()
        settings, protected = backend.state()
        lifecycle = PackageLifecycle(backend)
        pending = lifecycle.load()
        if pending is not None and pending['uid'] != uid:
            raise PermissionError('Remote-login maintenance belongs to another account.')
        if settings is not None:
            if settings['uid'] != uid:
                raise PermissionError('Remote login belongs to another account.')
            if settings['enabled'] or protected or pending is not None or any(backend.active(name) for name in (
                    'monitorize-remote-login.service', f'monitorize-remote-login-boot@{uid}.service',
                    f'monitorize-remote-login-desktop@{uid}.service')):
                from monitorize.remote_login.enrollment import disable
                result = disable(uid)
                if not result.get('success'):
                    raise RuntimeError(result.get('message', 'Remote-login cleanup failed.'))
        elif protected:
            raise RuntimeError('A protected display has no enrollment configuration; repair it before uninstalling.')
        elif pending is not None:
            lifecycle.prepare('remove', allow_disconnect=True)
        from monitorize.remote_login.enrollment import restore_linger, Commands
        restore_linger(uid, commands=Commands())
        # A legacy capture service must not retain a deleted engine or identity.
        for name in SYSTEM_UNITS:
            if '@' not in name:
                state = backend.properties(name)
                if state.get('LoadState') not in ('not-found', None):
                    backend.run('disable', '--now', name)
        remove_firewall()
        assets = system_targets(uid, 'keep')
        for path in assets:
            erase(path)
        run(['/usr/bin/systemctl', 'daemon-reload'])
        udev = shutil.which('udevadm')
        if udev:
            run([udev, 'control', '--reload-rules'])
            run([udev, 'trigger', '--subsystem-match=misc', '--sysname-match=uinput'])
        # Purge identity/recovery state last, after lifecycle and service reloads
        # succeeded. Earlier failures retain the information needed for retry.
        if mode == 'all':
            if not Path('/usr/libexec/monitorize-vkms').exists():
                remove_input_membership(uid)
            for path in paths:
                if path not in assets:
                    erase(path)
        remove_empty_system_dirs(uid, mode)
        remove_source_bytecode(uid)
    return dict(success=True, message='Source system assets removed; standalone VKMS retained.')


def remove_source_bytecode(uid):
    # Older source setup helpers could leave root-owned Python bytecode in
    # this checkout. Remove only generated cache trees, never source files.
    for base in (ROOT / 'linux', ROOT / 'scripts', ROOT / 'packaging'):
        for directory, dirs, _ in os.walk(base, followlinks=False):
            dirs[:] = [name for name in dirs if name not in ('venv', 'build', '.git')]
            if '__pycache__' in dirs:
                cache = Path(directory) / '__pycache__'
                check_tree(cache)
                for parent, children, files in os.walk(cache, followlinks=False):
                    for name in (*children, *files):
                        info = (Path(parent) / name).lstat()
                        if info.st_uid not in (0, uid):
                            raise ValueError('Generated cache belongs to another user: ' + str(cache))
                erase(cache)
                dirs.remove('__pycache__')


def remove_empty_system_dirs(uid, mode):
    for phase in ('boot', 'desktop'):
        dropin = Path(f'/etc/systemd/system/monitorize-remote-login-{phase}@{uid}.service.d')
        if dropin.is_dir() and not any(dropin.iterdir()):
            dropin.rmdir()
    legacy = Path('/var/lib/monitorize-login-capture')
    if mode == 'all' and legacy.is_dir() and not any(legacy.iterdir()):
        legacy.rmdir()


def remove_input_membership(uid):
    try:
        group = grp.getgrnam('monitorize-input')
    except KeyError:
        return
    user = pwd.getpwuid(uid)
    if user.pw_gid == group.gr_gid:
        raise ValueError('monitorize-input is the account primary group; manual review is required.')
    if user.pw_name in group.gr_mem:
        command = shutil.which('gpasswd')
        if not command:
            raise RuntimeError('gpasswd is required to remove Monitorize input membership.')
        run([command, '-d', user.pw_name, 'monitorize-input'])
    group = grp.getgrnam('monitorize-input')
    if not group.gr_mem and not any(account.pw_gid == group.gr_gid for account in pwd.getpwall()):
        command = shutil.which('groupdel')
        if not command:
            raise RuntimeError('groupdel is required to remove the unused Monitorize input group.')
        run([command, 'monitorize-input'])


def refresh_desktop():
    roots = xdg_roots(Path.home(), os.environ)
    for data in roots['DATA']:
        directory = data / 'applications'
        if directory.is_dir() and shutil.which('update-desktop-database'):
            run(['update-desktop-database', str(directory)])
        icons = data / 'icons/hicolor'
        if icons.is_dir() and shutil.which('gtk-update-icon-cache'):
            run(['gtk-update-icon-cache', '-f', '-t', str(icons)])
    if shutil.which('kbuildsycoca6'):
        run(['kbuildsycoca6'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('keep', 'all'))
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise PermissionError('Run linux/scripts/uninstall.sh as the desktop user, without sudo.')
        paths = user_targets(args.mode)
        for path in paths:
            check_tree(path)
        print('Uninstalling Monitorize' + (' and deleting its saved data.' if args.mode == 'all' else '; settings and pairing will be retained.'), flush=True)
        print('Stopping Monitorize and restoring its display layout…', flush=True)
        stop_desktop()
        # Recover a layout transaction left by a crashed desktop before erasing
        # its recovery journal. Use the venv while its Qt/D-Bus deps still exist.
        python = ROOT / 'linux/venv/bin/python3'
        if python.exists():
            code = ('from monitorize.streaming.recovery_guardian import recover_before_cleanup; '
                    'import sys; sys.exit(0 if recover_before_cleanup() else 1)')
            environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'linux')}
            run([str(python), '-B', '-c', code], environment=environment)
        roots = xdg_roots(Path.home(), os.environ)
        for base in roots['STATE']:
            journals = list((base / 'monitorize/recovery').glob('*.json'))
            if journals:
                raise RuntimeError('Unresolved display recovery journal: ' + str(journals[0]) + '. Restore that desktop session before uninstalling.')
        for base in roots['CONFIG']:
            legacy = base / 'monitorize/stock-vkms-recovery.json'
            if existing(legacy):
                raise RuntimeError('Legacy stock VKMS recovery remains: ' + str(legacy) + '. Restore the physical display layout before uninstalling.')
        controller = any(existing(config / 'systemd/user' / CONTROLLER) for config in roots['CONFIG'])
        if controller:
            run(['/usr/bin/systemctl', '--user', 'disable', '--now', CONTROLLER])
        if source_system_present():
            print('Requesting administrator authorization for source system cleanup…', flush=True)
            run([sys.executable, str(ROOT / 'scripts/authorize-source-maintenance.py'), 'uninstall-' + args.mode], timeout=300)
        if python.exists():
            code = ('from monitorize.platform.virtual_display_cleanup import remove_virtual_displays; '
                    'from monitorize.platform.utils import detect_desktop_environment; '
                    'import sys; result=remove_virtual_displays(detect_desktop_environment()); '
                    'print(result["message"]); sys.exit(0 if result["success"] else 1)')
            run([str(python), '-B', '-c', code], environment=environment, timeout=150)
        if processes():
            raise RuntimeError('Monitorize restarted during cleanup; stop it and rerun the uninstaller.')
        for path in paths:
            erase(path)
        if controller:
            run(['/usr/bin/systemctl', '--user', 'daemon-reload'])
        refresh_desktop()
        print('Monitorize uninstalled. ' + ('Saved data removed.' if args.mode == 'all' else 'Settings and pairing retained.'))
        print('Source checkout, standalone Sunshine, monitorize-vkms and shared prerequisites retained.')
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('Uninstall incomplete: ' + str(error) + '\nFix the reported issue and rerun; retained recovery data must not be deleted manually.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
