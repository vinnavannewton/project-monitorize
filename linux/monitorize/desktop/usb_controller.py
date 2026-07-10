"""ADB setup for USB streaming."""

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


class UsbController(QObject):
    statusChanged = pyqtSignal(str)
    busyChanged = pyqtSignal(bool)
    scanFinished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = ""
        self.busy = False
        self.process = None
        self.serial = None

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def _set_busy(self, value):
        self.busy = value
        self.busyChanged.emit(value)

    def start(self, serial=None):
        self.serial = serial
        self._set_busy(True)
        self._set_status("Running adb devices…")
        self._run(["devices"], self._devices_done)

    def reset(self):
        self.status = ""
        self.serial = None
        self._set_busy(False)

    def _run(self, args, callback):
        self.process = QProcess(self)
        self.process.finished.connect(callback)
        full_args = ["-s", self.serial] + args if (self.serial and args != ["devices"]) else args
        self.process.start("adb", full_args)

    def _devices_done(self, code, _status):
        if code:
            self._set_status("Error: adb devices failed. Is ADB installed?")
            self._set_busy(False)
            self.scanFinished.emit(False)
            return
        self._set_status("Setting up reverse proxy tcp:7110 (video)…")
        self._run(["reverse", "tcp:7110", "tcp:7112"], self._video_done)

    def _video_done(self, code, _status):
        if code:
            self._set_status("Error: Reverse port setup failed. Is a device connected?")
            self._set_busy(False)
            self.scanFinished.emit(False)
            return
        self._set_status("Setting up reverse proxy tcp:7111 (touch)…")
        self._run(["reverse", "tcp:7111", "tcp:7111"], self._touch_done)

    def _touch_done(self, code, _status):
        self._set_status(
            "Warning: tcp:7111 reverse failed — touch disabled"
            if code else "Device ready!"
        )
        self._set_busy(False)
        self.scanFinished.emit(True)
