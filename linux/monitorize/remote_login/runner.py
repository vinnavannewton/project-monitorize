"""Fixed installed non-root boot/desktop engine runners."""
import json
import os
from pathlib import Path
import signal
import secrets
import subprocess
import threading
import time
from monitorize.platform.login_capture_probe import find_boot_output,read_phase
from monitorize.platform.remote_login_identity import identity_paths
from .boot_host import BootHost
from monitorize.platform.sunshine_ports import SUNSHINE_BASE_PORT
from .daemon import require_installed
from .descriptors import read_descriptor
from .engine import EngineOwner,RetrySchedule
from .health import inspect_startup,write_health,rotate_startup_log,ListenerEvidence,startup_failure
from .settings import read_settings,validate_descriptor

ENGINE=Path('/usr/libexec/monitorize/sunshine')
ASSETS=Path('/usr/share/monitorize/sunshine/assets')
BOOT_CONFIG=Path('/etc/monitorize/remote-login/boot.conf')
APPS=Path('/usr/share/monitorize/remote-login/apps.json')


def own_session(session,uid):
    result=subprocess.run(['/usr/bin/loginctl','show-session',session,'--no-pager',
        '--property=User','--property=Active','--property=Remote','--property=Type','--property=Class','--property=Seat'],
        check=True,capture_output=True,text=True,timeout=3)
    values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    return (values.get('User')==str(uid) and values.get('Active')=='yes' and
            values.get('Remote')=='no' and values.get('Type') in ('wayland','x11') and
            values.get('Class') in ('user','user-early','user-incomplete') and values.get('Seat')=='seat0')


def preflight():
    settings=read_settings()
    if not settings.enabled or os.geteuid()==0 or os.geteuid()!=settings.uid:
        raise RuntimeError('Engine runner must use the configured non-root account.')
    require_installed(__file__);require_installed(ENGINE);require_installed(ASSETS);require_installed(APPS)
    from .capability import exact_kms_engine
    if not exact_kms_engine(ENGINE):raise RuntimeError('Install Monitorize Sunshine with the exact KMS output protocol before enabling remote login.')
    if not os.access('/dev/uinput',os.R_OK|os.W_OK):raise RuntimeError('Selected account cannot access /dev/uinput; rerun input setup.')
    caps=subprocess.run(['/usr/sbin/getcap','--',str(ENGINE)],check=True,capture_output=True,text=True,timeout=3)
    if 'cap_sys_admin' not in caps.stdout:raise RuntimeError('Installed Sunshine lacks KMS capability; rerun remote-login setup.')
    for directory in (settings.directory/'runtime',settings.directory/'logs'):
        directory.mkdir(mode=0o700,exist_ok=True)
        info=directory.lstat()
        if not directory.is_dir() or directory.is_symlink() or info.st_uid!=settings.uid or info.st_mode & 0o077:
            raise RuntimeError('Engine runtime/log directory is not private.')
    return settings


def _stopping():
    event=threading.Event()
    for number in (signal.SIGTERM,signal.SIGINT):signal.signal(number,lambda *_:event.set())
    return event


def _offset(log):
    try:return log.stat().st_size
    except FileNotFoundError:return 0


def boot_main():
    settings=preflight();require_installed(BOOT_CONFIG)
    from .idle import IdleDisplay
    from .control import ControlServer
    from .dbus_service import NAME,PATH,INTERFACE
    owner=EngineOwner(identity_paths(settings.directory));stop=_stopping();lock=threading.RLock()
    log=settings.directory/'logs/boot.log';boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    def call(method,*args):
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        try:
            interface=dbus.Interface(bus.get_object(NAME,PATH,introspect=False),INTERFACE)
            return json.loads(getattr(interface,method)(*args,timeout=18))
        finally:bus.close()
    context=call('BootContext')
    def engine_call(operation):return call('EngineDisplay',context['generation'],operation)
    def remove():
        owner.stop()
        engine_call('idle')
    display=IdleDisplay(lambda:engine_call('prepare'),remove,timeout_seconds=settings.display_idle_timeout_seconds,
                        timeout_provider=lambda:read_settings().display_idle_timeout_seconds)
    # A runner restart must not lose cleanup of an already connected reservation.
    if context.get('connected') is True:display.schedule_cleanup()
    token=secrets.token_hex(32);control_path=settings.directory/'runtime/boot-control.sock'
    def request(message):
        if message.get('operation')=='prepare':engine_call('check')
        return display.request(message)
    server=ControlServer(control_path,token,lambda:owner.process,request,lock)
    host=BootHost(owner,owner.paths,ENGINE,BOOT_CONFIG,ASSETS,prepare=lambda:rotate_startup_log(log))
    host.control_environment=dict(MONITORIZE_CONTROL_SOCKET=str(control_path),MONITORIZE_CONTROL_TOKEN=token,
                                  MONITORIZE_CONTROL_INSTANCE='1')
    listener=ListenerEvidence();previous=None;next_check=0
    def evidence():
        from .network_evidence import collect_network_evidence
        print(json.dumps({'network_evidence':collect_network_evidence()}),flush=True)
    threading.Thread(target=evidence,daemon=True,name='boot-network-evidence').start()
    try:
        while not stop.is_set():
            phase=read_phase()
            with lock:
                if phase!='greeter':break
                if time.monotonic()>=next_check:
                    engine_call('check');next_check=time.monotonic()+1
                if owner.process is not None and owner.process.poll() is not None:display.reset_engine()
                if display.due():
                    try:display.expire()
                    except Exception as error:
                        print('Greeter cleanup failed: '+type(error).__name__,flush=True)
                        display.deadline=time.monotonic()+5
                # Placeholder is never captured; managed prepare returns the exact live target.
                if not host.step(phase,None,time.monotonic()):break
                if owner.process is not previous:
                    previous=owner.process;listener=ListenerEvidence()
                listening=bool(owner.running and listener.read(log))
                host.observe_listener(listening,time.monotonic())
                health=display.health(listening=listening,startup_error=host.startup_error)
                if health['capture_ready']:host.healthy()
                write_health(settings.directory/'runtime/boot-health.json',dict(health,pid=os.getpid(),
                    boot_id=boot_id,time=time.monotonic(),session=context['session'],state=display.state))
            stop.wait(.25)
        return 0
    finally:server.close();host.close()


def write_desktop_config(settings, descriptor):
    """Remove overridden strings from a private snapshot so Auto needs no key=.

    Sunshine rejects empty command-line values. Omitting the CLI flag alone
    would preserve a stale encoder/adapter from the user's original profile.
    """
    import tempfile
    from monitorize.platform.remote_login_identity import _read_private
    options=descriptor.get('stream_options')
    if options is None:return
    validate_descriptor({k:v for k,v in descriptor.items() if k in
        ('capture','output_name','pipewire_node','wayland_display','display','stream_options')},settings,descriptor['output_name'])
    source=Path(settings.profile)/'sunshine.conf'
    text=_read_private(source,os.getuid()).decode('utf-8')
    overrides=set(options)|{'encoder','adapter_name'}
    lines=[line for line in text.splitlines() if line.split('=',1)[0].strip() not in overrides]
    target=settings.directory/'runtime/desktop-sunshine.conf'
    fd,name=tempfile.mkstemp(prefix='.desktop-profile-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as output:
            output.write('\n'.join(lines)+'\n');output.flush();os.fsync(output.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


def desktop_command(settings,descriptor):
    values=validate_descriptor({key:value for key,value in descriptor.items() if key in
        ('capture','output_name','pipewire_node','wayland_display','display','stream_options')},settings,descriptor['output_name'])
    desktop = descriptor.get('desktop', 'KDE')
    session_type = descriptor.get('session_type', 'wayland')
    if desktop not in ('KDE', 'Hyprland', 'GNOME', 'X-Cinnamon') or session_type not in ('x11', 'wayland'):
        raise ValueError('Unsupported desktop context.')
    if ((desktop == 'X-Cinnamon' and values['capture'] != 'kms')
            or (session_type == 'x11' and (desktop != 'X-Cinnamon' or 'display' not in values
                                           or 'wayland_display' in values))):
        raise ValueError('Unsupported Cinnamon capture context.')
    config=(settings.directory/'runtime/desktop-sunshine.conf' if 'stream_options' in values
            else Path(settings.profile)/'sunshine.conf')
    argv=[str(ENGINE),str(config),'capture='+values['capture'],'output_name='+values['output_name'],
          'upnp=disabled','origin_web_ui_allowed=pc',f'port={SUNSHINE_BASE_PORT}','file_apps='+str(APPS),
          'log_path='+str(settings.directory/'logs/desktop.log')]
    options=dict(encoder=settings.encoder,adapter_name=settings.adapter)
    options.update(values.get('stream_options',{}))
    argv.extend(key+'='+value for key,value in options.items() if value)
    argv.extend(key+'='+value for key,value in identity_paths(settings.directory).config_overrides().items())
    environment=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin',LANG='C.UTF-8',HOME=settings.home,
        XDG_RUNTIME_DIR=f'/run/user/{settings.uid}',DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{settings.uid}/bus',
        XDG_CURRENT_DESKTOP=desktop,XDG_SESSION_TYPE=session_type,XDG_SEAT='seat0',
        SUNSHINE_ASSETS_DIR=str(ASSETS),MONITORIZE_REQUIRE_EXACT_OUTPUT='1',
        MONITORIZE_CAPTURE_OUTPUT=values['output_name'])
    if 'pipewire_node' in values:environment['SUNSHINE_PIPEWIRE_NODE']=str(values['pipewire_node'])
    for key,target in (('wayland_display','WAYLAND_DISPLAY'),('display','DISPLAY')):
        if key in values:environment[target]=values[key]
    return argv,environment


def desktop_main():
    settings=preflight();descriptor=read_descriptor(settings.runtime/'desktop.json',settings.uid)
    write_desktop_config(settings,descriptor)
    argv,environment=desktop_command(settings,descriptor)
    owner=EngineOwner(identity_paths(settings.directory));retry=RetrySchedule();stop=_stopping()
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    log=settings.directory/'logs/desktop.log'
    from .idle import IdleDisplay
    from .control import ControlServer
    from .dbus_service import NAME,PATH,INTERFACE
    lock=threading.RLock()
    def engine_call(operation,mode=None):
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        try:
            interface=dbus.Interface(bus.get_object(NAME,PATH,introspect=False),INTERFACE)
            if mode is not None:
                return json.loads(interface.EngineDisplayMode(descriptor['generation'],operation,*mode,True,timeout=18))
            return json.loads(interface.EngineDisplay(descriptor['generation'],operation,timeout=18))
        finally:bus.close()
    def remove():
        # Release both retained app/capture state and the inherited identity FD.
        # The coordinator independently checks identity release before unplugging.
        owner.stop()
        engine_call('idle')
    mirror = bool(descriptor.get('mirror_output'))
    display=IdleDisplay(lambda:engine_call('prepare'),remove,timeout_seconds=settings.display_idle_timeout_seconds,
                        timeout_provider=lambda:read_settings().display_idle_timeout_seconds,
                        prepare_mode=None if mirror else lambda mode:engine_call('prepare',mode))
    # A carried boot output can be idle even before the first desktop stream.
    display.schedule_cleanup()
    listener=ListenerEvidence()
    startup_error='';startup_deadline=None
    token=secrets.token_hex(32)
    control_path=settings.directory/'runtime/control.sock'
    def request(message):
        if not mirror and message.get('operation')=='prepare' and not descriptor.get('client_display_mode',False):
            mode=descriptor.get('display_mode',[1920,1080,60])
            message=dict(message,width=mode[0],height=mode[1],fps=mode[2])
        return display.request(message)
    server=ControlServer(control_path,token,lambda:owner.process,request,lock)
    environment.update(MONITORIZE_CONTROL_SOCKET=str(control_path),MONITORIZE_CONTROL_TOKEN=token,
                       MONITORIZE_CONTROL_INSTANCE='1')
    try:
        while not stop.is_set():
            if not own_session(descriptor['session'],settings.uid):break
            with lock:
                exited=owner.reap()
                if exited is not None:
                    display.reset_engine();retry.failed(time.monotonic())
                    startup_error=startup_failure(settings.directory/'logs/desktop-console.log',exited)
                    print(startup_error,flush=True)
                if display.due():
                    try:display.expire()
                    except Exception as error:
                        print('Remote display cleanup failed: '+type(error).__name__,flush=True)
                        display.deadline=time.monotonic()+5
                if not owner.running and (retry.deadline is None or retry.due(time.monotonic())):
                    try:
                        owner.start(argv,environment,prepare=lambda:rotate_startup_log(log),
                                    diagnostics=settings.directory/'logs/desktop-console.log')
                        listener=ListenerEvidence();startup_deadline=time.monotonic()+45
                    except (OSError,ValueError,RuntimeError) as error:
                        retry.failed(time.monotonic())
                        startup_error='Sunshine startup failed: '+type(error).__name__+': '+str(error)[:256]
                        print(startup_error,flush=True)
                health=display.health(listening=bool(owner.running and listener.read(log)))
                if health['listening']:
                    startup_error='';startup_deadline=None
                elif owner.running and startup_deadline is not None and time.monotonic()>=startup_deadline:
                    owner.stop();display.reset_engine();retry.failed(time.monotonic())
                    startup_error='Sunshine did not start listening within 45 seconds. See desktop-console.log.'
                    startup_deadline=None
                    print(startup_error,flush=True)
                if health['capture_ready']:retry.healthy()
                health['startup_error']=startup_error or display.error or display.configuration_error
                if startup_error:health['retry_seconds']=max(0,int((retry.deadline or time.monotonic())-time.monotonic()))
                write_health(settings.directory/'runtime/desktop-health.json',dict(health,pid=os.getpid(),
                    boot_id=boot_id,time=time.monotonic(),session=descriptor['session'],state=display.state))
            stop.wait(.25)
        return 0
    finally:
        retry.cancel();server.close();owner.stop()
