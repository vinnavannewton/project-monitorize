"""Queued incoming pairing prompts with exact instance/request routing."""
from collections import OrderedDict
from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from monitorize.platform.pairing_api import resolve_hosts
from monitorize.platform.sunshine_service import is_sunshine_running


class IncomingPairing(QObject):
    changed = pyqtSignal()
    _polled = pyqtSignal(int, object)
    _finished = pyqtSignal(int, bool, str)

    def __init__(self, backend):
        super().__init__(backend)
        self.backend = backend
        self._request = {}
        self._phase = 'request'
        self._busy = False
        self._message = ''
        self._hosts = {}
        self._rows = {}
        self._dismissed = OrderedDict()
        self._polling = False
        self._epoch = 0
        self._action = 0
        self._action_key = None
        self._closed = False
        self._polled.connect(self._receive)
        self._finished.connect(self._complete)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        backend.uiVisibleChanged.connect(self._visibility)

    @pyqtProperty('QVariantMap', notify=changed)
    def request(self):return dict(self._request)

    @pyqtProperty(str, notify=changed)
    def phase(self):return self._phase

    @pyqtProperty(bool, notify=changed)
    def busy(self):return self._busy

    @pyqtProperty(str, notify=changed)
    def message(self):return self._message

    def _scope(self):
        backend = self.backend
        remote = backend.remoteLoginEnabled
        # A service-owned engine deliberately does not appear in the child map.
        instances = [1] if remote else [i for i in (1, 2) if is_sunshine_running(i)]
        return tuple(instances), remote

    def _visibility(self, visible):
        self._epoch += 1
        if visible and not self._closed:
            self.timer.start()
            self.poll()
        else:
            self.timer.stop()

    @pyqtSlot()
    def poll(self):
        if self._closed or not self.backend.uiVisible or self._polling or self._busy:
            return
        scope = self._scope()
        self._polling = True
        epoch = self._epoch
        def work():
            rows, hosts = {}, {}
            try:
                for host in resolve_hosts(*scope):
                    try:
                        pending = host.pending()
                    except Exception:
                        continue
                    hosts[host.instance] = host
                    for row in pending:
                        rows[(host.instance, row['id'])] = row
            finally:
                if not self._closed:
                    self._polled.emit(epoch, (scope, rows, hosts))
        self.backend._background_executor.submit(work)

    def _receive(self, epoch, result):
        self._polling = False
        if self._closed or epoch != self._epoch or not self.backend.uiVisible:
            return
        scope, rows, hosts = result
        if scope != self._scope() or self._busy:
            return
        current = self._key()
        identity_changed = (current and self._hosts.get(current[0]) != hosts.get(current[0]))
        self._rows, self._hosts = rows, hosts
        if identity_changed:
            self._request = {}
            self.changed.emit()
        if current and current not in rows and self._phase != 'done':
            self._request = {}
            self._message = ''
        if not self._request:
            self._next()

    def _key(self):
        return (self._request['instance'], self._request['id']) if self._request else None

    def _next(self):
        self._request = next((dict(row) for key, row in self._rows.items()
                              if key not in self._dismissed), {})
        self._phase, self._message = 'request', ''
        self.changed.emit()

    @pyqtSlot()
    def accept(self):
        if self._request and not self._busy:
            self._phase, self._message = 'pin', ''
            self.changed.emit()

    @pyqtSlot()
    def dismiss(self):
        if self._busy:
            return
        if self._request:
            self._remember_dismissal(self._key())
        self._request = {}
        self._next()

    @pyqtSlot()
    def reject(self):self._run('reject')

    @pyqtSlot(str)
    def submit(self, pin):
        if self._phase == 'pin':self._run('approve', pin)

    def _run(self, operation, pin=''):
        if self._closed or self._busy or not self._request:
            return
        row = dict(self._request)
        host = self._hosts.get(row['instance'])
        if host is None:
            return
        if operation == 'approve' and (len(pin) != 4 or not pin.isascii() or not pin.isdigit()):
            self._message = 'Enter the four-digit PIN shown in Moonlight.'
            self.changed.emit()
            return
        self._action += 1
        action = self._action
        self._action_key = self._key()
        self._busy, self._message = True, ''
        self.changed.emit()
        scope = self._scope()
        def work():
            try:
                # Re-resolve ownership/certificate after the operator's decision.
                current = {h.instance: h for h in resolve_hosts(*scope)}
                if current.get(host.instance) != host:
                    success, message = False, 'Sunshine stopped or changed identity. Request a new PIN in Moonlight.'
                elif operation == 'approve':
                    success, message = host.approve(row['id'], pin, row['name'])
                else:
                    success, message = host.reject(row['id'])
            except Exception:
                success, message = False, 'Could not complete the pairing request. Check Sunshine settings and request a new PIN.'
            if not self._closed:
                self._finished.emit(action, success, message)
        self.backend._background_executor.submit(work)

    def _complete(self, action, success, message):
        if self._closed or action != self._action:
            return
        self._busy, self._message = False, message
        if success:
            self._remember_dismissal(self._action_key)
            self._phase = 'done'
        self.changed.emit()

    def _remember_dismissal(self, key):
        self._dismissed[key] = None
        # Keep retry/outage polls from reopening a dismissed request while
        # bounding storage over the entire lifetime of a long-running app.
        while len(self._dismissed) > 512:
            self._dismissed.popitem(last=False)

    def close(self):
        self._closed = True
        self._epoch += 1
        self.timer.stop()
