#!/usr/bin/env python3
"""Explicit root installation/rollback of staged remote-login assets; no activation."""
import argparse
import json
import mmap
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess

BACKUPS=Path('var/lib/monitorize/remote-login-deployments')
ENGINE='usr/libexec/monitorize/sunshine'
FILES={ENGINE,'usr/libexec/monitorize/monitorize-system-setup',
       'usr/lib/firewalld/services/monitorize.xml','etc/ufw/applications.d/monitorize',
       'usr/libexec/monitorize-vkms/monitorize-vkms-helper',
       'usr/libexec/monitorize-vkms/monitorize-vkms-dbus-service',
       'usr/libexec/monitorize-vkms/monitorize-vkms-authorization',
       'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy',
       'usr/share/dbus-1/interfaces/io.github.vinnavannewton.MonitorizeVkms1.xml',
       'usr/lib/monitorize-vkms/VERSION','usr/share/monitorize-vkms/capabilities.json',
       'usr/libexec/monitorize/remote-login/entry.py',
       'usr/libexec/monitorize/remote-login/package-guard.py',
       'usr/share/monitorize/remote-login/apps.json',
       'usr/share/dbus-1/system.d/io.github.vinnavannewton.MonitorizeRemoteLogin1.conf',
       'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy',
       *('usr/lib/systemd/system/'+name for name in ('monitorize-vkms-boot-display.service',
         'monitorize-remote-login.service','monitorize-remote-login-boot@.service',
         'monitorize-remote-login-desktop@.service'))}


# Fixed obsolete assets only; retained in transactional backups for recovery.
RETIRED_FILES={
    'usr/lib/systemd/system/monitorize-remote-login-persistent@.service',
    *('usr/lib/monitorize-remote-login/monitorize/remote_login/'+name+'.py'
      for name in ('persistent_policy','persistent_service','persistent_runner','persistent_audio')),
}

HOST_FILES={'usr/libexec/monitorize-vkms/monitorize-vkms-authorization',
            'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy',
            'usr/share/dbus-1/interfaces/io.github.vinnavannewton.MonitorizeVkms1.xml',
            'usr/libexec/monitorize-vkms/monitorize-vkms-helper',
            'usr/libexec/monitorize-vkms/monitorize-vkms-dbus-service',
            'usr/lib/monitorize-vkms/VERSION','usr/share/monitorize-vkms/capabilities.json'}


def package_owner(path):
    for executable,args in (('pacman',['-Qoq','--']),('dpkg-query',['-S','--']),('rpm',['-qf','--'])):
        binary=shutil.which(executable)
        if not binary:continue
        result=subprocess.run([binary,*args,str(path)],capture_output=True,text=True,timeout=5,check=False)
        if result.returncode==0:return result.stdout.strip() or executable
        if result.returncode!=1:raise ValueError('Could not verify package ownership; use a native package upgrade.')
    return None


def allowed(relative):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts:return False
    value=path.as_posix()
    return value in FILES or value in RETIRED_FILES or value.startswith('usr/share/monitorize/sunshine/assets/') or (
        value.startswith(('usr/lib/monitorize-remote-login/monitorize/','usr/lib/monitorize-vkms/monitorize_vkms/'))
        and path.suffix=='.py')


def validate_bundle(stage):
    """Fail before changing the host if the prepared bundle is incomplete."""
    stage=Path(stage)
    required=(FILES-HOST_FILES)|{'usr/lib/monitorize-remote-login/monitorize/remote_login/daemon.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/greeter_display.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/display_managers.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/wayland_greeter.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/session_context.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/gdm_context.py',
                   'usr/lib/monitorize-remote-login/monitorize/remote_login/login_primary.py',
                   'usr/share/monitorize/sunshine/assets/web/index.html'}
    for relative in required:
        path=stage/relative
        if not path.is_file() or path.is_symlink():raise ValueError('Missing staged prerequisite: '+relative)
    daemon=stage/'usr/lib/monitorize-remote-login/monitorize/remote_login/daemon.py'
    if 'GREETER_ON_DEMAND_PROTOCOL = 1' not in daemon.read_text():
        raise ValueError('Stage the complete on-demand greeter service Python update.')
    with (stage/ENGINE).open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':raise ValueError('Staged Sunshine is not the built engine.')
        with mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as binary:
            if any(binary.find(marker)<0 for marker in (b'MONITORIZE_CONTROL_PROTOCOL=1',b'MONITORIZE_STRICT_KMS_OUTPUT_MISSING')):
                raise ValueError('Staged Sunshine lacks exact KMS output support.')
    staged=stage/'usr/lib/monitorize-vkms/monitorize_vkms'
    host=staged if staged.exists() else Path('/usr/lib/monitorize-vkms/monitorize_vkms')
    if not (host/'boot_display.py').is_file() or 'BOOT_DISPLAY_PROTOCOL = 1' not in (host/'helper.py').read_text():
        raise ValueError('Stage or install the VKMS host Python update with boot-display protocol v1.')
    if 'GREETER_ON_DEMAND_PROTOCOL = 1' not in (host/'boot_display.py').read_text():
        raise ValueError('Stage or install the VKMS host Python update with on-demand greeter protocol v1.')
    if 'IDLE_DISPLAY_PROTOCOL = 1' not in (host/'boot_display.py').read_text():
        raise ValueError('Stage or install the VKMS host Python update with idle-display protocol v1.')
    if staged.exists():
        for relative in HOST_FILES:
            if not (stage/relative).is_file():raise ValueError('Stage the matching host helpers, VERSION and capabilities manifest together.')
        package=stage/'usr/lib/monitorize-vkms/monitorize_vkms/helper.py'
        executable=stage/'usr/libexec/monitorize-vkms/monitorize-vkms-helper'
        if package.read_bytes()!=executable.read_bytes():raise ValueError('Staged host helper copies do not match.')
        version=(stage/'usr/lib/monitorize-vkms/VERSION').read_text().strip()
        manifest=json.loads((stage/'usr/share/monitorize-vkms/capabilities.json').read_text())
        if manifest!=dict(schema_version=1,version=version,remote_login_protocols=dict(boot_display=1,greeter_on_demand=1,idle_display=1)):
            raise ValueError('Staged host capabilities manifest does not match its protocols.')


class Commands:
    def capabilities(self,path):
        result=subprocess.run(['/usr/sbin/getcap','--',str(path)],capture_output=True,text=True,check=True,timeout=5)
        return result.stdout.strip().removeprefix(str(path)).strip()
    def set_capabilities(self,path,value):
        subprocess.run(['/usr/sbin/setcap',value or '-r',str(path)],capture_output=True,check=True,timeout=5)
    def reload(self):
        subprocess.run(['/usr/bin/systemctl','daemon-reload'],capture_output=True,check=True,timeout=20)


class Installer:
    def __init__(self,root=Path('/'),*,owner_uid=0,commands=None):
        self.root=Path(root);self.owner_uid=owner_uid;self.commands=commands or Commands()
    def parents(self,path):
        current=self.root
        for part in path.relative_to(self.root).parts[:-1]:
            current=current/part
            try:current.mkdir(mode=0o755);current.chmod(0o755)
            except FileExistsError:pass
            info=current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.owner_uid or info.st_mode & 0o022:
                raise ValueError('Unsafe installed asset directory.')
    def read(self,path,*,owner=None):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode & 0o022 or
                info.st_size>512*1024*1024 or (owner is not None and info.st_uid!=owner)):
                raise ValueError('Unsafe asset file.')
            return stream.read(),stat.S_IMODE(info.st_mode)
    def write(self,path,data,mode):
        self.parents(path)
        temporary=path.parent/('.monitorize-deploy-'+secrets.token_hex(8))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
            with os.fdopen(fd,'wb') as stream:
                os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
    def assert_unmanaged(self,relatives):
        anchors=set()
        for relative in relatives:
            if relative in HOST_FILES or relative.startswith('usr/lib/monitorize-vkms/'):
                anchors.add('usr/lib/monitorize-vkms/monitorize_vkms/helper.py')
            else:
                anchors.update((ENGINE,'usr/libexec/monitorize/remote-login/entry.py'))
        for anchor in anchors:
            if package_owner(self.root/anchor):
                raise ValueError('System assets belong to a package; use a native package upgrade or rollback instead of replacing package-owned files.')
    def assert_rollback_quiescent(self):
        for relative,key in (('etc/monitorize/remote-login.json','enabled'),
                             ('run/monitorize-vkms/primary.json','protected')):
            path=self.root/relative
            for parent in path.parents:
                if not parent.is_relative_to(self.root):break
                try:info=parent.lstat()
                except FileNotFoundError:continue
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.owner_uid or info.st_mode & 0o022:
                    raise ValueError('Unsafe remote-login policy directory.')
            try:raw,_=self.read(path,owner=self.owner_uid)
            except FileNotFoundError:continue
            if len(raw)>65536:raise ValueError('Remote-login policy exceeds its size bound.')
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('Invalid remote-login policy.')
            if key=='enabled' and type(value.get(key)) is not bool:raise ValueError('Invalid enrollment state.')
            if value.get(key) is True:
                raise ValueError('Disable remote login and release its protected display before explicit asset rollback. Pairing is retained.')
    def install(self,stage,*,host_only=False):
        stage=Path(stage)
        if stage.is_symlink() or not stage.is_dir():raise ValueError('Invalid staging tree.')
        sources=[]
        for source in sorted(stage.rglob('*')):
            if source.is_symlink():raise ValueError('Staged symlinks are not allowed.')
            if source.is_dir():continue
            relative=source.relative_to(stage).as_posix()
            if not allowed(relative) or relative in RETIRED_FILES:raise ValueError('Unexpected staged asset: '+relative)
            if host_only and relative not in HOST_FILES and not relative.startswith('usr/lib/monitorize-vkms/'):
                continue
            # Read before mutating destinations; never execute staged scripts.
            data,_=self.read(source)
            sources.append((relative,data))
        if not sources:raise ValueError('The staging tree is empty.')
        self.assert_unmanaged(relative for relative,_ in sources)
        snapshots=[]
        for relative,data in sources:
            target=self.root/relative;self.parents(target)
            try:old,mode=self.read(target,owner=self.owner_uid)
            except FileNotFoundError:old=None;mode=None
            caps=self.commands.capabilities(target) if relative==ENGINE and old is not None else ''
            snapshots.append((relative,old,mode,caps))
        for relative in sorted(RETIRED_FILES if not host_only else ()):
            target=self.root/relative
            if not target.exists() and not target.is_symlink():continue
            self.parents(target)
            old,mode=self.read(target,owner=self.owner_uid)
            snapshots.append((relative,old,mode,''))
        backup=self.root/BACKUPS/secrets.token_hex(16)
        self.parents(backup/'manifest.json');backup.chmod(0o700)
        manifest=[]
        for index,(relative,old,mode,caps) in enumerate(snapshots):
            if old is not None:self.write(backup/str(index),old,0o600)
            manifest.append(dict(path=relative,exists=old is not None,mode=mode,caps=caps))
        self.write(backup/'manifest.json',json.dumps(manifest).encode(),0o600)
        try:
            for relative,data in sources:
                executable=relative==ENGINE or relative=='usr/libexec/monitorize/monitorize-system-setup' or relative.startswith('usr/libexec/monitorize-vkms/')
                self.write(self.root/relative,data,0o755 if executable else 0o644)
            if any(relative==ENGINE for relative,_ in sources):
                self.commands.set_capabilities(self.root/ENGINE,'cap_sys_admin+p')
            for relative in (RETIRED_FILES if not host_only else ()):
                (self.root/relative).unlink(missing_ok=True)
            self.commands.reload()
        except Exception:
            try:self.rollback(backup,failed_install=True)
            except Exception:
                raise RuntimeError('Asset installation failed and rollback needs attention. Backup: '+str(backup)) from None
            raise RuntimeError('Asset installation failed; files were rolled back. Backup: '+str(backup)) from None
        return backup
    def rollback(self,backup,*,failed_install=False):
        backup=Path(backup).absolute()
        if backup.parent!=self.root/BACKUPS or len(backup.name)!=32 or any(c not in '0123456789abcdef' for c in backup.name):
            raise ValueError('Use the root-protected deployment backup path.')
        self.parents(backup/'manifest.json')
        manifest=json.loads(self.read(backup/'manifest.json',owner=self.owner_uid)[0])
        if not isinstance(manifest,list) or len(manifest)>10000:raise ValueError('Invalid rollback manifest.')
        if not failed_install:
            self.assert_rollback_quiescent()
            if any(not isinstance(item,dict) or not allowed(item.get('path','')) for item in manifest):
                raise ValueError('Invalid rollback path.')
            self.assert_unmanaged(item['path'] for item in manifest)
        replacements=[]
        for index,item in enumerate(manifest):
            if not isinstance(item,dict) or not allowed(item.get('path','')):raise ValueError('Invalid rollback path.')
            target=self.root/item['path'];self.parents(target)
            if target.exists() or target.is_symlink():self.read(target,owner=self.owner_uid)
            old=self.read(backup/str(index),owner=self.owner_uid)[0] if item['exists'] else None
            mode=item['mode']
            if old is not None and (type(mode) is not int or mode & ~0o777 or mode & 0o022):raise ValueError('Invalid rollback permissions.')
            replacements.append((target,old,mode,item.get('caps','')))
        for target,old,mode,caps in reversed(replacements):
            if old is None:target.unlink(missing_ok=True)
            else:
                self.write(target,old,mode)
                if target==self.root/ENGINE and caps:self.commands.set_capabilities(target,caps)
        self.commands.reload()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--stage',type=Path);group.add_argument('--rollback',type=Path)
    parser.add_argument('--host-only',action='store_true',help='Update only source-managed VKMS host userspace from a fully validated bundle; app assets must be installed through their native package.')
    args=parser.parse_args()
    if args.host_only and not args.stage:parser.error('--host-only requires --stage')
    if os.geteuid()!=0:parser.exit(1,'Run this explicit asset installation with sudo.\n')
    try:
        # Do not replace executable/code while an engine or supervisor is alive.
        for process in Path('/proc').glob('[0-9]*'):
            try:
                if (process/'comm').read_text().strip()=='sunshine':
                    raise RuntimeError('Stop Sunshine and the login-capture experiment before installing assets.')
            except (FileNotFoundError,ProcessLookupError):pass
        for unit in ('monitorize-remote-login.service','monitorize-vkms.service'):
            result=subprocess.run(['/usr/bin/systemctl','is-active','--quiet',unit],timeout=5,check=False)
            if result.returncode==0:raise RuntimeError('Stop '+unit+' before replacing its code.')
        installer=Installer()
        if args.stage:
            if not any((Path(directory)/'monitorize-vkms-bootstrap.service').is_file() for directory in
                       ('/etc/systemd/system','/usr/lib/systemd/system')):
                raise RuntimeError('Install the Monitorize VKMS host/bootstrap first.')
            validate_bundle(args.stage)
            backup=installer.install(args.stage,host_only=args.host_only)
            print('Installed assets only. No services enabled/started and no display changed.')
            print('Rollback: sudo /usr/bin/python3 -I '+str(Path(__file__).resolve())+' --rollback '+str(backup))
            print('Now launch Monitorize and explicitly enroll Remote login at boot in Settings.')
        else:
            installer.rollback(args.rollback);print('Asset rollback completed. Enrollment/identity and Wi-Fi are retained.')
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
