"""
Monitorize GUI — Main application window and entry point (QML Backend Bridge).
"""

import sys
import os
import subprocess
import shutil
import secrets
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, QDialog, QMessageBox
)
from PyQt6.QtCore import (
    Qt, QProcess, QProcessEnvironment, QTimer, QUrl,
    pyqtSignal, pyqtProperty, pyqtSlot
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import QColor, QPalette, QIcon
from PyQt6.QtQuickWidgets import QQuickWidget

from gui.utils import (
    detect_desktop_environment, get_local_ip, LINUX_DIR
)
from gui.settings import (
    load_general_settings, save_general_settings,
    load_usb_settings, save_usb_settings,
    load_wifi_settings, save_wifi_settings,
    load_second_display_settings, save_second_display_settings,
    load_receiver_settings, save_receiver_settings,
    load_receiver_rotation, save_receiver_rotation,
    load_receiver_credentials, save_receiver_credentials, clear_receiver_credentials,
)


class MonitorizeWindow(QMainWindow):
    """Main application window. Hosts the QML UI via QQuickWidget and
    exposes backend slots, properties, and signals to drive streaming,
    USB scanning, settings persistence, and system-tray behaviour."""

    
    detectedDeChanged = pyqtSignal(str)
    localIpChanged = pyqtSignal(str)
    usbStatusTextChanged = pyqtSignal(str)
    usbBusyChanged = pyqtSignal(bool)
    isStreamingChanged = pyqtSignal(bool)
    countdownChanged = pyqtSignal(int)
    streamingStatusChanged = pyqtSignal(str)
    pairingCodeChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str, str) 

    
    isReceivingChanged = pyqtSignal(bool)
    receiverStatusChanged = pyqtSignal(str)
    receiverHostIpChanged = pyqtSignal(str)
    receiverRotationChanged = pyqtSignal(int)
    discoveredDevicesChanged = pyqtSignal()
    receiverLogAppended = pyqtSignal(str)
    receiverPairingRequired = pyqtSignal(str, int, str)

    
    secondStreamActiveChanged = pyqtSignal(bool)

    def __init__(self):
        """Initialise the window, detect the desktop environment, set up
        the system tray, and load the QML interface."""
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)


        app_icon_path = os.path.join(LINUX_DIR, "assets", "monitorize-icon.png")
        if os.path.exists(app_icon_path):
            self.setWindowIcon(QIcon(app_icon_path))

        
        subprocess.Popen(["pkill", "-9", "-f", "gst-launch-1.0.*port=7110"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "gst-launch-1.0.*port=7112"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "gst-launch-1.0.*port=7114"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "gst-launch-1.0.*port=7115"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "Streamer_.*\\.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-9", "-f", "tls_proxy.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        
        detected = detect_desktop_environment()
        if detected:
            self._detected_de = detected
        else:
            self._detected_de = self._ask_desktop_environment()

        
        self._local_ip = get_local_ip()
        self._usb_status_text = ""
        self._usb_busy = False
        self._is_streaming = False
        self._countdown = 0

        
        self._is_receiving = False
        self._receiver_status = ""
        self._receiver_host_ip = ""
        self._receiver_rotation = load_receiver_rotation()
        self._discovered_devices = []
        self._discovery_browser = None
        self._discovery_zc = None
        self.process_receiver: QProcess | None = None
        self.process_receiver_tls: QProcess | None = None
        self._receiver_tls_buffer = ""
        self._receiver_auth_failed = False
        self._receiver_host = ""
        self._receiver_port = 7110
        self._receiver_encrypted = False
        self._receiver_fingerprint = ""
        self._receiver_pairing_code = ""
        self._receiver_retry_count = 0
        self._receiver_retry_pending = False
        self._receiver_stopping = False
        self._receiver_attempt_started = 0.0
        self._receiver_stable_timer = QTimer(self)
        self._receiver_stable_timer.setSingleShot(True)
        self._receiver_stable_timer.timeout.connect(self._mark_receiver_stable)
        self._receiver_retry_timer = QTimer(self)
        self._receiver_retry_timer.setSingleShot(True)
        self._receiver_retry_timer.timeout.connect(self._start_receiver_attempt)
        self._kde_inhibit_cookie = None

        
        self._second_stream_active = False
        self._third_stream_ready = False
        self.process_krfb2:     QProcess | None = None
        self.process_streamer2: QProcess | None = None
        self._gst_pids2 = set()
        self._streaming_status = ""
        self._pairing_code = ""
        self._tls_proxy_buffer = ""

        self.initial_headless_monitors = []
        if self._detected_de == "hyprland":
            self.initial_headless_monitors = self._get_current_headless_monitors()
            print(f"[Hyprland] Initial virtual monitors detected: {self.initial_headless_monitors}")
        self.created_headless_monitor = None

        self.process_krfb:          QProcess | None = None
        self.process_streamer:      QProcess | None = None
        self.process_input_bridge:  QProcess | None = None
        self.process_tls_proxy:     QProcess | None = None
        self._gst_pids = set()

        self._proc_adb_dev:  QProcess | None = None
        self._proc_adb_fwd:  QProcess | None = None
        self._proc_adb_fwd2:  QProcess | None = None

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        
        self._zc = None
        self._info = None

        
        self._network_timer = QTimer(self)
        self._network_timer.setInterval(5000)
        self._network_timer.timeout.connect(self._check_network_ip)
        self._network_timer.start()

        
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon(os.path.join(LINUX_DIR, "assets", "tray", "icon_tray_white.svg")))
        self._tray.setToolTip("Monitorize")

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self._restore_from_tray)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self._quit_app)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.hide()

        
        self.quick_widget = QQuickWidget(self)
        self.quick_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        
        self.quick_widget.rootContext().setContextProperty("backend", self)

        
        qml_path = os.path.join(LINUX_DIR, "gui", "main.qml")
        self.quick_widget.setSource(QUrl.fromLocalFile(qml_path))

        
        errors = self.quick_widget.errors()
        if errors:
            print("=== QML ERRORS ===")
            for err in errors:
                print(err.toString())
            print("==================")

        self.setCentralWidget(self.quick_widget)

    
    @pyqtProperty(str, notify=detectedDeChanged)
    def detectedDe(self):
        return self._detected_de

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(str, notify=usbStatusTextChanged)
    def usbStatusText(self):
        return self._usb_status_text

    @pyqtProperty(bool, notify=usbBusyChanged)
    def usbBusy(self):
        return self._usb_busy

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self._is_streaming

    @pyqtProperty(int, notify=countdownChanged)
    def countdown(self):
        return self._countdown

    @pyqtProperty(str, notify=streamingStatusChanged)
    def streamingStatus(self):
        return self._streaming_status

    @pyqtProperty(str, notify=pairingCodeChanged)
    def pairingCode(self):
        return self._pairing_code

    @pyqtProperty(bool, notify=isReceivingChanged)
    def isReceiving(self):
        return self._is_receiving

    @pyqtProperty(str, notify=receiverStatusChanged)
    def receiverStatus(self):
        return self._receiver_status

    @pyqtProperty(str, notify=receiverHostIpChanged)
    def receiverHostIp(self):
        return self._receiver_host_ip

    @pyqtProperty(int, notify=receiverRotationChanged)
    def receiverRotation(self):
        return self._receiver_rotation

    @pyqtProperty('QVariant', notify=discoveredDevicesChanged)
    def discoveredDevices(self):
        return list(self._discovered_devices)

    @pyqtProperty(bool, notify=secondStreamActiveChanged)
    def secondStreamActive(self):
        return self._second_stream_active

    
    def set_usb_status_text(self, text):
        self._usb_status_text = text
        self.usbStatusTextChanged.emit(text)

    def set_usb_busy(self, busy):
        self._usb_busy = busy
        self.usbBusyChanged.emit(busy)

    def set_is_streaming(self, streaming):
        self._is_streaming = streaming
        self.isStreamingChanged.emit(streaming)

    def set_streaming_status(self, text):
        self._streaming_status = text
        self.streamingStatusChanged.emit(text)

    def append_log(self, type, msg):
        self.logAppended.emit(type, msg)

    def set_is_receiving(self, receiving):
        self._is_receiving = receiving
        self.isReceivingChanged.emit(receiving)

    def set_receiver_status(self, text):
        self._receiver_status = text
        self.receiverStatusChanged.emit(text)

    def set_receiver_host_ip(self, ip):
        self._receiver_host_ip = ip
        self.receiverHostIpChanged.emit(ip)

    
    @pyqtSlot()
    def startUsbScan(self):
        """Begin the ADB device scan → port-forward chain for USB mode."""
        self.set_usb_busy(True)
        self.set_usb_status_text("Running adb devices…")

        self._proc_adb_dev = QProcess(self)
        self._proc_adb_dev.finished.connect(self._adb_devices_done)
        self._proc_adb_dev.start("adb", ["devices"])

    def _adb_devices_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Error: adb devices failed. Is ADB installed?")
            self.set_usb_busy(False)
            return

        self.set_usb_status_text("Setting up reverse proxy tcp:7110 (video)…")
        self._proc_adb_fwd = QProcess(self)
        self._proc_adb_fwd.finished.connect(self._adb_forward_done)
        self._proc_adb_fwd.start("adb", ["reverse", "tcp:7110", "tcp:7112"])

    def _adb_forward_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Error: Reverse port setup failed. Is a device connected?")
            self.set_usb_busy(False)
            return

        self.set_usb_status_text("Setting up reverse proxy tcp:7111 (touch)…")
        self._proc_adb_fwd2 = QProcess(self)
        self._proc_adb_fwd2.finished.connect(self._adb_forward2_done)
        self._proc_adb_fwd2.start("adb", ["reverse", "tcp:7111", "tcp:7111"])

    def _adb_forward2_done(self, exit_code, _status):
        if exit_code != 0:
            self.set_usb_status_text("Warning: tcp:7111 reverse failed — touch disabled")
        else:
            self.set_usb_status_text("Device ready!")
        self.set_usb_busy(False)

    @pyqtSlot()
    def resetUsbStatus(self):
        self.set_usb_status_text("")
        self.set_usb_busy(False)

    @pyqtSlot(result='QVariant')
    def loadUsbSettings(self):
        return load_usb_settings()

    @pyqtSlot(result='QVariant')
    def loadWifiSettings(self):
        return load_wifi_settings()

    @pyqtSlot(result='QVariant')
    def loadGeneralSettings(self):
        return load_general_settings()

    @pyqtSlot(bool, bool, bool)
    def saveGeneralSettings(self, minimize_to_tray, enable_touch, enable_stylus_features):
        save_general_settings(
            minimize_to_tray=minimize_to_tray,
            enable_touch=enable_touch,
            enable_stylus_features=enable_stylus_features,
        )

    @pyqtSlot(str, str, str, str, str, str, str, str)
    def saveUsbSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder):
        save_usb_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            bitrate=bitrate,
            display_type=display_type,
            encoder=encoder
        )

    @pyqtSlot(str, str, str, str, str, str, str, str, str, bool)
    def saveWifiSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder, stream_type, use_encryption):
        save_wifi_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            bitrate=bitrate,
            display_type=display_type,
            encoder=encoder,
            stream_type=stream_type,
            use_encryption=use_encryption,
        )

    @pyqtSlot(result='QVariant')
    def loadSecondDisplaySettings(self):
        return load_second_display_settings()

    @pyqtSlot(str, str, str, str)
    def saveSecondDisplaySettings(self, resolution, fps, bitrate, encoder):
        save_second_display_settings(
            resolution=resolution,
            fps=fps,
            bitrate=bitrate,
            encoder=encoder
        )

    @pyqtSlot(result='QVariant')
    def loadReceiverSettings(self):
        return load_receiver_settings()

    @pyqtSlot(str, str, bool)
    def saveReceiverSettings(self, ip, port, use_encryption):
        save_receiver_settings(ip=ip, port=port, use_encryption=use_encryption)

    @pyqtSlot(str, str, result=bool)
    def receiverNeedsPairing(self, host, advertised_fingerprint):
        fingerprint, token = load_receiver_credentials(host)
        return not token or bool(advertised_fingerprint and fingerprint != advertised_fingerprint)

    @pyqtSlot()
    def stopStreaming(self):
        self._on_stop_streaming()

    @pyqtSlot()
    def configureDisplay(self):
        self._on_configure_display()

    

    @pyqtSlot()
    def startHostDiscovery(self):
        """Start mDNS/Zeroconf browsing for other Monitorize hosts."""
        self._stopHostDiscoveryInternal()
        self._discovered_devices = []
        self.discoveredDevicesChanged.emit()

        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
            import socket as _socket

            class _Listener(ServiceListener):
                def __init__(self, window):
                    self._window = window

                def add_service(self, zc, type_, name):
                    info = zc.get_service_info(type_, name)
                    if info and info.addresses:
                        ip = _socket.inet_ntoa(info.addresses[0])
                        port = info.port
                        host_name = info.properties.get(b'name', b'Unknown').decode('utf-8', errors='replace')
                        encrypted = info.properties.get(b'encrypted', b'0') == b'1'
                        fingerprint = info.properties.get(b'fingerprint', b'').decode('ascii', errors='ignore')
                        third_value = info.properties.get(b'third_available')
                        third_available = None if third_value is None else third_value == b'1'
                        third_port = int(info.properties.get(b'third_port', b'7114'))
                        QTimer.singleShot(
                            0,
                            lambda: self._window._add_discovered_device(
                                host_name, ip, port, encrypted, fingerprint,
                                third_available, third_port,
                            )
                        )

                def remove_service(self, zc, type_, name):
                    pass

                def update_service(self, zc, type_, name):
                    self.add_service(zc, type_, name)

            self._discovery_zc = Zeroconf()
            self._discovery_browser = ServiceBrowser(
                self._discovery_zc,
                "_monitorize._tcp.local.",
                _Listener(self)
            )
            print("[Receiver] Host discovery started")
        except Exception as e:
            print(f"[Receiver] Discovery failed: {e}")

    @pyqtSlot()
    def stopHostDiscovery(self):
        """Stop mDNS browsing."""
        self._stopHostDiscoveryInternal()

    def _stopHostDiscoveryInternal(self):
        if self._discovery_browser is not None:
            self._discovery_browser.cancel()
            self._discovery_browser = None
        if self._discovery_zc is not None:
            try:
                self._discovery_zc.close()
            except Exception:
                pass
            self._discovery_zc = None

    def _add_discovered_device(
        self, name, ip, port, encrypted=False, fingerprint="",
        third_available=False, third_port=7114,
    ):
        """Add a discovered host to the list (called on main thread via QTimer)."""
        for dev in self._discovered_devices:
            if dev.get('ip') == ip and dev.get('port') == port:
                dev.update({
                    'name': name,
                    'encrypted': encrypted,
                    'fingerprint': fingerprint,
                    'thirdAvailable': third_available,
                    'thirdPort': third_port,
                })
                self.discoveredDevicesChanged.emit()
                return  
        self._discovered_devices.append({
            'name': name, 'ip': ip, 'port': port,
            'encrypted': encrypted, 'fingerprint': fingerprint,
            'thirdAvailable': third_available, 'thirdPort': third_port,
        })
        self.discoveredDevicesChanged.emit()
        print(f"[Receiver] Discovered host: {name} ({ip}:{port})")

    @pyqtSlot(str, int, bool, str, str)
    def connectToHost(self, host_ip, port=7110, encrypted=False, fingerprint="", pairing_code=""):
        """Launch the GStreamer receiver pipeline to display the remote stream."""
        self._stopHostDiscoveryInternal()
        self._kill_receiver_proc()

        self._receiver_stopping = False
        self._receiver_host = host_ip
        self._receiver_port = port
        self._receiver_encrypted = encrypted
        self._receiver_fingerprint = fingerprint
        self._receiver_pairing_code = pairing_code
        self._receiver_retry_count = 0
        self._receiver_retry_pending = False
        self._receiver_auth_failed = False
        self.set_receiver_host_ip(f"{host_ip}:{port}")
        self.set_receiver_status(f"Connecting to {host_ip}:{port}…")
        self.receiverLogAppended.emit(f"Connecting to {host_ip} on port {port}…")

        self._start_receiver_attempt()

    def _start_receiver_attempt(self):
        self._receiver_retry_pending = False
        self._receiver_auth_failed = False
        if self._receiver_encrypted:
            host_ip = self._receiver_host
            port = self._receiver_port
            saved_fingerprint, token = load_receiver_credentials(host_ip)
            if self._receiver_pairing_code:
                saved_fingerprint, token = self._receiver_fingerprint, ""
            self.process_receiver_tls = QProcess(self)
            self.process_receiver_tls.setWorkingDirectory(LINUX_DIR)
            self.process_receiver_tls.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.process_receiver_tls.readyReadStandardOutput.connect(self._read_receiver_tls)
            self.process_receiver_tls.finished.connect(self._on_receiver_tls_finished)
            args = [
                os.path.join(LINUX_DIR, "tls_receiver.py"),
                host_ip, str(port),
            ]
            if saved_fingerprint:
                args += ["--fingerprint", saved_fingerprint]
            if token:
                args += ["--token", token]
            elif self._receiver_pairing_code:
                args += ["--code", self._receiver_pairing_code]
            self.process_receiver_tls.start(sys.executable, args)
            return

        self._launch_receiver_pipeline(self._receiver_host, self._receiver_port)

    def _launch_receiver_pipeline(self, host_ip, port):
        self._receiver_attempt_started = time.monotonic()
        self.process_receiver = QProcess(self)
        self.process_receiver.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_receiver.started.connect(self._on_receiver_started)
        self.process_receiver.readyReadStandardOutput.connect(self._read_receiver)
        self.process_receiver.finished.connect(self._on_receiver_finished)
        self.process_receiver.errorOccurred.connect(self._on_receiver_error)

        self.process_receiver.start(sys.executable, [
            os.path.join(LINUX_DIR, "receiver_player.py"),
            host_ip, str(port), str(self._receiver_rotation),
        ])

    @pyqtSlot()
    def rotateReceiver(self):
        self._receiver_rotation = (self._receiver_rotation + 1) % 4
        save_receiver_rotation(self._receiver_rotation)
        self.receiverRotationChanged.emit(self._receiver_rotation)
        if (
            self.process_receiver is not None
            and self.process_receiver.state() == QProcess.ProcessState.Running
        ):
            self.process_receiver.write(f"ROTATE {self._receiver_rotation}\n".encode())

    def _on_receiver_started(self):
        self.set_receiver_status(
            "Waiting for Third display stream…"
            if self._receiver_port == 7114 else "Waiting for Second display stream…"
        )
        self._receiver_stable_timer.start(2000)

    def _mark_receiver_stable(self):
        if (
            self.process_receiver is not None
            and self.process_receiver.state() == QProcess.ProcessState.Running
        ):
            self._inhibit_sleep()
            self.set_is_receiving(True)
            self.set_receiver_status(f"Receiving from {self._receiver_host}:{self._receiver_port}")
            self.receiverLogAppended.emit("Stream connected and stable.")

    def _read_receiver_tls(self):
        if self.process_receiver_tls is None:
            return
        raw = bytes(self.process_receiver_tls.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._receiver_tls_buffer += raw
        lines = self._receiver_tls_buffer.split("\n")
        self._receiver_tls_buffer = lines.pop() if not self._receiver_tls_buffer.endswith("\n") else ""
        for line in lines:
            if line == "[TLS RECEIVER] READY" and self.process_receiver is None:
                self._launch_receiver_pipeline("127.0.0.1", 17110)
            elif line.startswith("[TLS RECEIVER] CREDENTIALS "):
                fingerprint, token = line.removeprefix("[TLS RECEIVER] CREDENTIALS ").split()
                save_receiver_credentials(self._receiver_host, fingerprint, token)
                self._receiver_pairing_code = ""
                self.set_receiver_status("Authenticated; starting encrypted stream…")
            elif line.startswith("[TLS RECEIVER] AUTH_FAILED"):
                self._receiver_auth_failed = True
                fingerprint = line.removeprefix("[TLS RECEIVER] AUTH_FAILED").strip()
                clear_receiver_credentials(self._receiver_host)
                self.set_receiver_status("Pairing required")
                self.receiverPairingRequired.emit(
                    self._receiver_host, self._receiver_port, fingerprint
                )
            elif line.startswith("[TLS RECEIVER] ERROR "):
                self.set_receiver_status(line.removeprefix("[TLS RECEIVER] ERROR "))
            elif line:
                self.receiverLogAppended.emit(line)

    def _on_receiver_tls_finished(self, code, _status):
        if (
            code != 0
            and not self._receiver_auth_failed
            and not self._is_receiving
            and not self._receiver_retry_pending
        ):
            self.set_receiver_status("Encrypted connection failed")

    def _on_receiver_error(self, _error):
        if self.process_receiver is not None:
            self.set_receiver_status(self.process_receiver.errorString())

    @pyqtSlot()
    def stopReceiving(self):
        """Stop the receiver pipeline and return to the main menu."""
        self._kill_receiver_proc()
        self.set_is_receiving(False)

    def _read_receiver(self):
        if self.process_receiver is None:
            return
        raw = bytes(self.process_receiver.readAllStandardOutput()).decode('utf-8', errors='replace')
        self.receiverLogAppended.emit(raw)
        if "ERROR" in raw:
            self.set_receiver_status("Error — see logs")

    def _on_receiver_finished(self, code, _status):
        self.receiverLogAppended.emit(f"Receiver process exited (code {code})")
        self._receiver_stable_timer.stop()
        elapsed = time.monotonic() - self._receiver_attempt_started
        if not self._receiver_stopping and elapsed < 2.0 and self._receiver_retry_count < 9:
            self._receiver_retry_count += 1
            self._receiver_retry_pending = True
            self.set_receiver_status(
                f"Waiting for {'Third' if self._receiver_port == 7114 else 'Second'} display stream… "
                f"({self._receiver_retry_count}/10)"
            )
            if (
                self.process_receiver_tls is not None
                and self.process_receiver_tls.state() != QProcess.ProcessState.NotRunning
            ):
                self.process_receiver_tls.terminate()
            self.process_receiver = None
            self.process_receiver_tls = None
            self._receiver_retry_timer.start(1000)
            return
        if self._is_receiving:
            self.set_receiver_status("Disconnected")
            self.receiverLogAppended.emit("Stream ended. Click Disconnect to return.")
        else:
            self.set_receiver_status("Unable to start stream after 10 attempts")

    def _kill_receiver_proc(self):
        self._receiver_stopping = True
        self._receiver_stable_timer.stop()
        self._receiver_retry_timer.stop()
        if self.process_receiver is not None and self.process_receiver.state() != QProcess.ProcessState.NotRunning:
            self.process_receiver.terminate()
            if not self.process_receiver.waitForFinished(3000):
                self.process_receiver.kill()
        self.process_receiver = None
        if self.process_receiver_tls is not None and self.process_receiver_tls.state() != QProcess.ProcessState.NotRunning:
            self.process_receiver_tls.terminate()
            if not self.process_receiver_tls.waitForFinished(3000):
                self.process_receiver_tls.kill()
        self.process_receiver_tls = None
        self._receiver_tls_buffer = ""
        self._receiver_auth_failed = False
        self._receiver_retry_pending = False
        
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*tcpclientsrc"], capture_output=True)
        self._uninhibit_sleep()

    def _inhibit_sleep(self):
        """Prevent the host system from sleeping/suspending during active receiving."""
        try:
            
            if self._detected_de == "kde":
                cmd = [
                    "dbus-send", "--session", "--print-reply",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.Inhibit",
                    "string:Monitorize", "string:Streaming display receiver active"
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if "uint32" in line:
                            cookie_str = line.split("uint32")[-1].strip()
                            self._kde_inhibit_cookie = int(cookie_str)
                            print(f"[Receiver] Sleep inhibited (KDE cookie: {self._kde_inhibit_cookie})")
                            break
            
            elif self._detected_de == "hyprland":
                subprocess.run(["pkill", "-USR1", "hypridle"], capture_output=True)
                print("[Receiver] Sleep inhibited (Hyprland hypridle paused)")
        except Exception as e:
            print(f"[Receiver] Failed to inhibit sleep: {e}")

    def _uninhibit_sleep(self):
        """Restore default system sleep/suspend policies."""
        try:
            
            if self._detected_de == "kde" and getattr(self, "_kde_inhibit_cookie", None) is not None:
                cmd = [
                    "dbus-send", "--session",
                    "--dest=org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.UnInhibit",
                    f"uint32:{self._kde_inhibit_cookie}"
                ]
                subprocess.run(cmd, capture_output=True)
                print(f"[Receiver] Sleep uninhibited (KDE cookie {self._kde_inhibit_cookie} released)")
                self._kde_inhibit_cookie = None
            
            elif self._detected_de == "hyprland":
                subprocess.run(["pkill", "-USR2", "hypridle"], capture_output=True)
                print("[Receiver] Sleep uninhibited (Hyprland hypridle resumed)")
        except Exception as e:
            print(f"[Receiver] Failed to uninhibit sleep: {e}")

    def _start_krfb(self, process, resolution, name, port):
        password = secrets.token_urlsafe(6)
        args = [
            "--resolution", resolution,
            "--name", name,
            "--password", password,
            "--port", str(port),
        ]
        process.start("krfb-virtualmonitor", args)

    

    @pyqtSlot(str, str, str, str)
    def startSecondStream(self, res, fps, bitrate, encoder):
        """Launch a second virtual monitor + streamer on port 7114 (KDE only)."""
        if self._second_stream_active:
            return

        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7114"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7115"], capture_output=True)

        try:
            clean_res = res.split()[0] if res else ""
            s2_w, s2_h = map(int, clean_res.split("x"))
        except ValueError:
            s2_w, s2_h = 1920, 1200
        s2_fps = int(fps)
        s2_bitrate = int(bitrate)

        
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        encoder_map = {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
            "Software (CPU / x264enc)": "cpu"
        }
        pref = encoder_map.get(encoder, "cpu")
        env.insert("MONITORIZE_ENCODER", pref)
        if getattr(self, "_wifi_encryption", False):
            env.insert("MONITORIZE_HOST", "127.0.0.1")
            env.insert("MONITORIZE_PORT", "7115")

        self._s2_w = s2_w
        self._s2_h = s2_h
        self._s2_fps = s2_fps
        self._s2_bitrate = s2_bitrate
        self._s2_env = env

        self._second_stream_active = True
        self._third_stream_ready = False
        self.secondStreamActiveChanged.emit(True)

        self.append_log("STREAMER", f"[Third display] Spawning virtual monitor: {s2_w}x{s2_h}")

        
        self.process_krfb2 = QProcess(self)
        self.process_krfb2.setWorkingDirectory(LINUX_DIR)
        self.process_krfb2.setProcessEnvironment(env)
        self.process_krfb2.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_krfb2.readyReadStandardOutput.connect(self._read_krfb2)
        self.process_krfb2.finished.connect(
            lambda code, _: self.append_log("STREAMER", f"[Third display] KRFB exited (code {code})")
        )

        self._start_krfb(
            self.process_krfb2, f"{s2_w}x{s2_h}", "TabletDisplay2", 5901
        )

        
        QTimer.singleShot(5000, self._launch_second_streamer)

    def _read_krfb2(self):
        if self.process_krfb2 is None:
            return
        raw = bytes(self.process_krfb2.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("STREAMER", f"[Third display KRFB] {raw}")

    def _launch_second_streamer(self):
        """Launch second Streamer_kde.py on port 7114."""
        if not self._second_stream_active:
            return

        self.process_streamer2 = QProcess(self)
        self.process_streamer2.setWorkingDirectory(LINUX_DIR)
        self.process_streamer2.setProcessEnvironment(self._s2_env)
        self.process_streamer2.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_streamer2.readyReadStandardOutput.connect(self._read_streamer2)
        self.process_streamer2.finished.connect(self._on_streamer2_finished)

        script_path = os.path.join(LINUX_DIR, "Streamer_kde.py")
        args = [
            script_path,
            str(self._s2_w),
            str(self._s2_h),
            str(self._s2_fps),
            str(self._s2_bitrate),
            "wifi",
            "7114",  
        ]
        self.process_streamer2.start(sys.executable, args)
        self.append_log("STREAMER", "[Third display] Streamer launched on port 7114. Select 'TabletDisplay2' in the KDE picker.")

    def _read_streamer2(self):
        if self.process_streamer2 is None:
            return
        raw = bytes(self.process_streamer2.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("STREAMER", f"[Third display] {raw}")
        for line in raw.splitlines():
            if "Setting pipeline to PLAYING" in line or "New clock:" in line:
                self._third_stream_ready = True
                if self._is_streaming and self._is_wifi:
                    self._update_zeroconf_registration(self._local_ip)
            elif "Got EOS" in line or "[GStreamer] EXITED:" in line:
                self._third_stream_ready = False
                if self._is_streaming and self._is_wifi:
                    self._update_zeroconf_registration(self._local_ip)
            if "[GStreamer] PID:" in line:
                try:
                    pid = int(line.split("PID:")[1].strip())
                    self._gst_pids2.add(pid)
                    print(f"[GUI] Tracked GStreamer PID (third display): {pid}")
                except Exception:
                    pass

    def _on_streamer2_finished(self, code, _status):
        self.append_log("STREAMER", f"[Third display] Streamer exited (code {code})")
        self._third_stream_ready = False
        if self._second_stream_active:
            self._second_stream_active = False
            self.secondStreamActiveChanged.emit(False)
        if self._is_streaming and self._is_wifi:
            self._update_zeroconf_registration(self._local_ip)

    @pyqtSlot()
    def stopSecondStream(self):
        """Stop the second display stream."""
        self._kill_second_stream_procs()
        self._second_stream_active = False
        self._third_stream_ready = False
        self.secondStreamActiveChanged.emit(False)
        if self._is_streaming and self._is_wifi:
            self._update_zeroconf_registration(self._local_ip)
        self.append_log("STREAMER", "[Third display] Stopped.")

    def _kill_second_stream_procs(self):
        """Terminate second display processes."""
        for proc in (self.process_krfb2, self.process_streamer2):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self.process_krfb2 = None
        self.process_streamer2 = None
        self._third_stream_ready = False

        for pid in list(self._gst_pids2):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self._gst_pids2.clear()
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7114"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7115"], capture_output=True)

    @pyqtSlot(str, str, str, str, str, bool)
    def startStreaming(self, res, fps, bitrate, display_type, encoder, is_wifi):
        """Parse streaming parameters from the QML UI and launch the
        appropriate DE-specific streamer subprocess."""
        self._is_wifi = is_wifi
        try:
            clean_res = res.split()[0] if res else ""
            self._stream_width, self._stream_height = map(int, clean_res.split("x"))
        except ValueError:
            self._stream_width, self._stream_height = 1920, 1200
        self._stream_fps = int(fps)
        self._stream_bitrate = int(bitrate)
        self._selected_display_type = display_type
        self._selected_encoder = encoder

        self._do_start_streaming()

    def _do_start_streaming(self):
        """Prepare the environment, kill stale processes, and trigger
        the streamer launch with desktop-specific virtual-monitor setup."""
        script_dir = LINUX_DIR

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")

        
        encoder_map = {
            "NVIDIA NVENC (nvh264enc)": "nvidia",
            "Intel/AMD VA-API (vah264enc)": "vaapi",
            "Software (CPU / x264enc)": "cpu"
        }
        pref = encoder_map.get(self._selected_encoder, "cpu")
        env.insert("MONITORIZE_ENCODER", pref)

        
        if self._is_wifi:
            wifi_settings = load_wifi_settings()
            stream_type = wifi_settings.get("stream_type", "Speed")
            self._wifi_encryption = wifi_settings.get("use_encryption", True)
        else:
            stream_type = "Speed"
            self._wifi_encryption = False
        env.insert("MONITORIZE_STREAM_TYPE", stream_type)
        if self._wifi_encryption:
            env.insert("MONITORIZE_HOST", "127.0.0.1")
            env.insert("MONITORIZE_PORT", "7112")
        if self._detected_de in ("kde", "hyprland") and self._selected_display_type == "Extend":
            env.insert("MONITORIZE_PRESERVE_SOURCE_SIZE", "1")

        self._script_dir = script_dir
        self._env        = env

        
        for pid in list(self._gst_pids):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self._gst_pids.clear()
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7110"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7112"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "Streamer_.*\\.py"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "tls_proxy.py"], capture_output=True)

        self._cleanup_zeroconf()

        if self._is_wifi:
            subprocess.run(["adb", "reverse", "--remove", "tcp:7110"], capture_output=True)
            subprocess.run(["adb", "reverse", "--remove", "tcp:7111"], capture_output=True)
            if self._wifi_encryption:
                self._launch_tls_proxy()

        
        self.set_is_streaming(True)

        if self._detected_de == "kde":
            if self._selected_display_type == "Mirror":
                self.set_streaming_status("Launching streamer (Mirror mode)…")
                self._launch_streamer()
            else:
                self.set_streaming_status("Starting virtual monitor…  5")
                self.process_krfb = QProcess(self)
                self.process_krfb.setWorkingDirectory(script_dir)
                self.process_krfb.setProcessEnvironment(env)
                self.process_krfb.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
                self.process_krfb.readyReadStandardOutput.connect(self._read_krfb)
                self.process_krfb.finished.connect(
                    lambda code, _: self.append_log("KRFB", f"Process exited (code {code})")
                )
                subprocess.run(["killall", "krfb-virtualmonitor"], capture_output=True)

                self._start_krfb(
                    self.process_krfb,
                    f"{self._stream_width}x{self._stream_height}",
                    "TabletDisplay",
                    5900,
                )
                self._countdown = 1
                self.countdownChanged.emit(self._countdown)
                self._countdown_timer.start()
        elif self._detected_de == "hyprland":
            if self._selected_display_type == "Mirror":
                self.set_streaming_status("Launching streamer (Mirror mode)…")
                self._launch_streamer()
            else:
                self.set_streaming_status("Setting up virtual monitor on Hyprland…")
                old_monitors = set(self._get_current_headless_monitors())
                subprocess.run(["hyprctl", "output", "create", "headless"], capture_output=True)

                new_monitors = set(self._get_current_headless_monitors())
                diff = new_monitors - old_monitors
                if diff:
                    new_name = list(diff)[0]
                else:
                    new_name = "HEADLESS-1"

                self.created_headless_monitor = new_name

                subprocess.run(["hyprctl", "keyword", "monitor", f"{new_name},{self._stream_width}x{self._stream_height}@{self._stream_fps},auto,1"], capture_output=True)
                subprocess.run(["hyprctl", "eval", f"hl.monitor({{ output = '{new_name}', mode = '{self._stream_width}x{self._stream_height}@{self._stream_fps}', position = 'auto', scale = 1.0 }})"], capture_output=True)
                print(f"[Hyprland] Created new headless monitor: {new_name} at {self._stream_width}x{self._stream_height}@{self._stream_fps}")
                self.append_log("STREAMER", f"Created new headless monitor: {new_name} at {self._stream_width}x{self._stream_height}@{self._stream_fps}")
                self.set_streaming_status("Waiting for virtual monitor to initialize…  2")
                self._countdown = 2
                self.countdownChanged.emit(self._countdown)
                self._countdown_timer.start()
        else:
            self.set_streaming_status("Launching streamer…")
            self._launch_streamer()

    def _countdown_tick(self):
        self._countdown -= 1
        self.countdownChanged.emit(self._countdown)

        if self._countdown > 0:
            self.set_streaming_status(f"Starting virtual monitor…  {self._countdown}")
            return

        self._countdown_timer.stop()
        self._launch_streamer()

    def _launch_streamer(self):
        """Spawn the correct DE-specific streamer script as a QProcess."""
        self.process_streamer = QProcess(self)
        self.process_streamer.setWorkingDirectory(self._script_dir)
        self.process_streamer.setProcessEnvironment(self._env)
        self.process_streamer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_streamer.readyReadStandardOutput.connect(self._read_streamer)
        self.process_streamer.finished.connect(self._on_streamer_finished)

        _streamer_map = {
            "kde":      "Streamer_kde.py",
            "gnome":    "Streamer_gnome.py",
            "hyprland": "Streamer_hyprland.py",
        }
        script_name = _streamer_map.get(self._detected_de, "Streamer_gnome.py")
        script_path = os.path.join(self._script_dir, script_name)

        args = [
            script_path,
            str(self._stream_width),
            str(self._stream_height),
            str(self._stream_fps),
            str(self._stream_bitrate),
        ]
        if self._is_wifi:
            args.append("wifi")
        else:
            args.append("usb")

        if self._detected_de == "hyprland":
            if getattr(self, "created_headless_monitor", None):
                args.append(self.created_headless_monitor)
            else:
                args.append("mirror")

        if self._detected_de == "gnome":
            args.append("1.0") 
            args.append(self._selected_display_type.replace(" ", "_"))

        self.process_streamer.start(sys.executable, args)

        if self._is_wifi:
            self._setup_zeroconf()

        if self._detected_de in ("kde", "gnome"):
            QTimer.singleShot(400, self._launch_input_bridge)
        elif self._detected_de == "hyprland":
            self._input_bridge_launched = False
            self._streamer_buffer = ""

        self.set_streaming_status("Status: Streaming…")

    def _launch_tls_proxy(self):
        self.process_tls_proxy = QProcess(self)
        self.process_tls_proxy.setWorkingDirectory(LINUX_DIR)
        self.process_tls_proxy.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_tls_proxy.readyReadStandardOutput.connect(self._read_tls_proxy)
        self.process_tls_proxy.start(sys.executable, [os.path.join(LINUX_DIR, "tls_proxy.py")])

    def _read_tls_proxy(self):
        if self.process_tls_proxy is None:
            return
        raw = bytes(self.process_tls_proxy.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._tls_proxy_buffer += raw
        lines = self._tls_proxy_buffer.split("\n")
        self._tls_proxy_buffer = lines.pop() if not self._tls_proxy_buffer.endswith("\n") else ""
        for line in lines:
            if line.startswith("[TLS CONTROL] PAIRING_CODE "):
                code = line.removeprefix("[TLS CONTROL] PAIRING_CODE ").strip()
                self._pairing_code = code
                self.pairingCodeChanged.emit(code)
            if "Pairing accepted" in line or "Client authenticated" in line:
                self.set_streaming_status("Status: Streaming securely")

    def _launch_input_bridge(self):
        """Spawn touch_daemon.py to relay Android touch/pen events."""
        gen = load_general_settings()
        touch_enabled = gen.get("enable_touch", True)
        stylus_features_enabled = (
            gen.get("enable_stylus_features", False)
            and self._detected_de in ("kde", "gnome", "hyprland")
        )
        if not touch_enabled and not stylus_features_enabled:
            self.append_log("INPUT", "Input is disabled in settings.")
            self.set_streaming_status("Status: Streaming (Input Disabled)")
            return

        self.process_input_bridge = QProcess(self)
        self.process_input_bridge.setWorkingDirectory(self._script_dir)
        self.process_input_bridge.setProcessEnvironment(self._env)
        self.process_input_bridge.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process_input_bridge.readyReadStandardOutput.connect(self._read_input_bridge)
        self.process_input_bridge.finished.connect(self._on_input_bridge_finished)
        args = [
            os.path.join(self._script_dir, "touch_daemon.py"),
            str(self._stream_width),
            str(self._stream_height),
        ]
        if getattr(self, "_is_wifi", False) and not getattr(self, "_wifi_encryption", False):
            args.append("--wifi")
        if stylus_features_enabled:
            args.append("--stylus-features")
        if stylus_features_enabled and not touch_enabled:
            args.append("--stylus-only")
        self.process_input_bridge.start(sys.executable, args)

        if "--stylus-features" in args:
            self.set_streaming_status("Stylus input starting via uinput…")
        elif self._detected_de == "hyprland":
            self.set_streaming_status("Touch service starting via uinput…")
        else:
            self.set_streaming_status("Touch service starting…")

    def _on_streamer_finished(self, code, _status):
        self.append_log("STREAMER", f"Process exited (code {code})")

        if (self._detected_de == "gnome" and code != 0 and self.isStreaming):
            self.append_log("STREAMER", "↺  GNOME streamer crashed — auto-restarting in 2s…")
            self.set_streaming_status("↺  Stream reconnecting after display config change…")
            QTimer.singleShot(2000, self._gnome_restart_streamer)

    def _on_input_bridge_finished(self, code, _status):
        self.append_log("INPUT", f"Bridge exited (code {code})")
        if code == 0 and self.isStreaming:
            self.append_log("INPUT", "ℹ️  Touch input not available — streaming continues without touch.")

    def _gnome_restart_streamer(self):
        if not self.isStreaming:
            return
        for pid in list(self._gst_pids):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self._gst_pids.clear()
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7110"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7112"], capture_output=True)
        self._launch_streamer()
        self.set_streaming_status("Status: Streaming…  (restarted)")

    def _read_krfb(self):
        if self.process_krfb is None:
            return
        raw = bytes(self.process_krfb.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("KRFB", raw)

    def _read_streamer(self):
        if self.process_streamer is None:
            return
        raw = bytes(self.process_streamer.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("STREAMER", raw)

        
        for line in raw.splitlines():
            if "[GStreamer] PID:" in line:
                try:
                    pid = int(line.split("PID:")[1].strip())
                    self._gst_pids.add(pid)
                    print(f"[GUI] Tracked GStreamer PID: {pid}")
                except Exception:
                    pass

        if self._detected_de == "hyprland":
            if not getattr(self, "_input_bridge_launched", False):
                if not hasattr(self, "_streamer_buffer"):
                    self._streamer_buffer = ""
                self._streamer_buffer += raw
                if "[Portal] Got PipeWire node=" in self._streamer_buffer:
                    self._input_bridge_launched = True
                    QTimer.singleShot(500, self._launch_input_bridge)

    def _read_input_bridge(self):
        if self.process_input_bridge is None:
            return
        raw = bytes(self.process_input_bridge.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log("INPUT", raw)

    def _on_stop_streaming(self):
        """Terminate all streaming subprocesses and return to the main menu."""
        self._kill_stream_procs()
        self.set_is_streaming(False)
        self._cleanup_zeroconf()

    def _on_configure_display(self):
        """Launch nwg-displays for Hyprland display configuration."""
        cmd = "nwg-displays"
        if shutil.which(cmd) is None:
            QMessageBox.warning(self, "nwg-displays Not Installed", "nwg-displays is not installed.")
            return

        try:
            QProcess.startDetached(cmd)
        except Exception as e:
            if "not found" in str(e).lower() or isinstance(e, FileNotFoundError):
                QMessageBox.warning(self, "nwg-displays Not Installed", "nwg-displays is not installed.")
            else:
                QMessageBox.warning(self, "Error", f"Failed to run nwg-displays:\n{str(e)}")

    def _kill_stream_procs(self):
        """Terminate and clean up all streaming-related QProcess instances."""
        self._countdown_timer.stop()
        self._countdown = 0

        
        if self._second_stream_active:
            self._kill_second_stream_procs()
            self._second_stream_active = False
            self.secondStreamActiveChanged.emit(False)

        for proc in (self.process_krfb, self.process_streamer, self.process_input_bridge, self.process_tls_proxy):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
        self.process_krfb          = None
        self.process_streamer      = None
        self.process_input_bridge  = None
        self.process_tls_proxy     = None
        self._tls_proxy_buffer     = ""
        self._pairing_code         = ""
        self.pairingCodeChanged.emit("")

        
        for pid in list(self._gst_pids):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self._gst_pids.clear()
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7110"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7112"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7114"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "gst-launch-1.0.*port=7115"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "tls_proxy.py"], capture_output=True)

        if self._detected_de == "hyprland" and getattr(self, "created_headless_monitor", None):
            print(f"[Hyprland] Removing created headless monitor: {self.created_headless_monitor}")
            subprocess.run(["hyprctl", "output", "remove", self.created_headless_monitor], capture_output=True)
            self.created_headless_monitor = None

    def _get_current_headless_monitors(self):
        """Query hyprctl for currently existing HEADLESS-* monitor names."""
        headless_names = []
        try:
            res = subprocess.run(["hyprctl", "monitors", "all", "-j"], capture_output=True, text=True)
            if res.returncode == 0:
                import json
                monitors = json.loads(res.stdout)
                for mon in monitors:
                    name = mon.get("name", "")
                    if name.startswith("HEADLESS"):
                        headless_names.append(name)
                return headless_names
        except Exception: pass

        try:
            import re
            res = subprocess.run(["hyprctl", "monitors", "all"], capture_output=True, text=True)
            if res.returncode == 0:
                matches = re.findall(r"\bHEADLESS-\d+\b", res.stdout)
                headless_names = list(set(matches))
        except Exception: pass
        return headless_names

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self._tray.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        """Hard quit from the tray menu — always terminates processes."""
        self._kill_stream_procs()
        self._kill_receiver_proc()
        self._stopHostDiscoveryInternal()
        self._cleanup_zeroconf()
        QApplication.quit()

    def _setup_zeroconf(self):
        """Initial Zeroconf setup."""
        self._update_zeroconf_registration(self._local_ip)

    def _cleanup_zeroconf(self):
        """Clean up old registration and close Zeroconf instance if any."""
        if self._zc is None:
            return

        if self._info is not None:
            try:
                self._zc.unregister_service(self._info)
            except Exception:
                pass
        try:
            self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._info = None

    def _update_zeroconf_registration(self, ip_addr):
        """Clean up old registration and register the service under the new IP."""
        try:
            from zeroconf import ServiceInfo, Zeroconf
            import socket

            self._cleanup_zeroconf()

            
            hostname = socket.gethostname()
            self._zc = Zeroconf()
            desc = {
                'name': hostname,
                'port': 7110,
                'encrypted': '1' if getattr(self, "_wifi_encryption", False) else '0',
                'third_available': '1' if self._third_stream_ready else '0',
                'third_port': '7114',
            }
            if getattr(self, "_wifi_encryption", False):
                from tls_proxy import certificate_fingerprint
                desc['fingerprint'] = certificate_fingerprint()

            self._info = ServiceInfo(
                "_monitorize._tcp.local.",
                f"{hostname}._monitorize._tcp.local.",
                addresses=[socket.inet_aton(ip_addr)],
                port=7110,
                properties=desc,
                server=f"{hostname}.local.",
            )
            self._zc.register_service(self._info)
            print(f"[Zeroconf] Service registered successfully on {ip_addr}")
        except Exception as e:
            print("Zeroconf registration/update failed:", e)

    def _check_network_ip(self):
        """Periodically check the local IP and update Zeroconf registration on changes."""
        current_ip = get_local_ip()
        if current_ip != self._local_ip:
            print(f"[Network] IP changed from {self._local_ip} to {current_ip}")
            self._local_ip = current_ip
            self.localIpChanged.emit(current_ip)
            if self._is_streaming and self._is_wifi:
                self._update_zeroconf_registration(current_ip)

    def closeEvent(self, event):
        gen = load_general_settings()
        if gen.get("minimize_to_tray", False) and self._is_streaming:
            event.ignore()
            self.hide()
            self._tray.show()
            self._tray.showMessage(
                "Monitorize",
                "Running in the background. Double-click the tray icon to restore.",
                QSystemTrayIcon.MessageIcon.Information,
                5000
            )
        else:
            self._quit_app()
            event.accept()

    def _ask_desktop_environment(self) -> str:
        dlg = QDialog()
        dlg.setWindowTitle("Select Desktop Environment")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 28, 32, 28)

        lbl = QLabel(
            "Could not automatically detect your desktop environment.\n"
            "Please select which one you are running:"
        )
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        kde_btn = QPushButton("KDE Plasma")
        gnome_btn = QPushButton("GNOME")
        row1.addWidget(kde_btn)
        row1.addWidget(gnome_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        hypr_btn = QPushButton("Hyprland")
        other_btn = QPushButton("Other (WIP)")
        row2.addWidget(hypr_btn)
        row2.addWidget(other_btn)
        layout.addLayout(row2)

        selected_de = "gnome"

        def pick(value):
            nonlocal selected_de
            selected_de = value
            dlg.accept()

        kde_btn.clicked.connect(lambda: pick("kde"))
        gnome_btn.clicked.connect(lambda: pick("gnome"))
        hypr_btn.clicked.connect(lambda: pick("hyprland"))
        other_btn.clicked.connect(lambda: pick("other"))

        dlg.exec()
        if selected_de == "other":
            QMessageBox.information(
                None, "Work In Progress",
                "Support for other environments is coming soon. The app will close now."
            )
            sys.exit(0)
        return selected_de


def load_theme_color(property_name: str, default_color: str) -> str:
    try:
        theme_path = os.path.join(LINUX_DIR, "gui", "Theme.qml")
        if os.path.exists(theme_path):
            with open(theme_path, "r") as f:
                content = f.read()
            import re
            match = re.search(rf"property\s+color\s+{property_name}\s*:\s*\"([^\"]+)\"", content)
            if match:
                return match.group(1)
    except Exception:
        pass
    return default_color


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setDesktopFileName("monitorize")

    socket = QLocalSocket()
    socket.connectToServer("monitorize")
    if socket.waitForConnected(250):
        socket.write(b"show")
        socket.waitForBytesWritten(250)
        return

    QLocalServer.removeServer("monitorize")
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen("monitorize"):
        return

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(load_theme_color("background", "#1b1e24")))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(load_theme_color("textLight", "#eff0f1")))
    palette.setColor(QPalette.ColorRole.Base,            QColor(load_theme_color("surface", "#232831")))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(load_theme_color("surfaceAlt", "#2b313b")))
    palette.setColor(QPalette.ColorRole.Button,          QColor(load_theme_color("surfaceAlt", "#2b313b")))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(load_theme_color("textLight", "#eff0f1")))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(load_theme_color("accent", "#3daee9")))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(load_theme_color("textPrimary", "#eff0f1")))
    app.setPalette(palette)

    win = MonitorizeWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection().deleteLater(), win._restore_from_tray()))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
