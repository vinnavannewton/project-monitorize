"""Explicit administrator enrollment; fixed root assets, no live display changes."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from monitorize.platform.login_capture_probe import build_config
from monitorize.platform.remote_login_identity import migrate_identity,identity_paths,acquire_identity_lock,_read_private
from .daemon import require_installed
from .root_files import RootFiles
from .runner import ENGINE,ASSETS,APPS,BOOT_CONFIG
from .settings import SETTINGS,read_settings

NEW_UNITS=('monitorize-vkms-boot-display.service','monitorize-remote-login.service')
OLD_UNITS=('monitorize-login-capture.service','monitorize-remote-login-probe.service')


class Commands:
    def run(self,args):
        result=subprocess.run(args,capture_output=True,text=True,timeout=20,check=False)
        if result.returncode:raise RuntimeError('Remote-login system command failed: '+args[0])
        return result.stdout.strip()
    def enabled(self,name):
        return subprocess.run(['/usr/bin/systemctl','is-enabled','--quiet',name],
            capture_output=True,timeout=5,check=False).returncode==0
    def enable(self,name):self.run(['/usr/bin/systemctl','enable',name])
    def disable(self,name):
        # Missing old experimental units are harmless; installed production units
        # must succeed. No --now here: never unplug a live virtual output.
        result=subprocess.run(['/usr/bin/systemctl','disable',name],capture_output=True,text=True,timeout=10)
        if result.returncode and name not in OLD_UNITS:raise RuntimeError('Could not disable remote-login unit.')
    def reload(self):self.run(['/usr/bin/systemctl','daemon-reload'])


def configure_next_boot(values,hostname,groups,*,files,commands):
    """Transactional config/enablement; no service start or display-manager restart."""
    previous={name:commands.enabled(name) for name in (*NEW_UNITS,*OLD_UNITS)}
    directory=Path('/var/lib/monitorize/remote-login')/str(values['uid'])
    config=build_config(directory,hostname,values['encoder'],values['adapter'])
    replacements={'file_apps':str(APPS),'log_path':str(directory/'logs/boot.log')}
    config='\n'.join((key+' = '+replacements[key] if (key:=line.split('=',1)[0].strip()) in replacements else line)
                     for line in config.splitlines())+'\n'
    try:
        files.write('etc/monitorize/remote-login.json',json.dumps(values))
        files.write('etc/monitorize/remote-login/boot.conf',config)
        group_line='[Service]\nSupplementaryGroups='+' '.join(str(g) for g in groups)+'\n'
        for phase in ('boot','desktop'):
            files.write(f'etc/systemd/system/monitorize-remote-login-{phase}@{values["uid"]}.service.d/10-device-groups.conf',group_line)
        commands.reload()
        for name in OLD_UNITS:commands.disable(name)
        for name in NEW_UNITS:commands.enable(name)
    except Exception:
        try:files.rollback()
        except Exception:pass
        for name,enabled in previous.items():
            try:(commands.enable if enabled else commands.disable)(name)
            except Exception:pass
        try:commands.reload()
        except Exception:pass
        raise RuntimeError('Remote-login setup failed; configuration/enablement rollback was attempted. Identity backup is retained.') from None


def configure_disabled(values,*,files,commands):
    """Roll back failed disablement without restarting stopped capture engines."""
    previous={name:commands.enabled(name) for name in NEW_UNITS}
    try:
        files.write('etc/monitorize/remote-login.json',json.dumps(values))
        for name in NEW_UNITS:commands.disable(name)
        commands.reload()
    except Exception:
        # Restoring enablement affects the next boot only. Never restart capture
        # here: the authenticated desktop owner has already been stopped.
        try:files.rollback()
        except Exception:pass
        for name,enabled in previous.items():
            try:(commands.enable if enabled else commands.disable)(name)
            except Exception:pass
        try:commands.reload()
        except Exception:pass
        raise RuntimeError('Remote-login disable failed; configuration/enablement rollback was attempted. Streaming remains stopped; retry setup before rebooting.') from None


def _no_engines():
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process/'comm').read_text().strip()=='sunshine':
                raise RuntimeError('Stop all Sunshine streams before configuring remote login.')
        except (FileNotFoundError,ProcessLookupError):continue


def _groups():
    paths=[Path('/dev/uinput'),*Path('/dev/dri').glob('card*'),*Path('/dev/dri').glob('renderD*')]
    if not paths[0].exists():raise RuntimeError('Configure uinput access before enabling remote login.')
    group=paths[0].stat().st_gid
    if group==0 and not paths[0].stat().st_mode & 0o002:
        raise RuntimeError('Configure Monitorize input access first; root group access will not be granted.')
    return sorted({path.stat().st_gid for path in paths if path.exists() and path.stat().st_gid!=0})


def provision_identity(source,destination,uid,*,files):
    """Keep a root-written retry receipt separate from configuration rollback.

    Only explicit setup can create the destination in its root-owned parent.
    A retry never overwrites the published identity with a stale source snapshot.
    """
    receipt='etc/monitorize/remote-login-pending.json'
    expected=dict(uid=uid,source=str(source),destination=str(destination))
    previous=files._read(files._path(receipt))
    if destination.exists():
        if previous is None or json.loads(previous[0])!=expected:
            raise RuntimeError('An unrecognized retained identity exists; inspect it before enrolling again.')
        return
    files.write(receipt,json.dumps(expected),0o600)
    migrate_identity(source,destination,uid=uid)




def restore_linger(uid,*,commands):
    files=RootFiles();record='etc/monitorize/remote-login-linger.json'
    previous=files._read(files._path(record))
    if previous is None:return
    values=json.loads(previous[0])
    if values.get('uid')!=uid:raise RuntimeError('Linger receipt belongs to another account.')
    if values.get('changed') is True:commands.run(['/usr/bin/loginctl','disable-linger',pwd.getpwuid(uid).pw_name])
    # Keep an inactive receipt to make repeat disablement safe.
    files.write(record,json.dumps(dict(uid=uid,changed=False,active=False)),0o600)


def enable(uid):
    if type(uid) is not int or uid<=0:raise RuntimeError('Select a non-root account.')
    from monitorize.platform.remote_login_host import host_readiness
    host=host_readiness()
    if not host['available']:raise RuntimeError(host['message'])
    account=pwd.getpwuid(uid)
    from .display_managers import require_manager
    manager=require_manager()
    for path in (ENGINE,ASSETS,APPS,Path('/usr/lib/systemd/system/monitorize-vkms-boot-display.service')):require_installed(path)
    sys.path.insert(0,'/usr/lib/monitorize-vkms')
    from monitorize_vkms import helper
    from monitorize_vkms import boot_display
    require_installed(helper.__file__);require_installed(boot_display.__file__)
    if getattr(helper,'BOOT_DISPLAY_PROTOCOL',0)!=1:
        raise RuntimeError('Upgrade the installed monitorize-vkms helper to boot-display protocol v1 first.')
    if getattr(boot_display,'GREETER_ON_DEMAND_PROTOCOL',0)!=1:
        raise RuntimeError('Install the VKMS host Python update with on-demand greeter protocol v1 first.')
    if getattr(boot_display,'IDLE_DISPLAY_PROTOCOL',0)!=1:
        raise RuntimeError('Upgrade the installed Monitorize VKMS host Python support to idle-display protocol v1 first.')
    from .capability import exact_kms_engine
    if not exact_kms_engine(ENGINE):raise RuntimeError('Install Monitorize Sunshine with the exact KMS output protocol first.')
    _no_engines();groups=_groups();commands=Commands()
    profile=Path(account.pw_dir)/'.config/monitorize/sunshine-1'
    # Read all profile prerequisites before publishing a retained identity.
    source={}
    for line in _read_private(profile/'sunshine.conf',uid).decode().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);source[key.strip()]=value.strip()
    existing=read_settings() if SETTINGS.exists() else None
    if existing is not None and existing.uid!=uid:raise RuntimeError('Remote login is enrolled for another account.')
    # Root-owned parents only. The selected account owns private identity/runtime.
    files=RootFiles()
    parent=Path('/var/lib/monitorize/remote-login')
    files._parents(parent/str(uid))
    directory=parent/str(uid)
    if existing is None:
        provision_identity(profile,directory,uid,files=RootFiles())
    with acquire_identity_lock(identity_paths(directory),uid=uid):
        paths=identity_paths(directory)
        for path in (paths.state,paths.cert,paths.key):_read_private(path,uid)
    for name in ('runtime','logs'):
        path=directory/name
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
        if path.is_symlink() or not path.is_dir():raise RuntimeError('Unsafe identity runtime directory.')
        info=path.lstat()
        if info.st_uid not in (0,uid) or info.st_mode & 0o077:raise RuntimeError('Unsafe identity runtime ownership.')
        os.chown(path,uid,account.pw_gid)
    values=dict(schema_version=1,enabled=True,uid=uid,gid=account.pw_gid,username=account.pw_name,
                home=account.pw_dir,profile=str(profile),manager=manager['manager'],encoder='software',adapter='')
    if existing is not None:values.update(encoder=existing.encoder,adapter=existing.adapter)
    caps=commands.run(['/usr/sbin/getcap','--',str(ENGINE)])
    old_caps=caps.removeprefix(str(ENGINE)).strip()
    changed='cap_sys_admin' not in old_caps
    try:
        if changed:commands.run(['/usr/sbin/setcap',(old_caps+' cap_sys_admin+p').strip(),str(ENGINE)])
        configure_next_boot(values,source.get('sunshine_name','Monitorize'),groups,files=files,commands=commands)
    except Exception:
        if changed:
            try:commands.run(['/usr/sbin/setcap',old_caps if old_caps else '-r',str(ENGINE)])
            except Exception:pass
        raise
    restore_linger(uid,commands=commands)  # Clean up only changes owned by the retired experiment.
    return dict(success=True,reboot_required=True,message='Configured for next boot. Enable the desktop controller, then reboot. Existing probe is disabled; no services were started.')


def disable(uid):
    settings=read_settings()
    if uid!=settings.uid:raise PermissionError('Only the enrolled account can disable remote login.')
    commands=Commands()
    commands.run(['/usr/bin/systemctl','stop','monitorize-remote-login.service',
                  f'monitorize-remote-login-boot@{uid}.service',f'monitorize-remote-login-desktop@{uid}.service'])
    with acquire_identity_lock(identity_paths(settings.directory),uid=uid):pass
    files=RootFiles()
    values=dict(schema_version=1,enabled=False,uid=settings.uid,gid=settings.gid,username=settings.username,
                home=settings.home,profile=settings.profile,manager=getattr(settings,'manager','plasmalogin.service'),encoder=settings.encoder,adapter=settings.adapter)
    configure_disabled(values,files=files,commands=commands)
    restore_linger(uid,commands=commands)
    try:remove_reserved(uid)
    except Exception as error:
        return dict(success=False,partial=True,cleanup_pending=True,reboot_required=False,
                    message='Boot startup is disabled, but display release needs retry: '+str(error))
    return dict(success=True,reboot_required=False,message='Boot startup disabled. Normal streaming is available now. Pairing and Wi-Fi settings are retained.')


def remove_reserved(uid, *, release=False):
    """Administrator recovery only when coordinator and engine cgroups exited."""
    settings=read_settings()
    if uid!=settings.uid:raise PermissionError('Only the enrolled account may remove its display.')
    import dbus
    from dbus.mainloop import NULL_MAIN_LOOP
    from .dbus_service import SystemTransport
    from .systemd import EngineUnits
    bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
    try:
        transport=SystemTransport(bus)
        try:
            unit=transport.call('/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager','GetUnit','monitorize-remote-login.service')
            state=transport.properties(unit,'org.freedesktop.systemd1.Unit')
            if state.get('ActiveState') not in ('inactive','failed'):
                raise RuntimeError('The coordinator is running; use its managed removal command.')
            group=str(transport.properties(unit,'org.freedesktop.systemd1.Service').get('ControlGroup',''))
            if group:
                if not group.startswith('/') or '..' in group.split('/'):raise RuntimeError('Invalid coordinator cgroup.')
                events=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events'
                if events.exists() and 'populated 1' in events.read_text():
                    raise RuntimeError('Coordinator processes have not exited.')
        except dbus.DBusException as error:
            if error.get_dbus_name()!='org.freedesktop.systemd1.NoSuchUnit':raise
        units=EngineUnits(uid,transport,lambda:False,lambda *args:None)
        if units.running('boot') or units.running('desktop'):raise RuntimeError('Remote-login engines must exit before recovery.')
        sys.path.insert(0,'/usr/lib/monitorize-vkms')
        from monitorize_vkms.boot_display import desktop_operation_installed
        require_installed(sys.modules['monitorize_vkms.boot_display'].__file__)
        # The host holds this same identity lock across cgroup verification and
        # disconnect. Reacquiring it here would reject our own recovery call.
        from monitorize_vkms import helper
        session,_=helper._active_session(uid)
        desktop_operation_installed(uid,session,'release' if release or not settings.enabled else 'idle',engines_stopped=True)
        return dict(success=True,changed=True,message='Reserved remote-login display removed. '+('Pairing and reservation retained.' if settings.enabled else 'Slot released for normal streaming; pairing retained.'))
    finally:bus.close()


DESKTOP_FALLBACK_PROTOCOL = 1


def desktop_fallback(uid):
    """Pause managed ownership until reboot; retain enrollment and pairing."""
    settings=read_settings()
    if uid!=settings.uid:raise PermissionError('Only the enrolled account can start desktop fallback.')
    sys.path.insert(0,'/usr/lib/monitorize-vkms')
    from monitorize_vkms import helper
    session,_=helper._active_session(uid)
    commands=Commands()
    units=['monitorize-remote-login.service',f'monitorize-remote-login-boot@{uid}.service',
           f'monitorize-remote-login-desktop@{uid}.service']
    # /run masks disappear at reboot; persistent enablement is untouched.
    commands.run(['/usr/bin/systemctl','mask','--runtime',*units])
    commands.run(['/usr/bin/systemctl','stop',*units])
    for unit in units:
        if commands.run(['/usr/bin/systemctl','show',unit,'--property=LoadState','--value'])!='masked':
            raise RuntimeError('The boot engine could not be paused safely until reboot.')
    # Verifies coordinator cgroups, both managed engines and identity lock before
    # releasing the protected mon1 lease. No boot configuration is rewritten.
    remove_reserved(uid,release=True)
    actual,_=helper._active_session(uid)
    if actual!=session:raise RuntimeError('Desktop session changed during fallback.')
    from monitorize.platform.remote_login_identity import fallback_grant_path
    grant=dict(uid=uid,session=session,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    RootFiles().write(str(fallback_grant_path(uid)).lstrip('/'),json.dumps(grant),0o644)
    return dict(success=True,message='Desktop streaming is available. Start at boot remains enabled for the next reboot.')


def main(action):
    if os.geteuid()!=0:raise RuntimeError('Administrator setup is required.')
    require_installed(__file__)
    raw=os.environ.get('PKEXEC_UID','')
    if not raw.isdigit() or int(raw)<=0:raise RuntimeError('Run setup through Monitorize administrator authorization.')
    try:result={'enable':enable,'disable':disable,'remove':remove_reserved,'fallback':desktop_fallback}[action](int(raw))
    except Exception as error:
        result={'success':False,'message':str(error)}
    print(json.dumps(result));return 0 if result['success'] else 1
