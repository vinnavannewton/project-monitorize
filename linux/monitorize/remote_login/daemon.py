"""Installed system supervisor entry point; no root work on import."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from monitorize.platform.login_capture_probe import find_boot_output
from monitorize.platform.remote_login_identity import identity_paths,acquire_identity_lock
from .settings import read_settings
from .descriptors import DescriptorStore,read_descriptor
from .systemd import EngineUnits
from .logind import LogindSessions
from .service import HandoffService


GREETER_ON_DEMAND_PROTOCOL = 1

def require_installed(path):
    """Reject source checkouts and writable/symlinked ancestors for root code."""
    path=Path(path).absolute()
    for item in (path,*path.parents):
        info=item.lstat()
        if info.st_uid!=0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Remote login requires trusted root-installed code.')


def build_service(settings,bus):
    from .dbus_service import SystemTransport
    require_installed(__file__)
    # Fixed root-installed host package only; no checkout/environment import path.
    sys.path.insert(0,'/usr/lib/monitorize-vkms')
    from monitorize_vkms import helper
    from monitorize_vkms.boot_display import boot_output_status,return_to_greeter,reconnect_reserved,desktop_operation_installed,greeter_operation_installed
    require_installed(helper.__file__)
    transport=SystemTransport(bus)
    paths=identity_paths(settings.directory)
    def free():
        try:
            with acquire_identity_lock(paths,uid=settings.uid):return True
        except BlockingIOError:return False
    def reserved_output():
        lease=boot_output_status(helper.STATE_DIRECTORY) or {}
        return lease.get('connector') or find_boot_output(require_enabled=False) or ('Virtual-0' if lease.get('protected') is True else None)
    # Publish listener identity without enabling capture. EnginePrepare activates
    # the output only for an authenticated launch, including after idle unplug.
    store=DescriptorStore(settings,reserved_output,lambda uid,session:desktop_operation_installed(uid,session,'adopt'),session_environment=helper._active_session)
    def health(phase):
        from .health import read_health
        try:
            name=f'monitorize-remote-login-{phase}@{settings.uid}.service'
            unit=transport.call('/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager','GetUnit',name)
            pid=int(transport.properties(unit,'org.freedesktop.systemd1.Service').get('MainPID',0))
            if pid<=0:return {}
            return read_health(settings.directory/f'runtime/{phase}-health.json',uid=settings.uid,pid=pid,
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        except Exception:return {}
    units=EngineUnits(settings.uid,transport,free,store.write,health_probe=health)
    def prepare_boot():
        if units.running('desktop') or units.running('boot') or not free():
            raise RuntimeError('Previous engine has not exited.')
        result=subprocess.run(['/usr/bin/loginctl','list-sessions','--json=short'],
                              capture_output=True,text=True,check=True,timeout=3)
        sessions=json.loads(result.stdout)
        if any(not isinstance(row,dict) or (row.get('seat')=='seat0' and
               row.get('class') not in ('greeter','manager')) for row in sessions):
            raise RuntimeError('A user session still owns the seat.')
        lock=helper._acquire_lifecycle_lock()
        try:
            lease=boot_output_status(helper.STATE_DIRECTORY)
            if not lease:raise RuntimeError('Boot output reservation is unavailable.')
            # No user session remains; restoring the fixed output is now safe.
            root=helper._configfs_root([]);_,connector=helper._require_bootstrap(root,[],'mon1')
            return_to_greeter(state=helper.STATE_DIRECTORY,generation=lease.get('generation'),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                sessions=sessions,engines_stopped=True,connector=connector)
        finally:os.close(lock)
    units.boot_preparer=prepare_boot
    from .journal import StopJournal
    service=HandoffService(settings.uid,LogindSessions(transport),units,
        journal=StopJournal(settings.uid,Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
    service.model.enabled=settings.enabled
    service.output_name=reserved_output
    def display_timeout():
        try:return read_settings().display_idle_timeout_seconds
        except ValueError:return settings.display_idle_timeout_seconds
    service.display_timeout=display_timeout
    def owner_check(session,generation):
        with service._lock:
            return (service.model.enabled and service.owner is not None and service.owner_session==session
                    and service.model.session==session and service.model.generation==generation
                    and service.model.stopped_session!=session)
    def worker_context():
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        connection=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        transport=SystemTransport(connection)
        return connection,transport,LogindSessions(transport)
    def boot_gate(sender,transport,*,idle=False,initial=False):
        from .engine_gate import EngineGate
        from .greeter_display import greeter_context
        isolate_uid=getattr(settings,'manager','plasmalogin.service')=='gdm.service'
        context=greeter_context(transport,isolate_uid=isolate_uid)
        generation=service.epoch if initial else getattr(service,'boot_generation','')
        if not initial and context!=getattr(service,'boot_owner_context',None):
            raise PermissionError('The boot runner belongs to an earlier greeter.')
        def current(uid,session):
            return greeter_context(transport,isolate_uid=isolate_uid)==context
        gate=EngineGate(settings.uid,transport,
            lambda:dict(uid=settings.uid,session=context['session'],generation=generation),
            lambda session,token:(service.model.enabled and token==generation and
                (initial or (getattr(service,'boot_generation',None)==generation and
                             getattr(service,'boot_owner_context',None)==context))),
            free,session_check=current,phase='boot')
        gate.check(sender,generation,idle=idle)
        return gate,context
    def boot_context(sender):
        connection,transport,_=worker_context()
        try:
            _,context=boot_gate(sender,transport,initial=True)
            import secrets
            with service._lock:
                service.boot_owner_context=context
                service.boot_generation=secrets.token_hex(16)
                return dict(generation=service.boot_generation,session=context['session'],
                            connected=(boot_output_status(helper.STATE_DIRECTORY) or {}).get('state')=='active')
        finally:connection.close()
    service.boot_context=boot_context
    def greeter_display(sender,operation):
        connection,transport,_=worker_context()
        try:
            gate,context=boot_gate(sender,transport,idle=operation=='idle')
            if operation=='check':return dict(valid=True)
            # The host transaction owns the identity lock; do not probe it again.
            def check():gate.check(sender,service.boot_generation)
            def virtual():
                from .wayland_greeter import greeter_compositor,greeter_environment
                session,environment=greeter_environment(settings,context,helper)
                greeter_compositor(settings,context,session,environment,helper)
                def activate(output,remaining):
                    # Resolve environment exclusively from the verified greeter UID.
                    session,environment=greeter_environment(settings,context,helper)
                    if session!=context['session']:raise PermissionError('Greeter session changed.')
                    from .wayland_greeter import activate_greeter
                    return activate_greeter(settings,context,session,environment,output,remaining,helper,check)
                result=greeter_operation_installed(operation,context_check=check,activate=activate)
                check()
                return dict(output_name=result.get('connector') or '',capture='kms',width=1920,height=1080,fps=60)
            from .wayland_greeter import capture_operation
            return capture_operation(settings,context,operation,helper,check,virtual)
        finally:connection.close()
    def engine_display(sender,generation,operation,mode=None,capture_idle=False):
        if operation not in ('prepare','idle','check'):raise ValueError('Unknown display operation.')
        if generation and generation==getattr(service,'boot_generation',None):
            if mode is not None:raise PermissionError('Client mode requests are desktop-only.')
            return greeter_display(sender,operation)
        if operation=='check':raise PermissionError('Invalid boot ownership token.')
        from .engine_gate import EngineGate
        from .policy import session_target
        connection,transport,sessions=worker_context()
        try:
            def selected(uid,session):
                return session_target(sessions.snapshot()['sessions'],uid)==('desktop',session)
            gate=EngineGate(settings.uid,transport,
                lambda:read_descriptor(settings.runtime/'desktop.json',settings.uid),owner_check,free,session_check=selected)
            descriptor=gate.check(sender,generation,idle=operation=='idle')
            options={}
            if mode is not None:
                from .settings import validate_display_mode
                if operation!='prepare':raise ValueError('Mode requests require desktop prepare.')
                requested=validate_display_mode(mode)
                mode=requested if descriptor.get('client_display_mode') else descriptor.get('display_mode',[1920,1080,60])
                options=dict(mode=validate_display_mode(mode),capture_idle=capture_idle)
            result=desktop_operation_installed(settings.uid,descriptor['session'],operation,engines_stopped=operation=='idle',**options)
            gate.check(sender,generation,idle=operation=='idle')
            actual=result.get('mode',[1920,1080,60])
            return dict(output_name=result.get('connector') or reserved_output(),capture='kms',width=actual[0],height=actual[1],fps=actual[2])
        finally:connection.close()
    service.engine_display=engine_display
    def remove_display(sender):
        import time
        connection,transport,sessions=worker_context()
        try:
            caller=sessions.authenticate(sender,settings.uid)
            with service._lock:
                if service.model.session!=caller['session']:raise PermissionError('Desktop session changed.')
                if service.journal is not None:service.journal.save(caller['session'])
                service.model.user_stop(caller['session']);service.ready=False
            fresh=EngineUnits(settings.uid,transport,free,lambda *args:None)
            fresh.stop('desktop')
            deadline=time.monotonic()+15
            while fresh.running('desktop') or not free():
                if time.monotonic()>=deadline:raise RuntimeError('Capture did not exit; output retained.')
                time.sleep(.1)
            sessions.authenticate(sender,settings.uid)
            desktop_operation_installed(settings.uid,caller['session'],'idle',engines_stopped=True)
            return dict(success=True,changed=True,message='Remote-login display removed. Use Start to resume listening.')
        finally:connection.close()
    service.remove_display=remove_display
    return service


def main():
    if os.geteuid()!=0:raise RuntimeError('Run the installed remote-login system service.')
    require_installed(__file__)
    settings=read_settings()
    manager=Path('/etc/systemd/system/display-manager.service')
    if settings.enabled:
        from .display_managers import require_manager
        require_manager(getattr(settings,'manager','plasmalogin.service'))
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    import dbus
    from .dbus_service import ExportedService
    DBusGMainLoop(set_as_default=True)
    bus=dbus.SystemBus(private=True)
    service=build_service(settings,bus);exported=ExportedService(bus,service)
    GLib.timeout_add(500,exported.tick)
    # logind events trigger an earlier reconcile; polling covers missed signals.
    def changed(*args,**kwargs):GLib.idle_add(lambda:(exported.tick(),False)[1])
    bus.add_signal_receiver(changed,dbus_interface='org.freedesktop.login1.Manager',bus_name='org.freedesktop.login1')
    bus.add_signal_receiver(changed,signal_name='PropertiesChanged',dbus_interface='org.freedesktop.DBus.Properties',
                            bus_name='org.freedesktop.login1')
    exported.tick();GLib.MainLoop().run()


if __name__=='__main__':main()
