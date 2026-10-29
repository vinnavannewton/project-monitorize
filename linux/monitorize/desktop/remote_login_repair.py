"""Pause idle desktop listeners before an explicit, asynchronous service repair."""
import time
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class RemoteLoginRepairCoordinator(QObject):
    _done = pyqtSignal(object)

    def __init__(self, backend):
        super().__init__(backend)
        self.backend = backend
        self.busy = False
        self.paused = None
        self.snapshot = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.advance)
        self._done.connect(self.finish)

    def begin(self, package=''):
        if self.busy or self.backend._closing or self.backend._remote_setup_busy:
            return
        self.package = package
        session = self.backend.session
        runtime = session.client_runtime
        remote = self.backend._remote_login
        from monitorize.platform.sunshine_service import is_sunshine_running
        if (session.busy or self.backend.applyingConfiguration or is_sunshine_running(2)
                or getattr(self.backend.streaming, 'streaming', False)
                or getattr(self.backend.streaming, 'third_streaming', False)
                or (session.active and not (runtime and runtime.active))
                or (remote and (remote.busy or remote.status.get('capture_ready')))):
            self.backend.remoteLoginSetupFinished.emit({'success': False, 'repair': True,
                'message': 'Stop the current stream and wait for pending operations before repairing remote login.'})
            return
        try:
            if runtime and runtime.active:
                self.snapshot = runtime.pause_idle_for_enrollment()
                self.paused = runtime
            self.busy = True
            self.backend._remote_setup_busy = True
            self.backend.remoteLoginSetupChanged.emit()
            if remote:
                remote.timer.stop()
            self.intent = self.backend._session_start_generation
            self.deadline = time.monotonic() + 20
            self.timer.start()
            self.advance()
        except Exception as error:
            self.finish({'success': False, 'message': str(error)})

    def advance(self):
        if not self.busy:
            return
        if self.backend._closing:
            self.finish({'success': False, 'message': 'Repair cancelled.'})
            return
        if self.paused and not self.paused.enrollment_pause_complete(self.snapshot):
            if time.monotonic() >= self.deadline:
                self.finish({'success': False, 'message': 'Listener cleanup did not finish. Stop streaming and retry repair.'})
            return
        self.timer.stop()
        from monitorize.platform.remote_login_repair import repair_remote_login
        future = self.backend._background_executor.submit(repair_remote_login, self.package)
        def done(future):
            try:
                result = future.result()
            except Exception:
                result = {'success': False, 'message': 'Remote-login repair failed. Refresh startup status before retrying.'}
            try:
                self._done.emit(result)
            except RuntimeError:
                pass
        future.add_done_callback(done)

    def finish(self, result):
        self.timer.stop()
        if self.paused:
            resume = (not self.backend._closing and not result.get('restart_required') and not self.backend.remoteLoginEnabled
                      and getattr(self, 'intent', None) == self.backend._session_start_generation)
            self.paused.resume_enrollment_pause(self.snapshot, instances=(1,) if resume else ())
            self.paused = None
            self.snapshot = None
        self.busy = False
        if not self.backend._closing:
            self.backend._finish_remote_login_setup({**result, 'repair': True})
