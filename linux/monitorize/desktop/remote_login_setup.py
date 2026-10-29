"""Qt-thread enrollment coordination; privileged work stays in the installed helper."""
import os
import time
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from monitorize.platform.system_setup import apply_remote_login_setup, remote_login_setup_status
from monitorize.platform.sunshine_service import is_sunshine_running
from monitorize.platform.recovery_actions import RecoveryRequest
from monitorize.config import app_log
from .remote_login_controller import configure, controller_snapshot, restore_controller_snapshot, ControllerConflict, restore_managed


class RemoteLoginSetupCoordinator(QObject):
    finished=pyqtSignal(object)
    _worker_done=pyqtSignal(object)
    interruptionRequested=pyqtSignal(object)

    def __init__(self,backend):
        super().__init__(backend)
        self.backend=backend;self.busy=False;self._cancelled=False;self._inflight=False
        self._snapshot=None;self._paused_runtime=None;self._root_result=None
        self._observed_runtime=None;self._listener_observed=False
        self._root_applied=None;self._restore_controller=False
        self._timer=QTimer(self);self._timer.setInterval(100);self._timer.timeout.connect(self._advance)
        self._worker_done.connect(self._root_finished)

    def begin(self,enabled):
        if self.busy or self.backend._closing:return
        self.request=RecoveryRequest('boot_startup',self.backend._session_start_generation,enabled)
        app_log.write('SETUP',f'Startup request={self.request.request_id} enabled={enabled} generation={self.request.generation}')
        self._cancelled=False;self._root_result=None;self._snapshot=None;self._paused_runtime=None
        self._inflight=False;self._waiting_for_stop=False;self._awaiting_decision=False
        self._remote_paused=None
        self._restore_controller=False;self._root_applied=None;self._decision_kind="stop"
        try:
            status=remote_login_setup_status()
            if enabled and not status.get('live_available'):
                raise ValueError(status.get('live_message') or 'Matching live-enrollment service support is required.')
            session=self.backend.session;runtime=session.client_runtime
            if session.busy:raise ValueError('Wait for display cleanup before changing startup settings.')
            remote=getattr(self.backend,'_remote_login',None)
            disruptive=(is_sunshine_running(2) or getattr(self.backend.streaming,'third_streaming',False)
                or getattr(self.backend.streaming,'streaming',False)
                or (session.active and not (runtime and runtime.active))
                or bool(getattr(remote,'status',{}).get('capture_ready')))
            if runtime and runtime.active and not disruptive:
                try:self._snapshot=runtime.pause_idle_for_enrollment();self._paused_runtime=runtime
                except ValueError:disruptive=True
            self.busy=True;self.backend._remote_setup_busy=True
            self.backend.remoteLoginSetupChanged.emit()
            if disruptive:
                self._awaiting_decision=True
                self._phase('Waiting for your decision…')
                self.interruptionRequested.emit(dict(request_id=self.request.request_id,
                    title='Change boot startup',message='This will stop the current Monitorize session before changing boot startup. Pairing is retained.'))
                return
            self._prepare()
        except Exception as error:
            self._finish(dict(success=False,activated=False,reboot_required=False,message=str(error)))

    def resolve(self,request_id,accepted):
        if not self.busy or not self._awaiting_decision or request_id!=self.request.request_id:return
        self._awaiting_decision=False
        if not accepted:
            self._finish(dict(success=False,cancelled=True,message='Startup change cancelled. Saved settings were retained.'))
            return
        if self.backend._closing or (getattr(self,'_intent',self.request.generation) if self._decision_kind=='controller' else self.request.generation)!=self.backend._session_start_generation:
            self._finish(dict(success=False,cancelled=True,message='The session changed. Request the startup change again.'))
            return
        if self._decision_kind=='controller':
            self._restore_controller=True
        else:
            self.backend._stop_owned_session()
            self._waiting_for_stop=True
        self._prepare()

    def _phase(self,message):
        self.backend._recovery_stage=message
        self.backend.remoteLoginSetupChanged.emit()

    def _prepare(self):
        remote=getattr(self.backend,'_remote_login',None)
        if remote is not None and hasattr(remote,'timer'):
            self._remote_paused=remote
            # Keep polling until an explicitly requested Stop is observed.
            if not self._waiting_for_stop:remote.timer.stop()
        self._intent=self.backend._session_start_generation
        self._deadline=time.monotonic()+20
        self._phase('Stopping the Monitorize session…' if self._waiting_for_stop else 'Preparing startup settings…')
        self._timer.start();self._advance()

    def _advance(self):
        if not self.busy:return
        if self._awaiting_decision:
            if self._cancelled or self.backend._closing:
                self._finish(dict(success=False,cancelled=True,message='Startup change cancelled.'))
            return
        if self._root_result is not None:
            self._check_listener();return
        if self._inflight or self._awaiting_decision:return
        if self._cancelled or self.backend._closing:
            self._finish(dict(success=False,cancelled=True,message='Remote-login setup cancelled.'));return
        if self._waiting_for_stop:
            if not self.backend._owned_session_stopped():
                if time.monotonic()>=self._deadline:
                    self._finish(dict(success=False,message='The owned session did not stop. No startup changes were made.'))
                return
            self._waiting_for_stop=False
            if self._remote_paused is not None:self._remote_paused.timer.stop()
        if self._paused_runtime and not self._paused_runtime.enrollment_pause_complete(self._snapshot):
            if time.monotonic()>=self._deadline:
                self._finish(dict(success=False,message='Listener cleanup did not finish. Retry setup after stopping streaming.'))
            return
        self._inflight=True
        request=self.request
        self._phase('Authorizing and applying startup settings…')
        future=self.backend._background_executor.submit(self._work)
        def done(future):
            try:result=future.result()
            except Exception:result=dict(success=False,outcome_unknown=True,message='Remote-login setup did not complete. Inspect setup status before retrying.')
            try:self._worker_done.emit(request.result(result))
            except RuntimeError:pass  # GUI has been destroyed; retained root receipt permits recovery.
        future.add_done_callback(done)

    def _work(self):
        snapshot=None;controller=False
        try:
            if self.request.enabled:
                snapshot=controller_snapshot()
                (restore_managed if self._restore_controller else configure)(True);controller=True
            result=self._root_applied or apply_remote_login_setup(self.request.enabled)
            if result.get('success'):self._root_applied=dict(result)
            if not self.request.enabled and result.get('success'):
                try:(restore_managed if self._restore_controller else configure)(False);controller=True
                except ControllerConflict:raise
                except Exception:
                    result={**result,'success':False,'partial':True,
                        'message':'Boot startup changed, but desktop startup restoration needs retry.'}
            if self.request.enabled and result.get('success'):
                from monitorize.config.settings import saved_display_idle_timeout
                from monitorize.platform.system_setup import apply_display_idle_timeout
                seconds=saved_display_idle_timeout()
                if seconds is not None and remote_login_setup_status().get('display_idle_timeout_seconds') != seconds:
                    synced=apply_display_idle_timeout(seconds)
                    if not synced.get('success'):
                        result={**result,'success':False,'partial':True,
                                'message':'Remote login is saved, but its disconnect timer could not be synchronized. Retry setup.'}
        except ControllerConflict as error:
            return dict(success=False,controller_restore_required=True,configured=bool(self._root_applied and self._root_applied.get('configured')),
                message=str(error))
        except Exception:
            result=dict(success=False,message='Remote-login setup or desktop-controller configuration failed.')
        if not result.get('success') and 'configured' not in result:
            try:result={**result,'configured':bool(remote_login_setup_status().get('enabled'))}
            except Exception:result={**result,'configured':True,'partial':True}
        if self.request.enabled and not result.get('success') and not result.get('configured') and snapshot is not None:
            try:restore_controller_snapshot(snapshot);controller=False
            except Exception:result={**result,'partial':True,'message':'Desktop controller restoration needs attention. Inspect setup status before retrying.'}
        return {**result,'controller_configured':controller}

    def _root_finished(self,result):
        if not self.busy:return
        if result.get('request_id',self.request.request_id)!=self.request.request_id:return
        self._inflight=False
        if result.get('controller_restore_required') and not self.backend._closing and not self._cancelled:
            if self._root_applied is not None:
                self.backend._finish_remote_login_setup({**result,'_live_pending':True,'_reset_remote_runtime':not self.request.enabled})
            self._awaiting_decision=True;self._decision_kind='controller'
            self._phase('Waiting to restore managed desktop startup…')
            self.interruptionRequested.emit(dict(request_id=self.request.request_id,title='Restore desktop controller',
                accept_text='Restore controller',message=result['message']))
            return
        if self.backend._closing:
            self._finish({**result,'success':False,'activated':False});return
        self.backend._finish_remote_login_setup({**result,'_live_pending':True,
            '_reset_remote_runtime':not self.request.enabled})
        if not self.request.enabled:
            self._finish(result)
            return
        if self._cancelled:
            runtime=self.backend._remote_login
            runtime.participant.want_running=False
            runtime.stop()
            self._finish({**result,'success':False,'partial':bool(result.get('configured')),
                          'message':'Remote-login setup stopped. Saved startup settings are retained.'});return
        if not result.get('success') or not result.get('controller_configured'):
            self._finish({**result,'success':False,'partial':bool(result.get('partial') or result.get('configured'))});return
        if result.get('uid')!=os.getuid() or not result.get('session'):
            self._finish({**result,'success':False,'partial':True,'message':'Remote login is saved, but its desktop context could not be verified. Retry setup.'});return
        self._root_result=result;self._deadline=time.monotonic()+60
        self._phase('Checking the remote-login listener…')
        runtime=self.backend._remote_login
        self._listener_observed=False;self._observed_runtime=runtime
        runtime.changed.connect(self._listener_changed)
        if result.get('reused') and runtime.participant.want_running:
            runtime.poll()
        elif hasattr(runtime,'start'):runtime.start()
        self._check_listener()

    def _listener_changed(self):
        # Runtime dispatch also emits changed while busy. Only a completed
        # post-helper observation is proof of current listener health.
        if self._observed_runtime is not None and not getattr(self._observed_runtime,'busy',False):
            self._listener_observed=True
        self._check_listener()

    def _check_listener(self):
        if not self.busy or self._root_result is None:return
        runtime=self.backend._remote_login;status=runtime.status;result=self._root_result
        if self._cancelled:
            self._finish({**result,'success':False,'partial':True,'message':'Setup stopped; startup settings are saved.'});return
        if self._listener_observed and (status.get('startup_error') or status.get('control_error') or status.get('state') in ('error','authentication_error')):
            self._finish({**result,'success':False,'partial':True,'message':'Remote login is saved, but could not start. Retry setup.'});return
        if (self._listener_observed and status.get('uid')==os.getuid() and status.get('session')==result['session']
            and status.get('desktop_owned') is True and status.get('listening') is True
            and runtime.participant.generation and runtime.participant.epoch
            and status.get('epoch')==runtime.participant.epoch):
            self._finish({**result,'success':True,'activated':True,
                'message':'Remote login enabled. Available now and after future boots.'});return
        if time.monotonic()>=self._deadline:
            self._finish({**result,'success':False,'partial':True,'message':'Remote login is saved, but its listener did not become ready. Retry setup.'})

    def _finish(self,result):
        if self._cancelled:result={**result,'success':False,'cancelled':True}
        if result.get('cancelled') and self._root_applied is not None:
            result={**result,'configured':bool(self._root_applied.get('configured')),'partial':True,
                'message':'Saved startup settings were retained. The remaining desktop operation was cancelled.'}
        self._timer.stop()
        self._awaiting_decision=False
        remote=getattr(self,'_remote_paused',None)
        if remote is not None and not getattr(remote,'closed',False) and not self.backend._closing:
            if getattr(remote,'enabled',False):remote.timer.start()
        self._remote_paused=None
        if self._observed_runtime is not None:
            self._observed_runtime.changed.disconnect(self._listener_changed)
            self._observed_runtime=None
        if self._paused_runtime is not None:
            resume=(not result.get('configured') and not result.get('cleanup_pending') and not self._cancelled and not self.backend._closing
                    and getattr(self,'_intent',self.request.generation)==self.backend._session_start_generation)
            self._paused_runtime.resume_enrollment_pause(self._snapshot,instances=(1,) if resume else ())
            self._paused_runtime=None;self._snapshot=None
        self.busy=False;self.backend._remote_setup_busy=False
        self.backend._recovery_stage=''
        if not self.backend._closing:
            self.backend.remoteLoginSetupChanged.emit()
            self.finished.emit(self.request.result({'activated':False,'reboot_required':False,**result}))

    def cancel(self):
        self._cancelled=True
        if not self.busy:return
        if self._root_result is not None:
            self.backend._remote_login.stop();self._check_listener()
        elif not self._inflight:self._advance()
