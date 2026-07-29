"""ADB setup for USB streaming."""

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


def authorized_adb_serials(output):
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    return [
        fields[0]
        for line in output.splitlines()
        if len(fields := line.split()) >= 2 and fields[1] == "device"
    ]


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
        serials = self._authorized_serials()
        if self.serial:
            if self.serial not in serials:
                self._set_status("Error: Selected Android device is not authorized in ADB.")
                self._set_busy(False)
                self.scanFinished.emit(False)
                return
        elif len(serials) == 1:
            self.serial = serials[0]
        elif not serials:
            self._set_status("Error: No authorized Android device found in ADB.")
            self._set_busy(False)
            self.scanFinished.emit(False)
            return
        else:
            self._set_status("Error: Multiple authorized Android devices found; select one first.")
            self._set_busy(False)
            self.scanFinished.emit(False)
            return
        self._set_status("Setting up reverse proxy tcp:7110 (video)…")
        self._run(["reverse", "tcp:7110", "tcp:7112"], self._video_done)

    def _authorized_serials(self):
        if self.process is None:
            return []
        return authorized_adb_serials(bytes(self.process.readAllStandardOutput()))

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
