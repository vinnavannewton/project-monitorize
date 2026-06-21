"""QML-facing Monitorize backend façade."""

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from gui.discovery_service import DiscoveryService
from gui.receiver_controller import ReceiverController
from gui.settings import (
    load_general_settings,
    load_receiver_credentials,
    load_receiver_settings,
    load_second_display_settings,
    load_usb_settings,
    load_wifi_settings,
    save_general_settings,
    save_receiver_settings,
    save_second_display_settings,
    save_usb_settings,
    save_wifi_settings,
)
from gui.streaming_controller import StreamingController
from gui.usb_controller import UsbController
from gui.utils import get_local_ip
from gui.validation import (
    normalize_host,
    sanitize_decoder,
    sanitize_port,
    valid_host,
    valid_port,
)


class MonitorizeBackend(QObject):
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
    discoveredDevicesChanged = pyqtSignal()
    receiverLogAppended = pyqtSignal(str)
    receiverPairingRequired = pyqtSignal(str, int, str)
    secondStreamActiveChanged = pyqtSignal(bool)
    configureDisplayRequested = pyqtSignal()

    def __init__(self, de, parent=None):
        super().__init__(parent)
        self._detected_de = de
        self._local_ip = get_local_ip()
        self.discovery = DiscoveryService(self)
        self.usb = UsbController(self)
        self.receiver = ReceiverController(de, self.discovery, self)
        self.streaming = StreamingController(de, self._local_ip, self.discovery, self)
        self._wire_signals()
        self.network_timer = QTimer(self)
        self.network_timer.setInterval(5000)
        self.network_timer.timeout.connect(self._check_network_ip)
        self.network_timer.start()

    def _wire_signals(self):
        self.usb.statusChanged.connect(self.usbStatusTextChanged)
        self.usb.busyChanged.connect(self.usbBusyChanged)
        self.discovery.devicesChanged.connect(self.discoveredDevicesChanged)
        self.receiver.receivingChanged.connect(self.isReceivingChanged)
        self.receiver.statusChanged.connect(self.receiverStatusChanged)
        self.receiver.hostChanged.connect(self.receiverHostIpChanged)
        self.receiver.logAppended.connect(self.receiverLogAppended)
        self.receiver.pairingRequired.connect(self.receiverPairingRequired)
        self.streaming.streamingChanged.connect(self.isStreamingChanged)
        self.streaming.statusChanged.connect(self.streamingStatusChanged)
        self.streaming.countdownChanged.connect(self.countdownChanged)
        self.streaming.pairingCodeChanged.connect(self.pairingCodeChanged)
        self.streaming.secondStreamChanged.connect(self.secondStreamActiveChanged)
        self.streaming.logAppended.connect(self.logAppended)

    @pyqtProperty(str, notify=detectedDeChanged)
    def detectedDe(self):
        return self._detected_de

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(str, notify=usbStatusTextChanged)
    def usbStatusText(self):
        return self.usb.status

    @pyqtProperty(bool, notify=usbBusyChanged)
    def usbBusy(self):
        return self.usb.busy

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self.streaming.streaming

    @pyqtProperty(int, notify=countdownChanged)
    def countdown(self):
        return self.streaming.countdown

    @pyqtProperty(str, notify=streamingStatusChanged)
    def streamingStatus(self):
        return self.streaming.status

    @pyqtProperty(str, notify=pairingCodeChanged)
    def pairingCode(self):
        return self.streaming.pairing_code

    @pyqtProperty(bool, notify=isReceivingChanged)
    def isReceiving(self):
        return self.receiver.receiving

    @pyqtProperty(str, notify=receiverStatusChanged)
    def receiverStatus(self):
        return self.receiver.status

    @pyqtProperty(str, notify=receiverHostIpChanged)
    def receiverHostIp(self):
        return self.receiver.host_label

    @pyqtProperty("QVariant", notify=discoveredDevicesChanged)
    def discoveredDevices(self):
        return list(self.discovery.devices)

    @pyqtProperty(bool, notify=secondStreamActiveChanged)
    def secondStreamActive(self):
        return self.streaming.third.active

    @pyqtSlot()
    def startUsbScan(self):
        self.usb.start()

    @pyqtSlot()
    def resetUsbStatus(self):
        self.usb.reset()

    @pyqtSlot(result="QVariant")
    def loadUsbSettings(self):
        return load_usb_settings()

    @pyqtSlot(result="QVariant")
    def loadWifiSettings(self):
        return load_wifi_settings()

    @pyqtSlot(result="QVariant")
    def loadGeneralSettings(self):
        return load_general_settings()

    @pyqtSlot(bool, bool, bool)
    def saveGeneralSettings(self, minimize, touch, stylus):
        save_general_settings(
            minimize_to_tray=minimize,
            enable_touch=touch,
            enable_stylus_features=stylus,
        )

    @pyqtSlot(str, str, str, str, str, str, str, str)
    def saveUsbSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder):
        save_usb_settings(
            resolution=resolution, custom_w=custom_w, custom_h=custom_h,
            fps=fps, custom_fps=custom_fps, bitrate=bitrate,
            display_type=display_type, encoder=encoder,
        )

    @pyqtSlot(str, str, str, str, str, str, str, str, str, bool)
    def saveWifiSettings(self, resolution, custom_w, custom_h, fps, custom_fps, bitrate, display_type, encoder, stream_type, encryption):
        save_wifi_settings(
            resolution=resolution, custom_w=custom_w, custom_h=custom_h,
            fps=fps, custom_fps=custom_fps, bitrate=bitrate,
            display_type=display_type, encoder=encoder,
            stream_type=stream_type, use_encryption=encryption,
        )

    @pyqtSlot(result="QVariant")
    def loadSecondDisplaySettings(self):
        return load_second_display_settings()

    @pyqtSlot(str, str, str, str)
    def saveSecondDisplaySettings(self, resolution, fps, bitrate, encoder):
        save_second_display_settings(
            resolution=resolution, fps=fps, bitrate=bitrate, encoder=encoder
        )

    @pyqtSlot(result="QVariant")
    def loadReceiverSettings(self):
        return load_receiver_settings()

    @pyqtSlot(str, str, bool, str)
    def saveReceiverSettings(self, ip, port, encryption, decoder):
        save_receiver_settings(
            ip=ip, port=port, use_encryption=encryption, decoder=decoder
        )

    @pyqtSlot(str, str, result=bool)
    def receiverNeedsPairing(self, host, advertised_fingerprint):
        fingerprint, token = load_receiver_credentials(host)
        return not token or bool(
            advertised_fingerprint and fingerprint != advertised_fingerprint
        )

    @pyqtSlot()
    def startHostDiscovery(self):
        self.discovery.start()

    @pyqtSlot()
    def stopHostDiscovery(self):
        self.discovery.stop_browsing()

    @pyqtSlot(str, int, bool, str, str, str)
    def connectToHost(self, host, port, encrypted, fingerprint, code, decoder):
        host = normalize_host(host)
        if not valid_host(host) or not valid_port(port):
            self.receiver._set_status("Invalid host or port")
            return
        self.receiver.connect(
            host,
            sanitize_port(port),
            encrypted,
            fingerprint,
            code,
            sanitize_decoder(decoder),
        )

    @pyqtSlot()
    def stopReceiving(self):
        self.receiver.stop()

    @pyqtSlot(str, str, str, str, str, bool)
    def startStreaming(self, res, fps, bitrate, display_type, encoder, wifi):
        self.streaming.start(res, fps, bitrate, display_type, encoder, wifi)

    @pyqtSlot()
    def stopStreaming(self):
        self.streaming.stop()

    @pyqtSlot(str, str, str, str)
    def startSecondStream(self, res, fps, bitrate, encoder):
        self.streaming.start_third(res, fps, bitrate, encoder)

    @pyqtSlot()
    def stopSecondStream(self):
        self.streaming.stop_third()

    @pyqtSlot()
    def configureDisplay(self):
        self.configureDisplayRequested.emit()

    def _check_network_ip(self):
        current = get_local_ip()
        if current != self._local_ip:
            self._local_ip = current
            self.localIpChanged.emit(current)
            self.streaming.update_ip(current)

    def close(self):
        self.streaming.stop()
        self.receiver.stop()
        self.discovery.close()
