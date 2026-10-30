#!/usr/bin/env python3
"""Authorized, fixed-purpose source setup/cache/legacy cleanup. Never build as root."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
# Last shipped source helper/policy before removal in 75bd542d. Preserve edits
# and unknown historical variants rather than inferring provenance from names.
LEGACY_HASHES={
    'monitorize-source-vkms-helper':'2823f35f83b603526b2b17f1b6830c672ba1d1ab2aa6ece8facb8f4a4c857d5e',
    'io.github.vinnavannewton.monitorize.source-vkms.policy':'0afe78a4a86b4917dac92c63d53d04bd85a8eb1340867632c3665f06585950f9',
}
LEGACY=(Path('/usr/libexec/monitorize/monitorize-source-vkms-helper'),
        Path('/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.source-vkms.policy'))


def source_read(relative,uid):
    if Path(relative).is_absolute() or '..' in Path(relative).parts:raise ValueError('Unsafe source path.')
    flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC
    fd=os.open(ROOT,flags|os.O_DIRECTORY)
    try:
        for component in Path(relative).parts[:-1]:
            following=os.open(component,flags|os.O_DIRECTORY,dir_fd=fd)
            os.close(fd);fd=following
        source=os.open(Path(relative).name,flags|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(source,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1 or info.st_mode & 0o022 or info.st_size>1024*1024:
                raise ValueError('Unsafe source setup file.')
            return stream.read(1024*1024+1)
    finally:os.close(fd)


def provider():
    spec=importlib.util.spec_from_file_location('source_system_recovery',ROOT/'packaging/common/system_recovery.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def bootstrap(uid):
    recovery=provider()
    files={Path('/usr/libexec/monitorize/monitorize-system-setup'):(source_read('packaging/common/monitorize-system-setup',uid),0o755),
           Path('/usr/libexec/monitorize/system_recovery.py'):(source_read('packaging/common/system_recovery.py',uid),0o644),
           Path('/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy'):(source_read('packaging/common/io.github.vinnavannewton.monitorize.system-setup.policy',uid),0o644)}
    manifest={}
    for relative,name in (('packaging/fedora/monitorize.xml','firewalld.xml'),
                          ('packaging/deb/common/monitorize.ufw.profile','ufw.profile'),
                          ('packaging/fedora/70-monitorize-uinput.rules','uinput.rules')):
        data=source_read(relative,uid);manifest[name]=hashlib.sha256(data).hexdigest()
        files[recovery.ASSETS/name]=(data,0o644)
    files[recovery.ASSETS/'manifest.json']=(json.dumps(manifest).encode(),0o644)
    snapshots={}
    for target in files:
        if recovery.owner(target):raise ValueError('Package-owned setup assets cannot be replaced by a source bootstrap.')
        snapshots[target]=(recovery.read_safe(target),stat.S_IMODE(target.stat().st_mode)) if target.exists() or target.is_symlink() else None
    backup=Path('/var/lib/monitorize/setup-backups')/secrets.token_hex(16)
    records=[]
    for index,(target,old) in enumerate(snapshots.items()):
        records.append(dict(path=str(target),exists=old is not None,mode=old[1] if old else None))
        if old:recovery.write_owned(backup/str(index),old[0],0o600)
    recovery.write_owned(backup/'manifest.json',json.dumps(records).encode(),0o600)
    try:
        for target,(data,mode) in files.items():recovery.write_owned(target,data,mode)
    except Exception:
        try:
            for target,old in reversed(list(snapshots.items())):
                if old:recovery.write_owned(target,old[0],old[1])
                else:target.unlink(missing_ok=True)
        except Exception:raise RuntimeError('Setup rollback needs attention. Backup: '+str(backup)) from None
        raise
    return dict(success=True,message='Source system setup is ready.',backup=str(backup))



def mount_id(descriptor):
    rows=Path('/proc/self/fdinfo/'+str(descriptor)).read_text().splitlines()
    return next(int(row.split(':',1)[1]) for row in rows if row.startswith('mnt_id:'))

def repair_cache(uid,gid):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    descriptor=os.open(ROOT,flags)
    original_mount=mount_id(descriptor);count=0
    try:
        for name in ('external','sunshine','node_modules'):
            following=os.open(name,flags,dir_fd=descriptor);os.close(descriptor);descriptor=following
            if mount_id(descriptor)!=original_mount:raise ValueError("Cache crosses a mount boundary.")
        device=os.fstat(descriptor).st_dev
        for _,directories,files,directory_fd in os.fwalk('.',follow_symlinks=False,dir_fd=descriptor):
            if mount_id(directory_fd)!=original_mount:raise ValueError('Cache crosses a mount boundary.')
            entries=['.',*directories,*files]
            for name in entries:
                info=os.stat(name,dir_fd=directory_fd,follow_symlinks=False)
                if info.st_dev!=device or info.st_uid not in (0,uid):raise ValueError('Cache crosses ownership or filesystem boundaries.')
                if stat.S_ISLNK(info.st_mode):continue  # Link ownership does not affect npm access; preserve it.
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
                    raise ValueError('Unsafe generated cache entry.')
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=directory_fd)
                try:
                    current=os.fstat(fd)
                    if mount_id(fd)!=original_mount:raise ValueError('Cache crosses a mount boundary.')
                    if ((current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino) or current.st_uid not in (0,uid)
                            or (stat.S_ISREG(current.st_mode) and current.st_nlink!=1)):
                        raise ValueError('Cache changed during repair.')
                    os.fchown(fd,uid,gid)
                    os.fchmod(fd,((current.st_mode & 0o777)| (0o700 if stat.S_ISDIR(current.st_mode) else 0o600)) & ~0o022)
                finally:os.close(fd)
                count+=1
                if count>200000:raise ValueError('Cache repair exceeds its entry limit.')
        return dict(success=True,message='Generated npm cache ownership repaired.',entries=count)
    except (OSError,ValueError) as error:
        raise ValueError('Cache repair stopped after '+str(count)+' entries: '+str(error)) from error
    finally:os.close(descriptor)


def cleanup_legacy():
    recovery=provider();existing=[]
    for path in LEGACY:
        if not path.exists() and not path.is_symlink():continue
        if recovery.owner(path):raise ValueError('Legacy path is package owned and was preserved.')
        data=recovery.read_safe(path)
        if hashlib.sha256(data).hexdigest()!=LEGACY_HASHES.get(path.name):
            raise ValueError('Legacy file provenance is unknown or modified; it was preserved.')
        existing.append((path,data,stat.S_IMODE(path.stat().st_mode)))
    backup=Path('/var/lib/monitorize/legacy-cleanup')/secrets.token_hex(16)
    for index,(path,data,mode) in enumerate(existing):
        recovery.write_owned(backup/str(index),data,0o600)
    recovery.write_owned(backup/'manifest.json',json.dumps([dict(path=str(p),mode=m) for p,_,m in existing]).encode(),0o600)
    for path,_,_ in existing:path.unlink()
    if any(path.exists() for path in LEGACY):raise RuntimeError('Legacy cleanup remains incomplete.')
    return dict(success=True,message='Legacy source helpers removed. Standalone VKMS support retained.',backup=str(backup))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('bootstrap','cache','legacy-cleanup','uninstall-keep','uninstall-all'))
    parser.add_argument('helper_args',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    try:
        raw=os.environ.get('PKEXEC_UID','')
        if os.geteuid()!=0 or not raw.isdecimal() or int(raw)<=0:raise ValueError('Authorize this source action through Polkit.')
        user=pwd.getpwuid(int(raw));info=ROOT.stat()
        if info.st_uid!=user.pw_uid or info.st_mode & 0o022 or not (ROOT/'.gitmodules').is_file():
            raise ValueError('Select the caller-owned Monitorize checkout.')
        if args.helper_args and args.action!='bootstrap':raise ValueError('Only bootstrap can continue system setup.')
        if args.action.startswith('uninstall-'):
            # Validate the fixed source helper before loading it under the
            # already authorized caller-owned checkout boundary.
            source_read('scripts/uninstall-source.py',user.pw_uid)
            spec=importlib.util.spec_from_file_location('source_uninstall',ROOT/'scripts/uninstall-source.py')
            uninstaller=importlib.util.module_from_spec(spec);spec.loader.exec_module(uninstaller)
            result=uninstaller.system_cleanup(user.pw_uid,args.action.removeprefix('uninstall-'))
        else:
            result=bootstrap(user.pw_uid) if args.action=='bootstrap' else repair_cache(user.pw_uid,user.pw_gid) if args.action=='cache' else cleanup_legacy()
        if args.helper_args:
            if args.action!='bootstrap':raise ValueError('Only bootstrap can continue system setup.')
            options=args.helper_args[1:] if args.helper_args[0]=='--' else args.helper_args
            command=['/usr/bin/python3','-I','/usr/libexec/monitorize/monitorize-system-setup',*options]
            completed=subprocess.run(command,capture_output=True,text=True,check=False)
            print(completed.stdout.strip());return completed.returncode
        print(json.dumps(result));return 0
    except (OSError,RuntimeError,ValueError,KeyError,ImportError,subprocess.SubprocessError) as error:
        print(json.dumps(dict(success=False,message=str(error))));return 1


if __name__=='__main__':raise SystemExit(main())
