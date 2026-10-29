"""Root-owned, fixed-target recovery primitives. No package installation or shell."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

PROTOCOL = 1
ASSETS=Path('/usr/share/monitorize/recovery')
TARGETS={'firewalld.xml':Path('/usr/lib/firewalld/services/monitorize.xml'),
         'ufw.profile':Path('/etc/ufw/applications.d/monitorize'),
         'uinput.rules':Path('/usr/lib/udev/rules.d/70-monitorize-uinput.rules')}


def owner(path):
    for executable,args in (('/usr/bin/pacman',['-Qoq','--']),
                            ('/usr/bin/dpkg-query',['-S','--']),
                            ('/usr/bin/rpm',['-qf','--queryformat','%{NAME}','--'])):
        if not Path(executable).is_file():continue
        result=subprocess.run([executable,*args,str(path)],capture_output=True,text=True,timeout=5)
        if result.returncode==0:return result.stdout.strip().split(': ',1)[0]
        if result.returncode!=1:raise ValueError('Could not verify package ownership.')
    return None


def read_safe(path,uid=0,limit=1024*1024):
    path=Path(path)
    for parent in path.parents:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('Unsafe installed recovery directory.')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1 or info.st_mode & 0o022 or info.st_size>limit:
            raise ValueError('Unsafe installed recovery asset.')
        return stream.read(limit+1)


def write_owned(path,data,mode=0o644):
    path=Path(path)
    # Only fixed destinations chosen by the installed caller reach this function.
    missing=[];parent=path.parent
    while not parent.exists():missing.append(parent);parent=parent.parent
    for ancestor in (parent,*parent.parents):
        info=ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('Unsafe recovery destination directory.')
    for directory in reversed(missing):directory.mkdir(mode=0o755)
    if path.exists() or path.is_symlink():read_safe(path)
    temporary=path.parent/('.monitorize-repair-'+secrets.token_hex(8))
    try:
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def restore_asset(name):
    if name not in TARGETS:raise ValueError('Unsupported recovery asset.')
    manifest=json.loads(read_safe(ASSETS/'manifest.json'))
    data=read_safe(ASSETS/name)
    if hashlib.sha256(data).hexdigest()!=manifest.get(name):raise ValueError('Recovery template integrity check failed.')
    target=TARGETS[name]
    if target.exists() or target.is_symlink():
        if read_safe(target)!=data:
            raise ValueError('The Monitorize profile was modified. Review the conflicting configuration; it was preserved.')
        return False
    package=owner(target)
    if package and package!=owner(ASSETS/name):
        raise ValueError('Recovery template does not belong to the installed package.')
    write_owned(target,data)
    return True


def start_networkmanager(run):
    """Start an intended installed manager, never replace active alternative networking."""
    for unit in ('connman.service','iwd.service','wicd.service','systemd-networkd.service'):
        result=subprocess.run(['/usr/bin/systemctl','is-active','--quiet',unit],capture_output=True,timeout=5)
        if result.returncode==0:raise ValueError('Another network service is active. Network configuration was preserved.')
    load=run(['/usr/bin/systemctl','show','NetworkManager.service','--property=LoadState','--value']).strip()
    if load!='loaded':raise ValueError('NetworkManager is not installed. Install the intended network manager first.')
    enabled=run(['/usr/bin/systemctl','is-enabled','NetworkManager.service']).strip()
    if enabled not in ('enabled','enabled-runtime','static','indirect'):
        raise ValueError('NetworkManager is not the configured network manager. Its enablement was preserved.')
    run(['/usr/bin/systemctl','start','NetworkManager.service'])
    if run(['/usr/bin/systemctl','show','NetworkManager.service','--property=ActiveState','--value']).strip()!='active':
        raise RuntimeError('NetworkManager did not become active.')
    return dict(success=True,message='NetworkManager is available. Refresh and select the saved Wi-Fi profile.')


def recover_host():
    """Use the enrolled maintenance boundary before touching a shared host service."""
    host=Path('/usr/libexec/monitorize-vkms/monitorize-vkms-recovery')
    try:
        if b'PROTOCOL=1' not in read_safe(host):raise ValueError('Unsupported host recovery protocol.')
    except FileNotFoundError:
        return dict(success=False,code='prerequisite_missing',message='The installed VKMS host does not provide recovery protocol 1. Matching host userspace is required.')
    enrolled=Path('/etc/monitorize/remote-login.json')
    if enrolled.exists():
        entry=Path('/usr/libexec/monitorize/remote-login/entry.py')
        if b'setup-recover-host' not in read_safe(entry):
            return dict(success=False,code='prerequisite_missing',message='The installed remote-login service needs matching host recovery support.')
        command=['/usr/bin/python3','-I',str(entry),'setup-recover-host']
    else:
        entry=Path('/usr/libexec/monitorize-vkms/monitorize-vkms-recovery')
        read_safe(entry)
        command=['/usr/bin/python3','-I',str(entry),'recover']
    result=subprocess.run(command,capture_output=True,text=True,check=False)
    try:value=json.loads(result.stdout)
    except ValueError:return dict(success=False,outcome_unknown=True,message='Host recovery did not report its final state. Refresh before retrying.')
    if not isinstance(value,dict) or type(value.get('success')) is not bool:raise ValueError('Invalid host recovery response.')
    if result.returncode:value['success']=False
    return value
