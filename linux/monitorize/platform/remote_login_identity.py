"""Private, atomic identity provisioning and engine-lifetime ownership.

Provisioning is invoked only by explicit setup after legacy engines are stopped.
A lock cannot stop old Sunshine versions that do not participate in this protocol;
setup must independently verify those processes exited before calling migration.
No credential values are returned in status or exception text.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import subprocess
import uuid

FILES=('sunshine_state.json','cacert.pem','cakey.pem')


@dataclass(frozen=True)
class IdentityPaths:
    directory: Path
    state: Path
    cert: Path
    key: Path
    lock: Path

    def config_overrides(self):
        return {'file_state':str(self.state),'credentials_file':str(self.state),
                'cert':str(self.cert),'pkey':str(self.key)}


def identity_paths(directory, *, flat=False):
    directory=Path(directory)
    identity=directory if flat else directory/'identity'
    return IdentityPaths(identity,identity/FILES[0],identity/FILES[1],identity/FILES[2],directory/'engine.lock')


def _directory(path,uid,*,private=True):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode & (0o077 if private else 0o022):
        raise ValueError('Identity directory must be private and owned by the selected account.')


@contextmanager
def acquire_identity_lock(paths, *, uid=None, private_directory=True):
    uid=os.geteuid() if uid is None else uid
    _directory(paths.lock.parent,uid,private=private_directory)
    try:
        fd=os.open(paths.lock,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        if os.geteuid()==0:os.fchown(fd,uid,-1)
    except FileExistsError:
        fd=os.open(paths.lock,os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1 or info.st_mode & 0o077:
            raise ValueError('Unsafe remote-login engine lock.')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def _read_private(path,uid):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1 or info.st_mode & 0o022 or info.st_size>1024*1024:
                raise ValueError('Unsafe source identity file.')
            result=source.read(1024*1024+1)
            if not result or len(result)>1024*1024:raise ValueError('Invalid source identity file.')
            return result
    except OSError:
        raise ValueError('Source identity is missing or unsafe.') from None


def _rename_exclusive(source,destination):
    # Linux renameat2 makes publication atomic without replacing a raced destination.
    libc=ctypes.CDLL(None,use_errno=True)
    rename=libc.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(source),-100,os.fsencode(destination),1):
        code=ctypes.get_errno()
        raise OSError(code,os.strerror(code))


class IdentitySetupError(ValueError):
    """A safe, actionable identity error for setup UI."""


def _source_files(source, uid):
    credentials = source / 'credentials'
    if os.path.lexists(credentials):
        _directory(credentials, uid, private=False)
    else:
        credentials = source
    raw = {}
    for name in FILES:
        path = (source if name == FILES[0] else credentials) / name
        if os.path.lexists(path):
            try:
                raw[name] = _read_private(path, uid)
            except ValueError as error:
                raise IdentitySetupError('A Sunshine identity file is unsafe or unreadable. Repair the primary desktop profile and retry.') from error
    return raw


def source_fingerprint(source, uid):
    raw = _source_files(source, uid)
    return {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}


def _openssl(*args, data=None):
    from monitorize.remote_login.daemon import require_installed
    try:
        executable = str(Path('/usr/bin/openssl').resolve(strict=True))
        require_installed(executable)
        return subprocess.run([executable, *args], input=data, capture_output=True,
                              timeout=15, check=True).stdout
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise IdentitySetupError('Could not prepare the Sunshine TLS identity. Check the system OpenSSL installation and the primary profile.') from error


def _prepare_identity(raw, stage):
    state_name, cert_name, key_name = FILES
    try:
        state = json.loads(raw.get(state_name, b'{}'))
        if not isinstance(state, dict):
            raise ValueError()
        root = state.get('root')
        if 'root' in state:
            if (not isinstance(root, dict) or not isinstance(root.get('uniqueid'), str)
                    or not root['uniqueid'].strip()
                    or any(root[k] != '' and not isinstance(root[k], list)
                           for k in ('devices', 'named_devices') if k in root)):
                raise ValueError()
        elif state and not all(isinstance(state.get(k), str) and state[k]
                               for k in ('username', 'salt', 'password')):
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise IdentitySetupError('Sunshine pairing state is invalid. Restore the primary profile from backup and retry.') from error

    has_cert, has_key = cert_name in raw, key_name in raw
    if has_cert != has_key or (root is not None and not has_cert):
        raise IdentitySetupError('Sunshine TLS credentials are incomplete. Restore the certificate and key for the primary profile and retry.')
    if root is None:
        if has_cert:
            public = _openssl('x509', '-pubkey', '-noout', data=raw[cert_name])
            if public != _openssl('pkey', '-pubout', data=raw[key_name]):
                raise IdentitySetupError('Sunshine certificate and private key do not match. Restore the matching pair and retry.')
        else:
            _openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7300',
                     '-subj', '/CN=Sunshine Gamestream Host', '-keyout', str(stage / key_name),
                     '-out', str(stage / cert_name))
            for name in (cert_name, key_name):
                path = stage / name
                raw[name] = path.read_bytes()
                path.unlink()
        state['root'] = {'uniqueid': str(uuid.uuid4()), 'named_devices': []}
        raw[state_name] = json.dumps(state).encode()
    return raw


def migrate_identity(source,destination, *, uid):
    """Atomically import a paired identity or create one for a fresh profile."""
    source=Path(source);destination=Path(destination)
    _directory(source,uid,private=False)
    with acquire_identity_lock(identity_paths(source,flat=True),uid=uid,private_directory=False):
        raw = _source_files(source, uid)
        stage=Path(tempfile.mkdtemp(prefix='.'+destination.name+'.',dir=destination.parent))
        try:
            raw = _prepare_identity(raw, stage)
            identity=stage/'identity';identity.mkdir(mode=0o700)
            for name,data in raw.items():
                fd=os.open(identity/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as target:
                    target.write(data);target.flush();os.fsync(target.fileno())
                if os.geteuid()==0:os.chown(identity/name,uid,-1)
            for path in (identity,stage):
                if os.geteuid()==0:os.chown(path,uid,-1)
                fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            _rename_exclusive(stage,destination)
            fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if stage.exists():shutil.rmtree(stage)
    return {'changed':True,'directory':str(destination),'backup':str(source)}


def fallback_grant_path(uid):
    return Path('/run/monitorize/desktop-fallback')/(str(uid)+'.json')


def _fallback_context():
    import subprocess
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    session=os.environ.get('XDG_SESSION_ID','')
    if not session:
        session=subprocess.run(['loginctl','show-user',str(os.getuid()),'--property=Display','--value'],
            capture_output=True,text=True,timeout=3,check=True).stdout.strip()
    return boot,session


def desktop_fallback_allowed(settings, *, owner_uid=0):
    """Root grants desktop ownership only for this boot/account/login session."""
    path=fallback_grant_path(settings.uid)
    try:
        parent=path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=owner_uid or parent.st_mode & 0o022:return False
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_nlink!=1 or info.st_size>4096:return False
            grant=json.load(stream)
        if not isinstance(grant,dict) or type(grant.get('uid')) is not int:return False
        identity_keys={'uid','session','boot_id'}
        if set(grant)!=identity_keys:
            if (set(grant)!=identity_keys|{'schema_version','unit_enablement','owned_runtime_masks'}
                or type(grant.get('schema_version')) is not int or grant['schema_version']!=1):return False
            names={'monitorize-remote-login.service',
                   *(f'monitorize-remote-login-{phase}@{settings.uid}.service' for phase in ('boot','desktop'))}
            enablement=grant['unit_enablement'];masks=grant['owned_runtime_masks']
            if (not isinstance(enablement,dict) or set(enablement)!=names
                or any(type(flag) is not bool for flag in enablement.values())
                or not isinstance(masks,list) or any(not isinstance(name,str) or name not in names for name in masks)
                or len(masks)!=len(set(masks))):return False
        boot,session=_fallback_context()
        return bool(session and grant['uid']==settings.uid and grant['session']==session and grant['boot_id']==boot)
    except (OSError,ValueError,TypeError):return False


def has_paired_clients():
    from monitorize.remote_login.settings import read_settings

    try:
        settings = read_settings()
        if settings.uid != os.getuid():
            return None
        root = json.loads(_read_private(identity_paths(settings.directory).state, settings.uid))['root']
        return bool(root.get('devices') or root.get('named_devices'))
    except (OSError, ValueError, KeyError, AttributeError):
        return None
