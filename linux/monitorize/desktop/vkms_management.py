"""Backend-owned VKMS Settings state survives QML page navigation."""
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from monitorize.platform import vkms_management as platform


class VkmsManagement(QObject):
    changed = pyqtSignal()
    _done = pyqtSignal(str, int, object)
    _log = pyqtSignal(str)

    def __init__(self, backend):
        super().__init__(backend)
        self.backend = backend
        self.health = dict(installed=None, state='checking', message='Checking…', version='')
        self.busy = False
        self.checking = False
        self.operation = ''
        self.message = ''
        self.logs = ''
        self.prepared = None
        self.serial = 0
        self.request_id = None
        self._coordinating = False
        self._early_result = None
        self.reboot_required = False
        self.up_to_date = False
        self._pending_up_to_date = False
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.setInterval(10000)
        self.notice_timer.timeout.connect(self.clear_notice)
        self._done.connect(self.complete)
        self._log.connect(self.append_log)
        backend.recoveryOperationFinished.connect(self.operation_finished)

    def snapshot(self):
        return {**self.health, 'checking': self.checking, 'busy': self.busy,
                'operation': self.operation, 'operation_message': self.message, 'logs': self.logs,
                'up_to_date': self.up_to_date}

    def clear_notice(self):
        self.notice_timer.stop()
        self.up_to_date = False
        self.changed.emit()

    def submit(self, kind, function):
        serial = self.serial
        future = self.backend._background_executor.submit(function)
        def finished(future):
            try:
                value = future.result()
            except Exception as error:
                value = dict(success=False, message=str(error))
            try:
                self._done.emit(kind, serial, value)
            except RuntimeError:
                pass
        future.add_done_callback(finished)

    def refresh(self):
        if self.busy or self.checking or self.backend._closing:
            return
        self.serial += 1
        self.checking = True
        self.changed.emit()
        self.submit('verify', platform.verify)

    def start(self, operation):
        b = self.backend
        if (operation not in ('reinstall', 'update') or self.busy or self.checking or b._closing
                or b.applyingConfiguration or b.systemSetupRunning or b.remoteLoginSetupBusy):
            return
        if self.health.get('installed') is not True:
            return
        self.clear_notice()
        self._pending_up_to_date = False
        self.serial += 1
        self.busy = True
        self.operation = operation
        self.message = 'Fetching Monitorize VKMS…' if operation == 'update' else 'Preparing VKMS reinstall…'
        self.logs = ''
        self.changed.emit()
        self.submit('prepare', lambda: platform.prepare_update(self._log.emit, reinstall=operation == 'reinstall'))

    def coordinate(self):
        self._coordinating = True
        self._early_result = None
        try:
            result = self.backend._recovery_operations().begin('vkms_' + self.operation)
        finally:
            self._coordinating = False
        self.request_id = result.get('request_id')
        if not result.get('pending'):
            self.finish(result)
        elif self._early_result is not None:
            self.operation_finished(self._early_result)
        self._early_result = None

    def install(self):
        result = platform.install_update(self.prepared, self._log.emit)
        if self.operation == 'reinstall' and result.get('success'):
            result['message'] = ('VKMS reinstalled. System restart required.' if result.get('reboot_required')
                                 else 'VKMS reinstalled. Checking installation…')
        return result

    def complete(self, kind, serial, value):
        if serial != self.serial or self.backend._closing:
            return
        if kind == 'verify':
            self.checking = False
            if value.get('state'):
                self.health = value
                if self.reboot_required and value.get('state') != 'working':
                    self.health.update(state='restart_required', message='System restart required', reboot_required=True)
            else:
                self.health.update(state='could_not_verify', message='Could not verify installation',
                                   details=value.get('message', 'Verification failed.'))
            if self.health.get('state') != 'working':
                self.clear_notice()
                self._pending_up_to_date = False
            elif self._pending_up_to_date:
                self._pending_up_to_date = False
                self.up_to_date = True
                self.notice_timer.start()
            self.changed.emit()
        elif kind == 'prepare':
            if not value.get('revision'):
                self.finish(value)
            else:
                self.prepared = value
                self.message = 'Preparing VKMS reinstall…' if self.operation == 'reinstall' else 'Preparing VKMS update…'
                self.changed.emit()
                self.coordinate()

    def operation_finished(self, result):
        if self._coordinating and result.get('operation') == 'vkms_' + self.operation:
            self._early_result = result
            return
        if (self.busy and result.get('operation') == 'vkms_' + self.operation
                and result.get('request_id') == self.request_id):
            self.finish(result)

    def finish(self, result):
        self._pending_up_to_date = bool(self.operation == 'update' and result.get('success')
            and (self.prepared or {}).get('up_to_date') and not result.get('reboot_required'))
        self.reboot_required = bool(result.get('reboot_required', self.reboot_required))
        self.busy = False
        self.prepared = None
        self.request_id = None
        self.operation = ''
        self.message = result.get('message', 'VKMS operation finished.')
        self.changed.emit()
        self.refresh()

    def append_log(self, text):
        self.logs = (self.logs + text + '\n')[-16384:]
        self.changed.emit()
