"""KDE third-display stream lifecycle."""

import os
import secrets
import sys

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from gui.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from gui.utils import LINUX_DIR


class ThirdStreamController(QObject):
    activeChanged = pyqtSignal(bool)
    readinessChanged = pyqtSignal(bool)
    logAppended = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.ready = False
        self.krfb = None
        self.streamer = None
        self.gst_pids = set()
        self.encrypted = False

    def start(self, res, fps, bitrate, encoder, encrypted):
        if self.active:
            return
        kill_patterns("gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115")
        try:
            width, height = map(int, (res or "").split()[0].split("x"))
        except ValueError:
            width, height = 1920, 1200
        self.width, self.height = width, height
        self.fps, self.bitrate = int(fps), int(bitrate)
        self.encrypted = encrypted
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(encoder, "cpu"))
        if encrypted:
            self.env.insert("MONITORIZE_HOST", "127.0.0.1")
            self.env.insert("MONITORIZE_PORT", "7115")
        self.active = True
        self.ready = False
        self.activeChanged.emit(True)
        self.readinessChanged.emit(False)
        self.logAppended.emit(
            "STREAMER", f"[Third display] Spawning virtual monitor: {width}x{height}"
        )
        self.krfb = QProcess(self)
        self.krfb.setWorkingDirectory(LINUX_DIR)
        self.krfb.setProcessEnvironment(self.env)
        self.krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.krfb.readyReadStandardOutput.connect(self._read_krfb)
        self.krfb.start("krfb-virtualmonitor", [
            "--resolution", f"{width}x{height}",
            "--name", "TabletDisplay2",
            "--password", secrets.token_urlsafe(6),
            "--port", "5901",
        ])
        QTimer.singleShot(5000, self._launch_streamer)

    def _launch_streamer(self):
        if not self.active:
            return
        self.streamer = QProcess(self)
        self.streamer.setWorkingDirectory(LINUX_DIR)
        self.streamer.setProcessEnvironment(self.env)
        self.streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.streamer.finished.connect(self._finished)
        self.streamer.start(sys.executable, [
            os.path.join(LINUX_DIR, "Streamer_kde.py"),
            str(self.width), str(self.height), str(self.fps), str(self.bitrate),
            "wifi", "7114",
        ])
        self.logAppended.emit(
            "STREAMER",
            "[Third display] Streamer launched on port 7114. "
            "Select 'TabletDisplay2' in the KDE picker.",
        )

    def _read_krfb(self):
        raw = bytes(self.krfb.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.logAppended.emit("STREAMER", f"[Third display KRFB] {raw}")

    def _read_streamer(self):
        raw = bytes(self.streamer.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.logAppended.emit("STREAMER", f"[Third display] {raw}")
        for line in raw.splitlines():
            if "Setting pipeline to PLAYING" in line or "New clock:" in line:
                self.ready = True
                self.readinessChanged.emit(True)
            elif "Got EOS" in line or "[GStreamer] EXITED:" in line:
                self.ready = False
                self.readinessChanged.emit(False)
            elif "[GStreamer] PID:" in line:
                try:
                    self.gst_pids.add(int(line.split("PID:")[1].strip()))
                except ValueError:
                    pass

    def _finished(self, code, _status):
        self.logAppended.emit(
            "STREAMER", f"[Third display] Streamer exited (code {code})"
        )
        self.ready = False
        self.readinessChanged.emit(False)
        if self.active:
            self.active = False
            self.activeChanged.emit(False)

    def stop(self):
        stop_processes(self.krfb, self.streamer)
        self.krfb = self.streamer = None
        kill_tracked_pids(self.gst_pids)
        kill_patterns("gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115")
        was_active = self.active
        self.active = self.ready = False
        if was_active:
            self.activeChanged.emit(False)
        self.readinessChanged.emit(False)

