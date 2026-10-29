#!/usr/bin/python3 -I
"""Explicit Polkit-authorized Monitorize service repair; never parse shell text."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
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


def snapshot_file(source, target, uid, *, dir_fd=None, limit=512 * 1024 * 1024):
    """Copy an opened regular file into a private root snapshot, with bounded reads."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    with os.fdopen(fd, 'rb') as incoming:
        info = os.fstat(incoming.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
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
            raise ValueError('A service asset belongs to a package. Manage this file through its owning package: ' + relative)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Compatibility with existing source clients; package actions are rejected.
    parser.add_argument('--kind', choices=('source',), default='source', help=argparse.SUPPRESS)
    parser.add_argument('--input', required=True, type=Path)
    args = parser.parse_args()
    try:
        raw_uid = os.environ.get('PKEXEC_UID', '')
        if os.geteuid() != 0 or not raw_uid.isdigit() or int(raw_uid) <= 0:
            raise ValueError('Start repair through Monitorize and authorize it with Polkit.')
        uid = int(raw_uid)
        with tempfile.TemporaryDirectory(prefix='monitorize-repair-') as temporary:
            target = Path(temporary)
            deployer = load_deployer()
            stage = snapshot_stage(args.input, target / 'stage', uid, deployer)
            result = repair_source(stage, deployer)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'success': False, 'message': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
