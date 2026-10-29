"""Explicit, recoverable live enrollment using fixed installed root assets."""
from contextlib import contextmanager
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from .root_files import RootFiles

LIVE_ENROLLMENT_PROTOCOL = 1


def config_paths(uid):
    return {'etc/monitorize/remote-login.json', 'etc/monitorize/remote-login/boot.conf',
        *(f'etc/systemd/system/monitorize-remote-login-{phase}@{uid}.service.d/10-device-groups.conf'
          for phase in ('boot','desktop')), f'run/monitorize/desktop-fallback/{uid}.json'}


@contextmanager
def setup_lock(*, root=Path('/'), owner_uid=0):
    files=RootFiles(root,owner_uid);path=files._path('run/monitorize-remote-login-setup.lock')
    files._parents(path)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid
                or info.st_mode&0o077 or info.st_nlink!=1):
            raise ValueError('Unsafe remote-login setup lock.')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


class SetupReceipt:
    def __init__(self,uid,*,root=Path('/'),owner_uid=0):
        if type(uid) is not int or uid<=0:raise ValueError('Invalid enrolled account.')
        self.uid=uid;self.files=RootFiles(root,owner_uid)
        self.relative=f'var/lib/monitorize/remote-login-setup/{uid}.json'
        self.path=self.files._path(self.relative)
        self.files._parents(self.path)

    def validate(self,value):
        if (not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('uid')!=self.uid
            or not isinstance(value.get('boot_id'),str) or not value['boot_id']
            or not isinstance(value.get('session'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['session'])
            or not isinstance(value.get('generation'),str) or not re.fullmatch(r'[0-9a-f]{32}',value['generation'])
            or value.get('stage') not in ('preparing','configured','reserved','starting','waiting_for_controller','complete','rolled_back','cleanup_pending')
            or not isinstance(value.get('files'),dict) or set(value['files'])-config_paths(self.uid)
            or not isinstance(value.get('system'),dict)):
            raise ValueError('Unsafe live-enrollment receipt.')
        for flag in ('maintenance_required','maintenance_started'):
            if type(value['system'].get(flag,False)) is not bool:
                raise ValueError('Unsafe setup maintenance state.')
        names={'monitorize-vkms-boot-display.service','monitorize-remote-login.service',
               'monitorize-login-capture.service','monitorize-remote-login-probe.service',
               *(f'monitorize-remote-login-{p}@{self.uid}.service' for p in ('boot','desktop'))}
        system=value['system']
        if type(system.get('enrolled',False)) is not bool:raise ValueError('Unsafe enrollment snapshot.')
        for key in ('units','active'):
            entries=system.get(key,{})
            if not isinstance(entries,dict) or set(entries)-names or any(type(flag) is not bool for flag in entries.values()):
                raise ValueError('Unsafe unit snapshot.')
        if (not isinstance(system.get('masks',[]),list) or set(system.get('masks',[]))-names
            or not isinstance(system.get('caps',''),str) or not re.fullmatch(r'[a-z0-9_=,+ ]{0,1024}',system.get('caps',''))):
            raise ValueError('Unsafe system snapshot.')
        for item in value['files'].values():
            if item is not None:
                if (not isinstance(item,dict) or set(item)!={'data','mode'} or type(item['mode']) is not int
                    or item['mode']&~0o777 or not isinstance(item['data'],str)):
                    raise ValueError('Unsafe configuration snapshot.')
                if len(base64.b64decode(item['data'],validate=True))>16384:
                    raise ValueError('Oversized configuration snapshot.')
        return value

    def load(self):
        previous=self.files._read(self.path)
        if previous is None:return None
        if previous[1]&0o077:raise ValueError('Live-enrollment receipt is not private.')
        return self.validate(json.loads(previous[0]))

    def save(self,value):
        self.validate(value)
        data=json.dumps(value,allow_nan=False)
        if len(data.encode())>65536:raise ValueError('Oversized live-enrollment receipt.')
        self.files.write(self.relative,data,0o600)

    def restore(self,value):
        self.validate(value)
        for relative,item in reversed(list(value['files'].items())):
            path=self.files._path(relative)
            self.files._read(path)  # Refuse a replaced symlink/unsafe destination.
            if item is None:path.unlink(missing_ok=True)
            else:
                self.files._parents(path)
                self.files._write(path,base64.b64decode(item['data'],validate=True),item['mode'])


class TransactionFiles(RootFiles):
    def __init__(self,journal):
        self.journal=journal
        super().__init__(journal.root,journal.owner_uid)

    def write(self,relative,data,mode=0o644):
        if relative not in config_paths(self.journal.uid):raise ValueError('Unexpected live setup destination.')
        if relative not in self.journal.value['files']:
            previous=self._read(self._path(relative))
            self.journal.value['files'][relative]=(None if previous is None else
                dict(data=base64.b64encode(previous[0]).decode(),mode=previous[1]))
            self.journal.save()
        super().write(relative,data,mode)

    def rollback(self):
        # Whole-transaction orchestration verifies process exit before restoring.
        pass


class LiveEnrollment:
    def __init__(self,backend,*,root=Path('/'),owner_uid=0):
        self.backend=backend;self.root=Path(root);self.owner_uid=owner_uid

    def enable(self,uid):
        self.uid=uid;backend=self.backend
        backend.preflight(uid);session=backend.context(uid);self.session=session
        self.receipt=SetupReceipt(uid,root=self.root,owner_uid=self.owner_uid)
        previous=self.receipt.load()
        if backend.existing(uid,session):
            if previous and previous['stage'] not in ('complete','rolled_back'):
                self.value=previous
                if previous['boot_id']==backend.boot_id and previous['session']==session:
                    backend.verify_receipt(uid,session,previous['generation'])
                    self.stage('waiting_for_controller')
                else:
                    # A verified healthy current lifecycle supersedes an old
                    # receipt; never replay its mutations in this boot/session.
                    self.stage('complete')
            return {**self.result(),'reused':True}
        if previous and previous['stage'] not in ('complete','rolled_back'):
            if previous['boot_id']!=backend.boot_id or previous['session']!=session:
                return dict(success=False,configured=True,activated=False,partial=True,cleanup_pending=True,
                    reboot_required=False,message='A previous setup belongs to another boot or session. Inspect ownership before retrying.')
            self.value=previous
        else:
            system=backend.snapshot(uid,session)
            self.value=dict(schema_version=1,uid=uid,boot_id=backend.boot_id,session=session,
                generation=(system.get('lease') or {}).get('generation') or secrets.token_hex(16),
                stage='preparing',files={},system=system)
            self.save()
        try:
            backend.configure(uid,self);self.stage('configured')
            backend.verify(uid,session)
            backend.reserve(uid,session,self.value['generation']);self.stage('reserved')
            self.stage('starting');backend.activate(uid,self)
            backend.verify(uid,session);self.stage('waiting_for_controller')
            return self.result()
        except Exception as error:
            try:
                backend.verify(uid,session)
                backend.rollback(uid,self)
                self.receipt.restore(self.value)
                if hasattr(backend,'restored'):backend.restored(uid,self)
                self.stage('rolled_back')
                return dict(success=False,configured=self.value['system'].get('enrolled',False),activated=False,partial=False,cleanup_pending=False,
                    reboot_required=False,message='Live remote-login setup failed ('+type(error).__name__+'); prior settings restored. Retry setup.')
            except Exception:
                self.stage('cleanup_pending')
                return dict(success=False,configured=True,activated=False,partial=True,cleanup_pending=True,
                    reboot_required=False,message='Remote login is saved, but setup recovery needs attention. Inspect the remote-login journal before retrying.')

    def save(self):self.receipt.save(self.value)
    def stage(self,name):self.value['stage']=name;self.save()
    def result(self):
        return dict(success=True,configured=True,activated=False,uid=self.uid,session=self.session,activation_state='waiting_for_controller',
                    reboot_required=False,message='Remote login is configured; starting the desktop listener.')


class InstalledBackend:
    def __init__(self):
        from .enrollment import Commands
        self.commands=Commands()
        self.boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def preflight(self,uid):
        from .daemon import require_installed
        from monitorize.platform.remote_login_host import host_readiness
        from .display_managers import require_manager
        require_installed(__file__)
        ready=host_readiness(require_live=True)
        if not ready['available']:raise RuntimeError(ready['message'])
        self.manager=require_manager()
        sys.path.insert(0,'/usr/lib/monitorize-vkms')
        from monitorize_vkms import helper, boot_display, boot_runtime
        for module in (helper,boot_display,boot_runtime):require_installed(module.__file__)
        self.host=boot_display;self.runtime=boot_runtime;self.helper=helper
        from .settings import SETTINGS,read_settings
        if SETTINGS.exists():
            settings=read_settings()
            if settings.uid!=uid or settings.manager!=self.manager['manager']:
                raise PermissionError('Enrollment account or display manager changed.')
        root=helper._configfs_root([])
        helper._require_bootstrap(root,[],'mon1')

    def context(self,uid):return self.runtime.desktop_context(uid)

    def status(self):
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        from .dbus_service import NAME,PATH,INTERFACE
        bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        try:
            interface=dbus.Interface(bus.get_object(NAME,PATH,introspect=False),INTERFACE)
            return json.loads(interface.GetStatus(timeout=5))
        finally:bus.close()

    def existing(self,uid,session):
        from .settings import SETTINGS,read_settings
        value=self.host.boot_output_status(self.helper.STATE_DIRECTORY)
        if value is None or not SETTINGS.exists() or not read_settings().enabled:return False
        if value['uid']!=uid or value['session']!=session or value['boot_id']!=self.boot_id:return False
        # The boot entry validates exact EDID and connector identity read-only.
        if value['state'] not in ('active','reserved'):return False
        self.host.main()
        state=self.commands.run(['/usr/bin/systemctl','show','monitorize-remote-login.service','--property=ActiveState','--value'])
        if state!='active':return False
        status=self.status()
        if status.get('uid')!=uid or status.get('session')!=session or not status.get('enabled'):
            raise PermissionError('Running supervisor belongs to another ownership context.')
        if status.get('state') in ('error','disabled','suspended'):
            return False  # The owned idle service can be quiesced transactionally.
        return True

    def snapshot(self,uid,session):
        from .enrollment import NEW_UNITS,OLD_UNITS,_no_engines,ENGINE
        from .settings import SETTINGS,read_settings
        units={name:self.commands.enabled(name) for name in (*NEW_UNITS,*OLD_UNITS)}
        active={name:self.commands.run(['/usr/bin/systemctl','show',name,'--property=ActiveState','--value'])=='active'
                for name in NEW_UNITS}
        if active[NEW_UNITS[-1]]:
            status=self.status()
            if status.get('uid')!=uid or status.get('session')!=session:
                raise PermissionError('Running remote-login service belongs to another context.')
        caps=self.commands.run(['/usr/sbin/getcap','--',str(ENGINE)]).removeprefix(str(ENGINE)).strip()
        masks=self.fallback_masks(uid,session)
        if masks:
            units.update({name:self._fallback_enablement[name] for name in masks})
        value=self.host.boot_output_status(self.helper.STATE_DIRECTORY)
        if value is not None and (value['uid']!=uid or value['boot_id']!=self.boot_id or value['session']!=session):
            raise PermissionError('Reserved display belongs to another lifecycle.')
        return dict(units=units,active=active,caps=caps,masks=masks,lease=value,
                    enrolled=bool(SETTINGS.exists() and read_settings().enabled),
                    maintenance_required=any(active.values()))

    def fallback_masks(self,uid,session):
        from monitorize.platform.remote_login_identity import fallback_grant_path
        names=['monitorize-remote-login.service',*(f'monitorize-remote-login-{p}@{uid}.service' for p in ('boot','desktop'))]
        masked=[name for name in names if self.commands.run(['/usr/bin/systemctl','show',name,'--property=LoadState','--value'])=='masked']
        if not masked:return []
        path=fallback_grant_path(uid);files=RootFiles();files._parents(path)
        previous=files._read(path)
        grant=json.loads(previous[0]) if previous else {}
        if (grant.get('schema_version')!=1 or grant.get('uid')!=uid or grant.get('session')!=session
            or grant.get('boot_id')!=self.boot_id or not isinstance(grant.get('owned_runtime_masks'),list)
            or set(grant['owned_runtime_masks'])-set(names) or set(masked)-set(grant['owned_runtime_masks'])
            or not isinstance(grant.get('unit_enablement'),dict) or set(grant['unit_enablement'])!=set(names)
            or any(type(flag) is not bool for flag in grant['unit_enablement'].values())):
            raise PermissionError('Runtime masks have no verified feature ownership; explicit recovery is required.')
        for name in masked:
            target=Path('/run/systemd/system')/name
            info=target.lstat()
            if not stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or os.readlink(target)!='/dev/null':
                raise PermissionError('Administrator unit mask must not be removed by setup.')
        self._fallback_enablement=grant['unit_enablement']
        return masked

    def maintenance(self,journal):
        from .package_lifecycle import PackageLifecycle,InstalledBackend
        return PackageLifecycle(InstalledBackend())

    def configure(self,uid,journal):
        from .enrollment import enable,_no_engines
        if journal.value['system'].get('maintenance_required'):
            lifecycle=self.maintenance(journal)
            if lifecycle.load() is not None:
                raise RuntimeError('Remote-login maintenance is pending. Resume recovery before enabling startup.')
            journal.value['system']['maintenance_started']=True;journal.save()
            # The caller already holds setup_lock; invoke the lifecycle directly,
            # never a package-guard subprocess that would acquire the lock again.
            lifecycle.prepare('upgrade',include_idle_services=True)
            # Keep the receipt as an admission gate while enabling unit files.
            # systemctl enable cannot operate on runtime-masked unit fragments.
            maintenance=lifecycle.load()
            if maintenance is not None:lifecycle.backend.unmask(maintenance['masks'])
        _no_engines()
        masks=journal.value['system']['masks']
        if masks:self.commands.run(['/usr/bin/systemctl','unmask','--runtime',*masks])
        enable(uid,files=TransactionFiles(journal),commands=self.commands)

    def reserve(self,uid,session,generation):
        return self.host.reserve_desktop_installed(uid,session,generation=generation)

    def activate(self,uid,journal):
        from monitorize.platform.remote_login_identity import fallback_grant_path
        grant=fallback_grant_path(uid)
        if grant.exists():
            files=TransactionFiles(journal);relative=str(grant).lstrip('/')
            files.write(relative,files._read(grant)[0],0o644)
            grant.unlink()
        if journal.value['system'].get('maintenance_started'):
            self.maintenance(journal).resume()
        for name in ('monitorize-vkms-boot-display.service','monitorize-remote-login.service'):
            self.commands.run(['/usr/bin/systemctl','start',name])
        status=self.status()
        if status.get('uid')!=uid or status.get('session')!=journal.value['session'] or not status.get('enabled'):
            raise RuntimeError('Supervisor did not acquire the enrolled desktop context.')

    def verify(self,uid,session):
        if self.context(uid)!=session:raise PermissionError('Enrolled desktop session changed.')

    def verify_receipt(self,uid,session,generation):
        current=self.host.boot_output_status(self.helper.STATE_DIRECTORY)
        if (current is None or current['generation']!=generation or current['uid']!=uid
                or current['session']!=session or current['boot_id']!=self.boot_id):
            raise PermissionError('Live setup receipt does not own the current reservation.')

    def rollback(self,uid,journal):
        system=journal.value['system']
        if system.get('maintenance_started'):
            lifecycle=self.maintenance(journal)
            maintenance=lifecycle.load()
            if maintenance is not None:
                if maintenance['uid']!=uid or maintenance['boot_id']!=self.boot_id:
                    raise PermissionError('Maintenance ownership changed during rollback.')
                # Restore enablement while keeping admission blocked. Resume only
                # after the journal has restored the prior configuration.
                lifecycle.backend.unmask(maintenance['masks'])
        if journal.value['stage'] in ('starting','waiting_for_controller','cleanup_pending'):
            for name in ('monitorize-remote-login.service','monitorize-vkms-boot-display.service'):
                if system.get('maintenance_started') or not system.get('active',{}).get(name):self.commands.run(['/usr/bin/systemctl','stop',name])
            self.runtime.verify_quiescent(uid)
        current=self.host.boot_output_status(self.helper.STATE_DIRECTORY)
        if system.get('lease') is None and current is not None:
            self.host.release_live_desktop_installed(uid,journal.value['session'],generation=journal.value['generation'])
        if system['masks']:self.commands.run(['/usr/bin/systemctl','unmask','--runtime',*system['masks']])
        for name,enabled in system['units'].items():
            (self.commands.enable if enabled else self.commands.disable)(name)
        from .enrollment import ENGINE
        self.commands.run(['/usr/sbin/setcap',system['caps'] or '-r',str(ENGINE)])
        if system['masks']:self.commands.run(['/usr/bin/systemctl','mask','--runtime',*system['masks']])

    def restored(self,uid,journal):
        self.commands.reload()
        if journal.value['system'].get('maintenance_started'):
            self.maintenance(journal).resume()
            from .settings import read_settings
            if read_settings().enabled:
                for name,was_active in journal.value['system'].get('active',{}).items():
                    if was_active:self.commands.run(['/usr/bin/systemctl','start',name])


def enable_live(uid):
    return LiveEnrollment(InstalledBackend()).enable(uid)


def complete_for_session(uid,session):
    """Called only by the root supervisor after verified current listener health."""
    if not (Path('/var/lib/monitorize/remote-login-setup')/(str(uid)+'.json')).exists():return
    receipt=SetupReceipt(uid)
    value=receipt.load()
    if value is None or value['stage']=='complete':return
    if value['stage'] not in ('starting','waiting_for_controller'):return
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if value['session']!=session or value['boot_id']!=boot:return
    with setup_lock():
        value=receipt.load()
        if (value is None or value['stage'] not in ('starting','waiting_for_controller')
                or value['session']!=session or value['boot_id']!=boot):return
        backend=InstalledBackend();backend.preflight(uid)
        if backend.context(uid)!=session:return
        current=backend.host.boot_output_status(backend.helper.STATE_DIRECTORY)
        if (current is None or current['generation']!=value['generation'] or current['uid']!=uid
                or current['session']!=session or current['boot_id']!=boot):return
        value['stage']='complete';receipt.save(value)
