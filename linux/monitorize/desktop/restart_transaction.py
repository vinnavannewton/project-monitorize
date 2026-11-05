"""Serialized, cancellable restart with bounded cleanup and one rollback."""
import copy
import time
from PyQt6.QtCore import QObject,QTimer,pyqtSignal


class RestartTransaction(QObject):
    changed = pyqtSignal()
    finished = pyqtSignal(bool,str)

    def __init__(self, stop, stopped, launch, ready, failure, commit, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self.stop=stop;self.stopped=stopped;self.launch=launch;self.ready=ready
        self.failure=failure;self.commit=commit;self.clock=clock
        self.phase='';self.message='';self.candidate=None;self.previous=None
        self.rollback=False;self.error='';self.publish=False
        self.recovering=False
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.tick)

    @property
    def busy(self):return bool(self.phase)

    def begin(self,candidate,previous,*,publish=True):
        if self.busy:return False
        self.candidate=copy.deepcopy(candidate);self.previous=copy.deepcopy(previous)
        self.publish=publish;self.rollback=False;self.error=''
        self.recovering=False
        self.phase='stopping';self.message='Stopping the current session…'
        self.deadline=self.clock()+30
        try:self.stop()
        except Exception as exc:self._finish(False,str(exc));return False
        if not self.phase:return False
        self.timer.start();self.changed.emit();return True

    def cancel(self):
        self.timer.stop();self.phase='';self.candidate=None
        self.message='';self.changed.emit()

    def _finish(self,success,message):
        self.timer.stop();self.phase='';self.message=message
        self.changed.emit();self.finished.emit(success,message)

    def _fail(self,error):
        if self.rollback:
            self._finish(False,self.error+' Previous configuration could not restart: '+error)
            return
        if self.previous is None or self.previous == self.candidate:
            # Stop partial startup, but never resurrect unverified saved intent or
            # retry the configuration that just failed.
            self.error=error;self.phase='stopping';self.recovering=True
            self.message='Startup failed; stopping the failed session…'
            self.deadline=self.clock()+30
            try:self.stop()
            except Exception as exc:self._finish(False,error+' '+str(exc));return
            self.changed.emit();return
        self.error=error;self.rollback=True;self.phase='stopping'
        self.message='New configuration failed; restoring the previous session…'
        self.deadline=self.clock()+30
        try:self.stop()
        except Exception as exc:self._finish(False,error+' '+str(exc));return
        self.changed.emit()

    def tick(self):
        if not self.busy:return
        try:
            if self.clock()>self.deadline:
                if self.phase=='stopping':
                    # Never launch over a cleanup/ownership timeout.
                    self._finish(False,'Timed out waiting for the previous session to stop. '+self.error)
                else:self._fail('Session did not become ready within 100 seconds.')
                return
            if self.phase=='stopping':
                if not self.stopped():return
                if self.recovering:
                    self._finish(False,self.error+' Session stopped. Correct the configuration and use Apply or Restart.')
                    return
                target=self.previous if self.rollback else self.candidate
                self.phase='starting';self.deadline=self.clock()+100
                self.message='Restarting the session…'
                self.changed.emit();self.launch(copy.deepcopy(target))
                return
            error=self.failure()
            if error:self._fail(error);return
            if not self.ready():return
            if self.rollback:
                self._finish(False,self.error+' The previous running configuration was restored; your saved settings are retained.')
            else:
                if self.publish:self.commit(copy.deepcopy(self.candidate))
                self._finish(True,'Changes applied. Session is ready.' if self.publish else 'Session restarted.')
        except Exception as exc:
            if self.phase == 'stopping':
                self._finish(False, ' '.join(part for part in (self.error, str(exc)) if part))
            else:
                self._fail(str(exc))
