"""Qt desktop participant using one stable private D-Bus sender per lifetime."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from monitorize.config import app_log
from PyQt6.QtCore import QObject,QTimer,pyqtSignal
from monitorize.platform.login_capture_probe import find_boot_output
from monitorize.remote_login.settings import SETTINGS,read_settings
from monitorize.remote_login.dbus_service import NAME,PATH,INTERFACE
from monitorize.remote_login.capability import engine_launch_error,source_engine_update_error

MESSAGES={
    'greeter_idle':'Login host is listening; the virtual display is disconnected until a client connects.',
    'greeter_preparing':'Preparing the login-screen display.',
    'greeter_disconnect_grace':'Client disconnected; removing the login-screen display after {timeout} seconds.',
    'waiting_for_greeter':'Waiting for the login screen.',
    'greeter_listening':'Session is starting.',
    'login_available':'Login screen available.',
    'handoff_to_desktop':'Session is starting.',
    'waiting_for_desktop':'Session is starting.',
    'desktop_listening':'Waiting for a client.',
    'streaming':'Client connected.',
    'disconnect_grace':'Client disconnected; removing the display after {timeout} seconds.',
    'removing':'Releasing capture and removing the virtual display.',
    'desktop_starting':'Session is starting.',
    'authentication_error':'Monitorize could not authenticate this application as part of your active desktop session. Close it and launch the desktop controller service.',
    'ownership_wait':'Waiting to reclaim desktop streaming ownership.',
    'desktop_available':'Client connected.',
    'stopped':'Session is not running.',
    'stopping_desktop':'Stopping desktop streaming.',
    'suspended':'Streaming paused because another session owns the seat.',
    'error':'Remote-login service needs attention. Inspect its journal.',
    'unavailable':'Boot service is unavailable. Use Restart to start desktop streaming.',
}


def desktop_mirror_supported(status):
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower().split(':')
    return (status.get('desktop_mirror_version', 0) >= 1
            and os.environ.get('XDG_SESSION_TYPE') == 'x11'
            and bool({'cinnamon', 'x-cinnamon'}.intersection(desktop)))


def saved_stream_options():
    """Only persisted/applied settings reach a fresh desktop service participant."""
    from monitorize.config.settings import load_display_settings
    from monitorize.desktop.session import Session
    from monitorize.platform.gpu_discovery import resolve_encoding_gpu
    config=Session._saved_values(load_display_settings())
    device=resolve_encoding_gpu(config['encoder'],config['gpu_id'])
    if config['gpu_id'] and not device:raise ValueError('Selected encoding GPU is unavailable.')
    hevc,av1={'Auto':('0','0'),'H.264':('1','1'),'HEVC':('2','1'),'AV1':('1','2')}[config['codec']]
    return dict(encoder={'Auto':'','NVIDIA':'nvenc','VA-API':'vaapi','Vulkan':'vulkan','Software':'software'}[config['encoder']],
        hevc_mode=hevc,av1_mode=av1,adapter_name=device.get('render_node','') if device else '',
        native_pen_touch='enabled' if config['native_pen_touch'] else 'disabled',
        stream_audio='enabled' if config['enable_audio'] else 'disabled')


def legacy_stream_options(options, *, write=False, keep_enrolled_encoder=False):
    """Older runner reads its profile but fixes encoder/adapter at enrollment."""
    import tempfile
    from pathlib import Path
    settings=read_settings()
    if not keep_enrolled_encoder and (options.get('encoder','') != settings.encoder or options.get('adapter_name','') != settings.adapter):
        raise ValueError('The installed remote-login service fixes its encoder and GPU at setup. Update remote-login setup to change those; codec changes are supported.')
    if not write:return
    path=Path(settings.profile)/'sunshine.conf'
    # Validate using the same narrow whitelist as the current descriptor API.
    from monitorize.remote_login.settings import validate_descriptor
    validate_descriptor({'capture':'kms','output_name':'Virtual-1','stream_options':options},settings,'Virtual-1')
    values={k:v for k,v in options.items() if k not in ('encoder','adapter_name')}
    lines=path.read_text().splitlines() if path.exists() else []
    lines=[line for line in lines if line.split('=',1)[0].strip() not in values]
    lines.extend(key+' = '+value for key,value in values.items())
    fd,name=tempfile.mkstemp(prefix='.stream-options-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write('\n'.join(lines)+'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


class Participant:
    def __init__(self,*,factory=None,output_finder=None):
        # A fresh compositor may leave the boot connector disabled until the
        # privileged adoption worker activates it. Capture still requires enabled.
        self.factory=factory;self.output_finder=output_finder or (lambda:find_boot_output(require_enabled=False));self.bus=None;self.interface=None
        self.generation='';self.want_running=True;self.epoch=None;self.stream_options=None
        self._control_failures={};self._diagnostic_state=None
        self.layout=None;self.layout_mode='extend'
        self.mirror_output=None;self._mirroring=False
        if factory is None:
            from monitorize.config.settings import load_general_settings
            from monitorize.desktop.remote_layout import RemoteLayout
            self.layout_mode=load_general_settings().get('display_behavior','extend')
            self.layout=RemoteLayout()

    def _connect(self):
        if self.interface is not None:return
        if self.factory is not None:self.interface=self.factory();return
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        self.bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        self.interface=dbus.Interface(self.bus.get_object(NAME,PATH,introspect=False),INTERFACE)

    def _control_error(self,method,error):
        name = error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
        if name in ('org.freedesktop.DBus.Error.ServiceUnknown','org.freedesktop.DBus.Error.NameHasNoOwner'):
            return ('The remote-login supervisor is unavailable. '
                    'Check monitorize-remote-login.service in Diagnostics & logs.')
        if name in ('org.freedesktop.DBus.Error.NoReply','org.freedesktop.DBus.Error.Timeout'):
            return f'The remote-login supervisor did not reply to {method}. Check its service journal.'
        return f'Remote-login request {method} failed. See Diagnostics & logs.'

    def _call(self,method,*args):
        try:
            self._connect()
            result=json.loads(getattr(self.interface,method)(*args,timeout=60 if method=='PrepareDesktop' else 5))
        except Exception as error:
            name=error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
            name=name if isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_.]{1,160}',name) else 'unspecified'
            fingerprint=(type(error).__name__,name)
            if self._control_failures.get(method)!=fingerprint:
                # Do not log args, descriptors, tokens, or arbitrary error text.
                app_log.write('REMOTE',f'Control {method} failed: {fingerprint[0]}; D-Bus={name}')
                self._control_failures[method]=fingerprint
            raise
        if method in self._control_failures:
            app_log.write('REMOTE',f'Control {method} recovered.')
            self._control_failures.pop(method)
        return result

    def poll(self):
        status=self._poll()
        if self.layout is not None:
            try:
                streaming=(status.get('state')=='streaming' or
                           (status.get('state')=='desktop_available' and bool(status.get('capture_ready'))))
                target=(status.get('output_name') or self.output_finder()) if streaming else None
                self.layout.update('extend' if self._mirroring else self.layout_mode,streaming,target)
            except Exception as exc:
                status=dict(status,layout_error='Desktop layout failed: '+str(exc))
        state=str(status.get('state','unknown'))
        state=state if re.fullmatch(r'[a-z_]{1,48}',state) else 'unknown'
        diagnostic=(state,bool(status.get('listening')),bool(status.get('capture_ready')),
            bool(status.get('startup_error') or status.get('control_error')),bool(status.get('layout_error')),self.want_running)
        if diagnostic!=self._diagnostic_state:
            app_log.write('REMOTE','Desktop state=%s listening=%s capture_ready=%s operation_error=%s layout_error=%s want_running=%s'%diagnostic)
            self._diagnostic_state=diagnostic
        return status

    def _poll(self):
        status={}
        method='GetStatus'
        try:
            status=self._call('GetStatus')
            epoch=status.get('epoch')
            if epoch!=self.epoch:self.generation='';self.epoch=epoch
            # Session inactivity (including suspend) revokes ownership without
            # restarting the supervisor. Its epoch therefore need not change.
            # A waiting state is not authority to reuse a previous generation:
            # reacquire on the same authenticated bus connection before Prepare.
            if status.get('state') in ('waiting_for_desktop','handoff_to_desktop','suspended',
                                      'waiting_for_greeter','greeter_listening','login_available'):
                self.generation=''
            if not self.want_running and status.get('state')=='waiting_for_desktop':
                return dict(status,state='stopped')
            if status.get('state')=='stopped':self.want_running=False
            if self.want_running:
                if self.factory is None and status.get('state') in ('waiting_for_desktop','handoff_to_desktop'):
                    error=source_engine_update_error()
                    if error:return dict(status,state='error',startup_error=error,listening=False,capture_ready=False)
                if status.get('state') not in ('waiting_for_desktop','handoff_to_desktop','desktop_starting','desktop_available'):
                    return status
                if not self.generation:
                    method='AcquireDesktop'
                    grant=self._call(method)
                    if not grant.get('granted'):return dict(status,state=grant.get('state',status['state']))
                    self.generation=grant['generation']
                if status.get('state') in ('waiting_for_desktop','handoff_to_desktop'):
                    output=status.get('output_name') or self.output_finder()
                    if output is None:return dict(state='waiting_for_desktop',capture_ready=False)
                    descriptor={'capture':'kms','output_name':output}
                    mirror = self.mirror_output
                    if self.factory is None:
                        from monitorize.config.settings import load_display_settings
                        from monitorize.desktop.session import Session
                        saved=load_display_settings();config=Session._saved_values(saved)
                        width,height=map(int,config['res'].split('x'))
                        descriptor.update(client_display_mode=bool(saved.get('use_client_display_settings',False)),
                                          display_mode=[width,height,float(config['fps'])])
                        if mirror is None and config['display_type'] == 'Mirror':
                            mirror = config['mirror_output']
                    if mirror and desktop_mirror_supported(status):
                        descriptor['mirror_output'] = mirror
                    if self.stream_options is None and status.get('desktop_options_version',0)>=1:
                        self.stream_options=saved_stream_options()
                    if status.get('desktop_options_version',0)>=1:
                        if self.stream_options:descriptor['stream_options']=dict(self.stream_options)
                    elif self.factory is None:
                        legacy_stream_options(self.stream_options or saved_stream_options(),write=True,keep_enrolled_encoder=True)
                    method='PrepareDesktop'
                    status=self._call(method,self.generation,json.dumps(descriptor))
                    self._mirroring = 'mirror_output' in descriptor
            return status
        except Exception as error:
            # Revoked ownership is not a missing service. Keep the same sender,
            # discard only the stale token, and observe status on the next poll.
            name=error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
            if name==NAME+'.AuthenticationFailed':
                self.generation=''
                return dict(status,state='authentication_error',capture_ready=False,listening=False,
                            control_error=MESSAGES['authentication_error'])
            if name==NAME+'.Rejected' or isinstance(error,PermissionError):
                self.generation=''
                return dict(status,state='ownership_wait',capture_ready=False,listening=False)
            if name==NAME+'.Unavailable':
                return dict(status,state='error',capture_ready=False,listening=False,
                            control_error='Remote-login operation failed. Inspect the remote-login service journal.')
            # A timeout must not immediately replay a potentially completed write.
            return dict(state='unavailable',capture_ready=False,listening=False,
                        control_error=self._control_error(method,error))

    def _restore_layout(self):
        try:
            if self.layout is not None:self.layout.restore()
        except Exception as exc:
            return 'Desktop layout restoration pending: '+str(exc)
        return ''

    def stop(self):
        error=self._restore_layout()
        self.want_running=False
        try:
            if self.generation:status=self._call('Stop',self.generation)
            else:status=self.poll()
        except Exception:status=self.poll()
        return dict(status,layout_error=error) if error else status

    def start(self, options=None, display_behavior=None, *, mirror_output=None):
        error=self._restore_layout()
        if error:return dict(self._poll(),layout_error=error)
        if display_behavior is not None:self.layout_mode=display_behavior
        if options is not None:self.stream_options=dict(options)
        self.mirror_output=mirror_output
        if self.factory is None and (error := engine_launch_error()):
            return dict(state='error',capture_ready=False,listening=False,startup_error=error)
        try:
            self._call('Start');self.want_running=True
        except Exception as error:
            name=error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
            if name==NAME+'.AuthenticationFailed':
                self.generation=''
                return dict(state='authentication_error',capture_ready=False,listening=False,
                            control_error=MESSAGES['authentication_error'])
            return dict(state='unavailable',capture_ready=False,listening=False,
                        control_error=self._control_error('Start',error))
        return self.poll()

    def close(self):
        self._restore_layout()
        try:
            if self.generation:self._call('ReleaseDesktop',self.generation)
        except Exception:pass
        finally:
            if self.bus is not None:self.bus.close()
            self.bus=None;self.interface=None;self.generation=''


class RemoteLoginRuntime(QObject):
    changed=pyqtSignal()
    _finished=pyqtSignal(object)
    def __init__(self,parent=None,*,participant=None,enabled=None):
        super().__init__(parent)
        if enabled is None:
            enabled=False
            try:
                if SETTINGS.exists():
                    settings=read_settings();enabled=settings.enabled and settings.uid==os.getuid()
            except ValueError:pass
        self.enabled=bool(enabled);self.status={'state':'waiting_for_desktop','capture_ready':False}
        self.participant=participant or Participant();self.closed=False;self.busy=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='monitorize-handoff')
        self._finished.connect(self._finish)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.poll)
        if self.enabled:self.timer.start();QTimer.singleShot(0,self.poll)

    @property
    def active(self):
        # Enrollment is not proof that a streaming service is running.
        return self.enabled and self.status.get('state') not in ('stopped','disabled','unavailable','error','authentication_error')
    def refresh_enrollment(self):
        """Refresh configuration without releasing the authenticated participant."""
        enabled=False
        if SETTINGS.exists():
            settings=read_settings();enabled=settings.enabled and settings.uid==os.getuid()
        self.enabled=bool(enabled)
        if self.enabled:self.timer.start()
        else:self.timer.stop()
    @property
    def message(self):
        return self.status.get('layout_error') or self.status.get('startup_error') or self.status.get('control_error') or MESSAGES.get(self.status.get('state'),'Waiting for remote-login ownership.').format(timeout=self.status.get('display_idle_timeout_seconds',20))

    def _submit(self,method):
        if self.closed or self.busy or not self.enabled:return
        self.busy=True;self.changed.emit()
        future=self.executor.submit(method)
        def done(result):
            if not self.closed:
                try:self._finished.emit(result.result())
                except Exception as error:
                    app_log.write('REMOTE','Desktop worker failed: '+type(error).__name__)
                    self._finished.emit({'state':'unavailable','capture_ready':False,'control_error':'Remote-login desktop worker failed. See Diagnostics & logs.'})
        future.add_done_callback(done)

    def _finish(self,result):
        if self.closed:return
        self.busy=False;self.status=result;self.changed.emit()
    def poll(self):self._submit(self.participant.poll)
    def start(self, options=None, display_behavior=None, *, mirror_output=None):
        if options is None and display_behavior is None and mirror_output is None:
            self._submit(self.participant.start)
        elif mirror_output is None:
            self._submit(lambda:self.participant.start(options,display_behavior=display_behavior))
        else:
            self._submit(lambda:self.participant.start(options,display_behavior=display_behavior,mirror_output=mirror_output))
    def stop(self):
        # Queue behind an already dispatched PrepareDesktop; a user Stop must
        # never be dropped just because the ownership worker is currently busy.
        if self.closed or not self.enabled:return
        self.executor.submit(self.participant.stop).add_done_callback(
            lambda f:self._finished.emit(f.result()) if not self.closed else None)
    def close(self):
        if self.closed:return
        self.closed=True;self.timer.stop()
        if self.enabled:self.executor.submit(self.participant.close)
        self.executor.shutdown(wait=False,cancel_futures=not self.enabled)
