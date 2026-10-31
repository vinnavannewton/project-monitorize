"""One contextual stop/authorize/verify continuation for owned desktop operations."""
import time
from PyQt6.QtCore import QObject,QTimer,pyqtSignal
from monitorize.platform.recovery_actions import RecoveryRequest

OPERATIONS=('source_repair','maintenance_resume','host_recovery','vkms_restart','vkms_update','clear_tokens','cleanup_displays','reset_sunshine','reboot','logout')


class RecoveryOperations(QObject):
    decisionRequested=pyqtSignal(object)
    _done=pyqtSignal(object)

    def __init__(self,backend):
        super().__init__(backend);self.backend=backend;self.busy=False;self.inflight=False
        self.waiting=False;self.cancelled=False;self.paused=None;self.snapshot=None
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.advance)
        self._done.connect(self.complete)

    def begin(self,operation,disable_boot=False):
        if operation not in OPERATIONS:raise ValueError('Unsupported recovery operation.')
        b=self.backend
        if self.busy or b._closing or b.applyingConfiguration or (b.systemSetupRunning and operation not in ('vkms_restart','vkms_update')):return {'success':False,'message':'Wait for the current operation to finish.'}
        if b.session.busy:return {'success':False,'message':'Display cleanup is still in progress.'}
        self.generation=b._session_start_generation
        self.request=RecoveryRequest(operation,self.generation,False if disable_boot else None)
        self.busy=True;self.inflight=False;self.waiting=False;self.cancelled=False;self.paused=None;self.snapshot=None
        b._remote_setup_busy=True;b._recovery_stage='Preparing recovery…';b.remoteLoginSetupChanged.emit()
        runtime=b.session.client_runtime
        remote=b._remote_login
        disruptive=(bool(getattr(remote,'status',{}).get('capture_ready')) or b.streaming.streaming
                    or b.streaming.third_streaming or (b.session.active and not (runtime and runtime.active)))
        if runtime and runtime.active and not disruptive and operation not in ("logout","reboot"):
            try:self.snapshot=runtime.pause_idle_for_enrollment();self.paused=runtime
            except ValueError:disruptive=True
        if disruptive or operation in ("reboot","logout"):
            self.waiting=True
            self.decisionRequested.emit(dict(request_id=self.request.request_id,title='Continue recovery',
                message=('Save your work. This will '+('restart the system' if operation=='reboot' else 'end this login session')+'.' if operation in ('reboot','logout') else 'This will stop the affected Monitorize session before continuing. Pairing is retained.'),
                accept_text=('Restart' if operation=='reboot' else 'Log out' if operation=='logout' else 'Stop and continue')))
        else:self.prepare(False)
        return {'success':False,'pending':True,'request_id':self.request.request_id,'message':'Recovery in progress.'}

    def resolve(self,request_id,accepted):
        if not self.busy or not self.waiting or request_id!=self.request.request_id:return
        self.waiting=False
        if not accepted or self.backend._session_start_generation!=self.request.generation:
            self.complete(dict(success=False,cancelled=True,message='Recovery cancelled. Settings retained.'));return
        self.backend._stop_owned_session()
        self.prepare(True)

    def prepare(self,stopping):
        self.stopping=stopping;self.generation=self.backend._session_start_generation
        self.deadline=time.monotonic()+25
        self.backend._recovery_stage='Stopping the owned session…' if stopping else 'Checking recovery prerequisites…'
        self.backend.remoteLoginSetupChanged.emit()
        if not stopping and self.backend._remote_login:self.backend._remote_login.timer.stop()
        self.timer.start();self.advance()

    def advance(self):
        if not self.busy or self.inflight or self.waiting:return
        if self.cancelled or self.backend._closing:
            self.complete(dict(success=False,cancelled=True,message='Recovery cancelled.'));return
        if self.stopping and not self.backend._owned_session_stopped():
            if time.monotonic()>self.deadline:self.complete(dict(success=False,message='Owned session cleanup did not finish. No recovery changes were applied.'))
            return
        if self.paused and not self.paused.enrollment_pause_complete(self.snapshot):
            if time.monotonic()>self.deadline:self.complete(dict(success=False,message='Idle listener cleanup did not finish.'))
            return
        if self.backend._remote_login:
            if self.backend._remote_login.busy:
                if time.monotonic()>self.deadline:self.complete(dict(success=False,message='Remote-login status did not settle. Retry after the current operation.'))
                return
            self.backend._remote_login.timer.stop()
        self.timer.stop();self.inflight=True
        self.backend._recovery_stage='Authorizing and completing recovery…';self.backend.remoteLoginSetupChanged.emit()
        operation=self.request.operation
        if operation=='cleanup_displays':
            self.backend.virtualDisplayCleanupFinished.connect(self.cleanup_finished)
            self.backend.removeStagnantVirtualDisplays();return
        if operation in ('clear_tokens','reset_sunshine'):
            self.backend._applying_recovery=True
            try:result=(self.backend.clearRestoreTokens() if operation=='clear_tokens' else self.backend.resetSunshineSettings())
            except Exception as error:result=dict(success=False,message=str(error))
            finally:self.backend._applying_recovery=False
            self.complete(result);return
        if operation=='vkms_update':
            action=self.backend._vkms_manager().install;arguments=()
        elif operation=='vkms_restart':
            from monitorize.platform.system_setup import restart_vkms_host
            action=restart_vkms_host;arguments=()
        elif operation in ('reboot','logout'):
            from monitorize.platform.session_recovery import end_session
            action=end_session;arguments=(operation,)
        elif operation=='source_repair':
            from monitorize.platform.remote_login_repair import repair_remote_login
            action=repair_remote_login;arguments=()
        elif operation=='maintenance_resume':
            from monitorize.platform.system_setup import apply_remote_login_recovery
            action=apply_remote_login_recovery;arguments=(self.request.enabled is False,)
        else:
            from monitorize.platform.system_setup import recover_vkms_host
            action=recover_vkms_host;arguments=()
        request=self.request
        future=self.backend._background_executor.submit(action,*arguments)
        def done(future):
            try:result=future.result()
            except Exception:result=dict(success=False,outcome_unknown=True,message='Recovery outcome is unknown. Refresh status before retrying.')
            try:self._done.emit(request.result(result))
            except RuntimeError:pass
        future.add_done_callback(done)

    def cleanup_finished(self,success,message):
        try:self.backend.virtualDisplayCleanupFinished.disconnect(self.cleanup_finished)
        except (TypeError,RuntimeError):pass
        self.complete(dict(success=success,message=message))

    def complete(self,result):
        if not self.busy:return
        if result.get('request_id',self.request.request_id)!=self.request.request_id:return
        self.timer.stop();b=self.backend;operation=self.request.operation
        if self.cancelled:result={**result,'success':False,'cancelled':True}
        if self.paused:
            resume=(not b._closing and not self.cancelled and not result.get('cleanup_pending') and not result.get('outcome_unknown') and not result.get('reboot_required')
                and not result.get('configured',b.remoteLoginEnabled) and self.generation==b._session_start_generation
                and operation not in ('clear_tokens','cleanup_displays'))
            self.paused.resume_enrollment_pause(self.snapshot,instances=(1,) if resume else ())
        applied=self.inflight
        self.paused=None;self.snapshot=None;self.busy=False;self.inflight=False
        b._remote_setup_busy=False;b._recovery_stage=''
        if b._closing:return
        result=self.request.result(result)
        if operation in ('source_repair','maintenance_resume'):
            if applied:b._finish_remote_login_setup({**result,'repair':True})
            else:
                b.remoteLoginSetupChanged.emit();b.remoteLoginSetupFinished.emit({**result,'repair':True})
            if self.cancelled and b._remote_login:
                b._remote_login.participant.want_running=False;b._remote_login.stop()
        else:
            if self.cancelled and b._remote_login:
                b._remote_login.participant.want_running=False;b._remote_login.stop()
            if b._remote_login.enabled:b._remote_login.timer.start()
            if operation in ('host_recovery','vkms_restart','vkms_update'):b.refreshVkmsHelperAvailability();b.refreshVkmsAuthorization()
            b.remoteLoginSetupChanged.emit();b.recoveryOperationFinished.emit(result)

    def cancel(self):
        self.cancelled=True
        if self.busy and not self.inflight:self.complete(dict(success=False,cancelled=True,message='Recovery cancelled.'))
