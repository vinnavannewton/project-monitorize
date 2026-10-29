"""One user-requested KMS authorization, without prompting background listeners."""
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from monitorize.platform.polkit_agent import agent_missing, start_agent
from monitorize.platform.sunshine_service import get_sunshine_kms_setup_status
from monitorize.platform.system_setup import kms_setup_command, parse_system_setup_result


def capture_requirements(bundle, desktop, remote=False):
    """Resolve Auto the same way as capture startup, retaining per-instance scope."""
    from monitorize.desktop.streaming_controller import _sunshine_capture_method
    if bundle.get("streaming_backend") != "sunshine":
        return []
    if remote:
        return [(1, "/usr/libexec/monitorize/sunshine")]
    result = []
    for instance, raw in ((1, bundle["primary"]), (2, bundle["second"])):
        if instance == 2 and (not raw.get("enabled") or bundle["primary"].get("display_type") == "Mirror"):
            continue
        requested = raw.get("sunshine_capture", "auto")
        capture = (_sunshine_capture_method(desktop, vkms=raw.get("virtual_display_creator", bundle["primary"].get("virtual_display_creator")) == "vkms")
                   if requested == "auto" else requested)
        if capture == "kms":
            result.append((instance, None))
    return result


class KmsAuthorization(QObject):
    """Serialize fixed helper actions and verify the selected files before resuming."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.request = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)

    @property
    def busy(self):
        return self.request is not None or self.process is not None

    def ensure(self, bundle, desktop, remote, completed):
        """Return immediately when ready; otherwise report or repair through completed."""
        if self.busy:
            return False
        requirements = capture_requirements(bundle, desktop, remote)
        targets = {}
        identities = {}
        for instance, binary in requirements:
            status = get_sunshine_kms_setup_status(instance, binary)
            try:
                path = Path(status["binary"]).resolve(strict=True)
                info = path.stat()
                identities[str(path)] = (info.st_dev, info.st_ino)
            except OSError as exc:
                completed(False, status["message"] or str(exc), instance)
                return False
            if status["code"] == "ready":
                continue
            if status["code"] != "missing_capability":
                completed(False, status["message"], instance)
                return False
            path = str(Path(status["binary"]).resolve())
            if path not in targets:
                command, error = kms_setup_command(path)
                if error:
                    completed(False, error["message"], instance)
                    return False
                targets[path] = (instance, command)
        if not targets:
            return True
        self.request = {"targets": list(targets.items()), "requirements": requirements,
                        "completed": completed, "identities": identities}
        self.changed.emit()
        self._next()
        return False

    def _next(self):
        request = self.request
        if not request:
            return
        if not request["targets"]:
            for instance, binary in request["requirements"]:
                status = get_sunshine_kms_setup_status(instance, binary)
                if status["code"] != "ready":
                    self._complete(False, status["message"], instance)
                    return
                path = Path(status["binary"]).resolve()
                try:
                    info = path.stat()
                    expected = request["identities"].get(str(path))
                    if expected is None or (info.st_dev, info.st_ino) != expected:
                        raise OSError("The Sunshine executable changed during authorization. Click Start again.")
                except OSError as exc:
                    self._complete(False, str(exc), instance)
                    return
            self._complete(True, "", 1)
            return
        path, (instance, command) = request["targets"].pop(0)
        request["active_target"] = (path, (instance, command))
        request["instance"] = instance
        request["identities"][path] = (int(command[-3]), int(command[-1]))
        process = QProcess(self)
        self.process = process
        process.finished.connect(lambda code, _status: self._finished(process, code))
        process.errorOccurred.connect(lambda error: self._finished(process, -1)
                                     if error == QProcess.ProcessError.FailedToStart else None)
        process.start(command[0], command[1:])
        # The fixed root helper completes its transaction without a GUI timeout.

    def _finished(self, process, code):
        if self.process is not process:
            return
        self.timer.stop()
        self.process = None
        stdout=bytes(process.readAllStandardOutput()).decode(errors="replace")
        stderr=bytes(process.readAllStandardError()).decode(errors="replace")
        result = parse_system_setup_result(code, stdout, stderr)
        process.deleteLater()
        if not self.request:
            self.changed.emit();return
        if code not in (0,126) and agent_missing(stderr+stdout) and not self.request.get('agent_retried'):
            self.request['agent_retried']=True
            if start_agent():
                self.request['targets'].insert(0,self.request['active_target'])
                QTimer.singleShot(500,self._next)
                return
        if not result["success"]:
            self._complete(False, result["message"], self.request["instance"])
        else:
            self._next()

    def _timeout(self):
        # Do not terminate an authorized helper while it commits root assets.
        self.cancel()

    def _complete(self, success, message, instance):
        request, self.request = self.request, None
        self.changed.emit()
        if request:
            request["completed"](success, message, instance)

    def cancel(self):
        """Stop and shutdown invalidate the continuation even if authorization finishes."""
        self.timer.stop()
        self.request = None
        # Discard only the desktop continuation. _finished drains the one active
        # helper and releases busy state, preventing overlapping authorization.
        self.changed.emit()
