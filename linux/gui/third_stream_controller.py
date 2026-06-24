"""KDE third-display stream lifecycle."""

import os
import sys

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from gui.process_utils import kill_patterns, kill_tracked_pids, stop_processes
from gui.utils import LINUX_DIR
from gui.validation import (
    DEFAULT_SECONDARY_RESOLUTION,
    sanitize_bitrate,
    sanitize_encoder,
    sanitize_encoder_profile,
    sanitize_fps,
    sanitize_resolution,
)


class ThirdStreamController(QObject):
    activeChanged = pyqtSignal(bool)
    readinessChanged = pyqtSignal(bool)
    logAppended = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.ready = False
        self.streamer = None
        self.gst_pids = set()
        self.encrypted = False
        self.generation = 0
        self.encoder_profile = "Low Latency"

    def start(self, res, fps, bitrate, encoder, encoder_profile, encrypted):
        if self.active:
            return
        self.generation += 1
        generation = self.generation
        kill_patterns("gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115")
        width, height = sanitize_resolution(res, DEFAULT_SECONDARY_RESOLUTION)
        self.width, self.height = width, height
        self.fps, self.bitrate = sanitize_fps(fps), sanitize_bitrate(bitrate)
        encoder = sanitize_encoder(encoder)
        self.encoder_profile = sanitize_encoder_profile(encoder_profile)
        self.encoder = encoder
        self.encrypted = encrypted
        self.env = QProcessEnvironment.systemEnvironment()
        self.env.insert("PYTHONUNBUFFERED", "1")
        self.env.insert("MONITORIZE_ENCODER", {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
        }.get(encoder, "cpu"))
        self.env.insert("MONITORIZE_ENCODER_PROFILE", self.encoder_profile)
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
        self.env.insert("MONITORIZE_PORTAL_SOURCE_TYPE", "4")
        self.env.insert(
            "MONITORIZE_PORTAL_SELECTOR_HINT",
            "KDE will create a second virtual monitor for Monitorize.",
        )
        QTimer.singleShot(0, lambda: self._launch_streamer(generation))

    def _launch_streamer(self, generation=None):
        generation = self.generation if generation is None else generation
        if not self.active or generation != self.generation:
            return
        self.streamer = QProcess(self)
        process = self.streamer
        self.streamer.setWorkingDirectory(LINUX_DIR)
        self.streamer.setProcessEnvironment(self.env)
        self.streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.streamer.readyReadStandardOutput.connect(
            lambda: self._read_streamer(generation, process)
        )
        self.streamer.finished.connect(
            lambda code, status: self._finished(code, status, generation, process)
        )
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

    def _read_streamer(self, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.streamer if process is None else process
        if generation != self.generation or process is not self.streamer:
            return
        raw = bytes(process.readAllStandardOutput()).decode(
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

    def _finished(self, code, _status, generation=None, process=None):
        generation = self.generation if generation is None else generation
        process = self.streamer if process is None else process
        if generation != self.generation or process is not self.streamer:
            return
        self.logAppended.emit(
            "STREAMER", f"[Third display] Streamer exited (code {code})"
        )
        self.ready = False
        self.readinessChanged.emit(False)
        if self.active:
            self.active = False
            self.activeChanged.emit(False)

    def stop(self):
        self.generation += 1
        stop_processes(self.streamer)
        self.streamer = None
        kill_tracked_pids(self.gst_pids)
        kill_patterns("gst-launch-1.0.*port=7114", "gst-launch-1.0.*port=7115")
        was_active = self.active
        self.active = self.ready = False
        if was_active:
            self.activeChanged.emit(False)
        self.readinessChanged.emit(False)
