#!/usr/bin/python3 -I
"""Explicit Polkit-authorized Monitorize service repair; never parse shell text."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

REPAIR_PROTOCOL = 1
HERE = Path(__file__).resolve().parent


def load_deployer():
    installed = HERE / 'deploy.py'
    source = HERE.parents[1] / 'scripts/install-remote-login-assets.py'
    spec = importlib.util.spec_from_file_location('monitorize_deployer', installed if installed.is_file() else source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(command, *, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip() or 'Repair command failed.')[-4096:])
    return result.stdout.strip()


def snapshot_file(source, target, uid, *, dir_fd=None, allow_root=False, limit=512 * 1024 * 1024):
    """Copy an opened regular file into a private root snapshot, with bounded reads."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    with os.fdopen(fd, 'rb') as incoming:
        info = os.fstat(incoming.fileno())
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid != uid and not (allow_root and info.st_uid == 0 and info.st_mode & 0o004))
                or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > limit):
            raise ValueError('Repair input has unsafe ownership, permissions or size.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            total = 0
            while block := incoming.read(1024 * 1024):
                total += len(block)
                if total > limit:
                    raise ValueError('Repair input exceeds its size bound.')
                output.write(block)
        target.chmod(0o600)
        return total


def open_directory(path):
    """Anchor every directory component without following user-controlled links."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Use an absolute source stage without parent traversal.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def snapshot_stage(source, target, uid, deployer):
    descriptor = open_directory(source)
    total = 0
    count = 0
    try:
        # fwalk checks opened directory identities and does not follow links.
        # Relative opens stay anchored even if a stage directory is renamed.
        for directory, directories, filenames, directory_fd in os.fwalk('.', follow_symlinks=False, dir_fd=descriptor):
            for name in directories:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('Repair stages cannot contain symlink directories.')
            for name in filenames:
                relative = (Path(directory) / name).as_posix()
                # App repair never changes the separately installed VKMS host.
                if (not deployer.allowed(relative) or relative in deployer.HOST_FILES
                        or relative.startswith('usr/lib/monitorize-vkms/') or relative in deployer.RETIRED_FILES):
                    raise ValueError('Unexpected source repair asset: ' + relative)
                count += 1
                if count > 10000:
                    raise ValueError('Too many source repair files.')
                total += snapshot_file(name, target / relative, uid, dir_fd=directory_fd)
                if total > 1024 * 1024 * 1024:
                    raise ValueError('Source repair stage exceeds its size bound.')
    finally:
        os.close(descriptor)
    deployer.validate_bundle(target)
    return target


def guard(*arguments):
    if not Path('/usr/libexec/monitorize/remote-login/package-guard.py').is_file():
        if (Path('/etc/monitorize/remote-login.json').exists()
                or Path('/run/monitorize-vkms/primary.json').exists()):
            raise RuntimeError('The installed lifecycle helper is missing. Reinstall the matching Monitorize package before repairing enrolled services.')
        return
    run(['/usr/bin/python3', '-I', '/usr/libexec/monitorize/remote-login/package-guard.py', *arguments])


def prepare_upgrade():
    receipt = Path('/var/lib/monitorize/remote-login-package.json')
    was_pending = receipt.exists()
    try:
        guard('--prepare', 'upgrade')
    except Exception:
        # Cancel only maintenance created by this attempt. Preserve recovery
        # state belonging to a previous transaction for explicit diagnosis.
        if not was_pending and receipt.exists():
            guard('--resume')
        raise


@contextmanager
def setup_lock():
    fd = os.open('/run/monitorize-remote-login-setup.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('Unsafe remote-login setup lock.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def repair_source(stage, deployer):
    installer = deployer.Installer()
    relatives = [path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()]
    installer.assert_unmanaged(relatives)
    for relative in relatives:
        if deployer.package_owner(Path('/') / relative):
            raise ValueError('A service asset belongs to a package. Use native-package repair: ' + relative)
    # Reuse the lifecycle receipt/masks to block reactivation, refuse connected
    # clients and preserve enrollment/identity while code is replaced.
    prepare_upgrade()
    try:
        with setup_lock():
            if run(['/usr/bin/systemctl', 'show', 'monitorize-remote-login.service', '--property=LoadState', '--value']) != 'not-found':
                run(['/usr/bin/systemctl', 'stop', 'monitorize-remote-login.service'])
            backup = installer.install(stage)
    except Exception as error:
        if getattr(error, 'rollback_failed', False):
            raise RuntimeError(str(error) + '. Services remain paused; restore the backup before resuming.') from None
        guard('--resume')
        raise
    guard('--resume')
    return {'success': True, 'message': 'Remote-login service files repaired. Settings and pairing retained.',
            'backup': str(backup)}


def package_command(package, manager):
    """Inspect immutable snapshot metadata before allowing a fixed package action."""
    if manager == 'pacman':
        identity = run(['/usr/bin/pacman', '-Qqp', str(package)])
        if identity != 'monitorize':
            raise ValueError('Select a Monitorize Arch package.')
        return ['/usr/bin/pacman', '-U', '--noconfirm', str(package)]
    if manager == 'deb':
        identity = run(['/usr/bin/dpkg-deb', '--field', str(package), 'Package'])
        if identity != 'monitorize':
            raise ValueError('Select a Monitorize Debian package.')
        return ['/usr/bin/apt-get', 'install', '--yes', '--reinstall', str(package)]
    if manager == 'rpm':
        identity = run(['/usr/bin/rpm', '-qp', '--queryformat', '%{NAME}', str(package)])
        if identity != 'monitorize':
            raise ValueError('Select a Monitorize RPM package.')
        # Let the native package manager verify dependencies/signatures/hooks.
        if Path('/usr/bin/dnf').is_file():
            installed = run(['/usr/bin/rpm', '-q', '--queryformat', '%{EVR}', 'monitorize'])
            incoming = run(['/usr/bin/rpm', '-qp', '--queryformat', '%{EVR}', str(package)])
            return ['/usr/bin/dnf', '-y', 'reinstall' if installed == incoming else 'install', str(package)]
        if Path('/usr/bin/zypper').is_file():
            return ['/usr/bin/zypper', '--non-interactive', 'install', '--force', str(package)]
    raise ValueError('No supported native package manager was found.')


def repair_package(package, manager):
    command = package_command(package, manager)
    prepare_upgrade()
    try:
        # Never kill a package manager halfway through a privileged transaction.
        run(command, timeout=None)
    finally:
        guard('--resume')
    return {'success': True, 'message': 'Monitorize package installed. Reopen Monitorize to load the installed version.',
            'restart_required': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, choices=('source', 'pacman', 'deb', 'rpm'))
    parser.add_argument('--input', required=True, type=Path)
    args = parser.parse_args()
    try:
        raw_uid = os.environ.get('PKEXEC_UID', '')
        if os.geteuid() != 0 or not raw_uid.isdigit() or int(raw_uid) <= 0:
            raise ValueError('Start repair through Monitorize and authorize it with Polkit.')
        uid = int(raw_uid)
        with tempfile.TemporaryDirectory(prefix='monitorize-repair-') as temporary:
            target = Path(temporary)
            if args.kind == 'source':
                deployer = load_deployer()
                stage = snapshot_stage(args.input, target / 'stage', uid, deployer)
                result = repair_source(stage, deployer)
            else:
                name = {'pacman': 'monitorize.pkg.tar.zst', 'deb': 'monitorize.deb', 'rpm': 'monitorize.rpm'}[args.kind]
                snapshot_file(args.input, target / name, uid, allow_root=True)
                result = repair_package(target / name, args.kind)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'success': False, 'message': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
