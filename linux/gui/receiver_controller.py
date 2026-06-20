"""Desktop stream receiver lifecycle."""

import os
import subprocess
import sys
import time

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from gui.process_utils import gst_has_element, kill_patterns, stop_processes
from gui.settings import (
    clear_receiver_credentials,
    load_receiver_credentials,
    save_receiver_credentials,
)
from gui.utils import LINUX_DIR


class ReceiverController(QObject):
    receivingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    hostChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str)
    pairingRequired = pyqtSignal(str, int, str)

    def __init__(self, de, discovery, parent=None):
        super().__init__(parent)
        self.de = de
        self.discovery = discovery
        self.receiving = False
        self.status = ""
        self.host_label = ""
        self.process = None
        self.tls_process = None
        self.tls_buffer = ""
        self.auth_failed = False
        self.stopping = False
        self.retry_count = 0
        self.retry_pending = False
        self.attempt_started = 0.0
        self.inhibit_cookie = None
        self.stable_timer = QTimer(self)
        self.stable_timer.setSingleShot(True)
        self.stable_timer.timeout.connect(self._mark_stable)
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self._start_attempt)

    def _set_receiving(self, value):
        self.receiving = value
        self.receivingChanged.emit(value)

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def connect(self, host, port, encrypted, fingerprint, pairing_code, decoder):
        self.discovery.stop_browsing()
        self.stop()
        self.stopping = False
        self.host = host
        self.port = port
        self.encrypted = encrypted
        self.fingerprint = fingerprint
        self.pairing_code = pairing_code
        self.decoder = decoder
        self.sink = "glimagesink" if gst_has_element("glimagesink") else "autovideosink"
        if decoder == "Hardware":
            hardware = next((
                name for name in ("vah264dec", "vaapih264dec")
                if gst_has_element(name)
            ), None)
            if not hardware:
                self._set_status(
                    "Hardware decoder unavailable — install the GStreamer VA-API decoder"
                )
                self.logAppended.emit(
                    "ERROR: Hardware mode requires vah264dec or vaapih264dec."
                )
                return
            self.decoder_args = [hardware]
            self.decoder_label = f"VA-API {hardware}"
        else:
            self.decoder_args = ["avdec_h264", "max-threads=1", "thread-type=slice"]
            self.decoder_label = "Software avdec_h264"
        self.retry_count = 0
        self.retry_pending = False
        self.auth_failed = False
        self.host_label = f"{host}:{port}"
        self.hostChanged.emit(self.host_label)
        self._set_status(f"Connecting to {host}:{port}…")
        self.logAppended.emit(f"Connecting to {host} on port {port}…")
        self._start_attempt()

    def _start_attempt(self):
        self.retry_pending = False
        self.auth_failed = False
        if not self.encrypted:
            self._launch_pipeline(self.host, self.port)
            return
        fingerprint, token = load_receiver_credentials(self.host)
        if self.pairing_code:
            fingerprint, token = self.fingerprint, ""
        self.tls_process = QProcess(self)
        self.tls_process.setWorkingDirectory(LINUX_DIR)
        self.tls_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.tls_process.readyReadStandardOutput.connect(self._read_tls)
        self.tls_process.finished.connect(self._tls_finished)
        args = [os.path.join(LINUX_DIR, "tls_receiver.py"), self.host, str(self.port)]
        if fingerprint:
            args += ["--fingerprint", fingerprint]
        if token:
            args += ["--token", token]
        elif self.pairing_code:
            args += ["--code", self.pairing_code]
        self.tls_process.start(sys.executable, args)

    def _launch_pipeline(self, host, port):
        self.attempt_started = time.monotonic()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_pipeline)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(
            lambda _error: self._set_status(self.process.errorString())
        )
        args = [
            "-e", "tcpclientsrc", f"host={host}", f"port={port}", "!",
            "h264parse", "!", "queue", "max-size-buffers=1",
            "max-size-time=0", "max-size-bytes=0", "leaky=downstream", "!",
            *self.decoder_args, "!", "queue", "max-size-buffers=1",
            "max-size-time=0", "max-size-bytes=0", "leaky=downstream", "!",
            self.sink, "sync=false",
        ]
        self.logAppended.emit(f"Decoder: {self.decoder_label}; sink: {self.sink}")
        self.process.start("gst-launch-1.0", args)

    def _started(self):
        display = "Third" if self.port == 7114 else "Second"
        self._set_status(f"Waiting for {display} display stream…")
        self.stable_timer.start(2000)

    def _mark_stable(self):
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self._inhibit_sleep()
            self.retry_count = 0
            self._set_receiving(True)
            self._set_status(f"Receiving from {self.host}:{self.port}")
            self.logAppended.emit("Stream connected and stable.")

    def _read_tls(self):
        raw = bytes(self.tls_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.tls_buffer += raw
        lines = self.tls_buffer.split("\n")
        self.tls_buffer = lines.pop() if not self.tls_buffer.endswith("\n") else ""
        for line in lines:
            if line == "[TLS RECEIVER] READY" and self.process is None:
                self._launch_pipeline("127.0.0.1", 17110)
            elif line.startswith("[TLS RECEIVER] CREDENTIALS "):
                fingerprint, token = line.removeprefix(
                    "[TLS RECEIVER] CREDENTIALS "
                ).split()
                save_receiver_credentials(self.host, fingerprint, token)
                self.pairing_code = ""
                self._set_status("Authenticated; starting encrypted stream…")
            elif line.startswith("[TLS RECEIVER] AUTH_FAILED"):
                self.auth_failed = True
                fingerprint = line.removeprefix("[TLS RECEIVER] AUTH_FAILED").strip()
                clear_receiver_credentials(self.host)
                self._set_status("Pairing required")
                self.pairingRequired.emit(self.host, self.port, fingerprint)
            elif line.startswith("[TLS RECEIVER] ERROR "):
                self._set_status(line.removeprefix("[TLS RECEIVER] ERROR "))
            elif line:
                self.logAppended.emit(line)

    def _tls_finished(self, code, _status):
        if code and not self.auth_failed and not self.receiving and not self.retry_pending:
            self._set_status("Encrypted connection failed")

    def _read_pipeline(self):
        raw = bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.logAppended.emit(raw)
        if "ERROR" in raw:
            self._set_status("Error — see logs")

    def _finished(self, code, _status):
        self.logAppended.emit(f"Receiver process exited (code {code})")
        self.stable_timer.stop()
        elapsed = time.monotonic() - self.attempt_started
        max_retries = 30 if self.receiving else 10
        if (
            not self.stopping
            and (self.receiving or elapsed < 2)
            and self.retry_count < max_retries - 1
        ):
            self.retry_count += 1
            self.retry_pending = True
            display = "Third" if self.port == 7114 else "Second"
            self._set_status(
                f"Waiting for {display} display stream… "
                f"({self.retry_count}/{max_retries})"
            )
            stop_processes(self.tls_process)
            self.process = self.tls_process = None
            self.retry_timer.start(1000)
            return
        if self.receiving:
            self._set_status("Disconnected")
            self.logAppended.emit("Stream ended. Click Disconnect to return.")
        else:
            self._set_status("Unable to start stream after 10 attempts")

    def stop(self):
        self.stopping = True
        self.stable_timer.stop()
        self.retry_timer.stop()
        stop_processes(self.process, self.tls_process)
        self.process = self.tls_process = None
        self.tls_buffer = ""
        self.auth_failed = self.retry_pending = False
        kill_patterns("gst-launch-1.0.*tcpclientsrc")
        self._uninhibit_sleep()
        self._set_receiving(False)

    def _inhibit_sleep(self):
        try:
            if self.de == "kde":
                result = subprocess.run([
                    "dbus-send", "--session", "--print-reply",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.Inhibit",
                    "string:Monitorize",
                    "string:Streaming display receiver active",
                ], capture_output=True, text=True)
                line = next((line for line in result.stdout.splitlines() if "uint32" in line), "")
                if line:
                    self.inhibit_cookie = int(line.split("uint32")[-1].strip())
            elif self.de == "hyprland":
                subprocess.run(["pkill", "-USR1", "hypridle"], capture_output=True)
        except Exception as exc:
            print(f"[Receiver] Failed to inhibit sleep: {exc}")

    def _uninhibit_sleep(self):
        try:
            if self.de == "kde" and self.inhibit_cookie is not None:
                subprocess.run([
                    "dbus-send", "--session",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.UnInhibit",
                    f"uint32:{self.inhibit_cookie}",
                ], capture_output=True)
                self.inhibit_cookie = None
            elif self.de == "hyprland":
                subprocess.run(["pkill", "-USR2", "hypridle"], capture_output=True)
        except Exception as exc:
            print(f"[Receiver] Failed to uninhibit sleep: {exc}")

