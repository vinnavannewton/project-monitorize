"""QML-facing Monitorize backend façade."""

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

import threading, time, subprocess

from monitorize.config import app_log, autostart
from monitorize.desktop.discovery_service import DiscoveryService
from monitorize.desktop.receiver_controller import ReceiverController
from monitorize.config.settings import (
    load_recent_usb_devices,
    load_recent_wifi_devices,
    add_recent_usb_device,
    add_recent_wifi_device,
    MAX_PRESETS,
    load_general_settings,
    load_presets,
    load_receiver_credentials,
    load_receiver_settings,
    load_second_display_settings,
    load_usb_settings,
    load_wifi_settings,
    save_general_settings,
    save_presets,
    save_receiver_settings,
    save_second_display_settings,
    save_usb_settings,
    save_wifi_settings,
)
from monitorize.desktop.streaming_controller import StreamingController
from monitorize.desktop.usb_controller import UsbController
from monitorize.platform.utils import get_local_ip
from monitorize.config.validation import (
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
    presetsChanged = pyqtSignal()
    presetLaunchStatusChanged = pyqtSignal(str)
    recentUsbDevicesChanged = pyqtSignal()
    recentWifiDevicesChanged = pyqtSignal()
    recentStatusUpdated = pyqtSignal(list,dict) 

    def __init__(self, de, parent=None):
        super().__init__(parent)
        self._detected_de = de
        self._local_ip = get_local_ip()
        self.discovery = DiscoveryService(self)
        self.usb = UsbController(self)
        self.receiver = ReceiverController(de, self.discovery, self)
        self.streaming = StreamingController(de, self._local_ip, self.discovery, self)
        self._presets = load_presets()
        self._pending_usb_preset = None
        self._preset_launch_status = ""
        self._wire_signals()
        self.network_timer = QTimer(self)
        self.network_timer.setInterval(5000)
        self.network_timer.timeout.connect(self._check_network_ip)
        self.network_timer.start()

        
        self._recent_usb_devices = []
        self._recent_wifi_devices = []
        self.recentStatusUpdated.connect(self._on_recent_status_updated)
        self.status_checker = RecentDeviceStatusChecker(self)
        self.status_checker.start()

    def _wire_signals(self):
        self.usb.statusChanged.connect(self.usbStatusTextChanged)
        self.usb.busyChanged.connect(self.usbBusyChanged)
        self.usb.scanFinished.connect(self._finish_usb_preset_launch)
        self.usb.scanFinished.connect(self._on_usb_scan_finished)
        self.discovery.devicesChanged.connect(self.discoveredDevicesChanged)
        self.receiver.receivingChanged.connect(self.isReceivingChanged)
        self.receiver.statusChanged.connect(self.receiverStatusChanged)
        self.receiver.hostChanged.connect(self.receiverHostIpChanged)
        self.receiver.logAppended.connect(
            lambda message: app_log.write("RECEIVER", message)
        )
        self.receiver.logAppended.connect(self.receiverLogAppended)
        self.receiver.pairingRequired.connect(self.receiverPairingRequired)
        self.streaming.streamingChanged.connect(self.isStreamingChanged)
        self.streaming.statusChanged.connect(self.streamingStatusChanged)
        self.streaming.countdownChanged.connect(self.countdownChanged)
        self.streaming.pairingCodeChanged.connect(self.pairingCodeChanged)
        self.streaming.secondStreamChanged.connect(self.secondStreamActiveChanged)
        self.streaming.logAppended.connect(app_log.write)
        self.streaming.logAppended.connect(self.logAppended)
        self.streaming.clientConnected.connect(self._on_wifi_client_connected)

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
        return self.streaming.third_active()

    @pyqtProperty("QVariant", notify=presetsChanged)
    def presets(self):
        return list(self._presets)

    @pyqtProperty(str, notify=presetLaunchStatusChanged)
    def presetLaunchStatus(self):
        return self._preset_launch_status

    @pyqtProperty(list, notify=recentUsbDevicesChanged)
    def recentUsbDevices(self):
        return self._recent_usb_devices

    @pyqtProperty(list, notify=recentWifiDevicesChanged)
    def recentWifiDevices(self):
        return self._recent_wifi_devices

    @pyqtSlot()
    @pyqtSlot(str)
    def startUsbScan(self, serial=None):
        self._pending_usb_preset = None
        self.usb.start(serial)

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

    @pyqtSlot(result=bool)
    def isAutostartEnabled(self):
        return autostart.is_enabled()

    @pyqtSlot(bool, result=str)
    def setAutostartEnabled(self, enabled):
        return autostart.set_enabled(enabled)

    @pyqtSlot(str, str, str, str, str, str, str, str, str)
    def saveUsbSettings(
        self, resolution, custom_w, custom_h, fps, custom_fps, bitrate,
        display_type, encoder, encoder_profile,
    ):
        save_usb_settings(
            resolution=resolution, custom_w=custom_w, custom_h=custom_h,
            fps=fps, custom_fps=custom_fps, bitrate=bitrate,
            display_type=display_type, encoder=encoder,
            encoder_profile=encoder_profile,
        )

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, bool)
    def saveWifiSettings(
        self, resolution, custom_w, custom_h, fps, custom_fps, bitrate,
        display_type, encoder, encoder_profile, stream_type, encryption,
    ):
        save_wifi_settings(
            resolution=resolution, custom_w=custom_w, custom_h=custom_h,
            fps=fps, custom_fps=custom_fps, bitrate=bitrate,
            display_type=display_type, encoder=encoder,
            encoder_profile=encoder_profile,
            stream_type=stream_type, use_encryption=encryption,
        )

    @pyqtSlot(result="QVariant")
    def loadSecondDisplaySettings(self):
        return load_second_display_settings()

    @pyqtSlot(str, str, str, str, str)
    def saveSecondDisplaySettings(self, resolution, fps, bitrate, encoder, encoder_profile):
        save_second_display_settings(
            resolution=resolution, fps=fps, bitrate=bitrate, encoder=encoder,
            encoder_profile=encoder_profile,
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

    @pyqtSlot("QVariant")
    def setReceiverVideoItem(self, item):
        self.receiver.set_video_item(item)

    @pyqtSlot(str, str, str, str, str, str, bool)
    def startStreaming(
        self, res, fps, bitrate, display_type, encoder, encoder_profile, wifi
    ):
        self._pending_usb_preset = None
        self.streaming.start(
            res, fps, bitrate, display_type, encoder, encoder_profile, wifi
        )

    @pyqtSlot()
    def stopStreaming(self):
        self._pending_usb_preset = None
        self.streaming.stop()

    @pyqtSlot(str, str, str, str, str)
    def startSecondStream(self, res, fps, bitrate, encoder, encoder_profile):
        self.streaming.start_third(res, fps, bitrate, encoder, encoder_profile)

    @pyqtSlot()
    def stopSecondStream(self):
        self.streaming.stop_third()

    @pyqtSlot()
    def configureDisplay(self):
        self.configureDisplayRequested.emit()

    @pyqtSlot(str, int, result=str)
    def saveCurrentPreset(self, name, replace_index=-1):
        name = name.strip()
        if not self.streaming.streaming:
            return "No active stream to save."
        if not name:
            return "Enter a preset name."
        if len(name) > 32:
            return "Preset names can contain at most 32 characters."
        duplicate = next(
            (
                index for index, preset in enumerate(self._presets)
                if preset["name"].casefold() == name.casefold()
                and index != replace_index
            ),
            -1,
        )
        if duplicate >= 0:
            return f"duplicate:{duplicate}"
        if replace_index < -1 or replace_index >= len(self._presets):
            return "Invalid preset selection."
        if replace_index == -1 and len(self._presets) >= MAX_PRESETS:
            return "full"
        preset = self.streaming.active_configuration()
        preset["name"] = name
        if replace_index >= 0:
            self._presets[replace_index] = preset
        else:
            self._presets.append(preset)
        save_presets(self._presets)
        self._presets = load_presets()
        self.presetsChanged.emit()
        return ""

    @pyqtSlot(int)
    def launchPreset(self, index):
        if index < 0 or index >= len(self._presets):
            self._set_preset_launch_status("Preset no longer exists.")
            return
        preset = self._presets[index]
        self._pending_usb_preset = None
        if preset["mode"] == "usb":
            self._pending_usb_preset = preset
            self._set_preset_launch_status("Checking USB device...")
            if not self.usb.busy:
                self.usb.start()
            return
        self._set_preset_launch_status("")
        self._start_preset(preset)

    @pyqtSlot(int, str, result=str)
    def renamePreset(self, index, name):
        name = name.strip()
        if index < 0 or index >= len(self._presets):
            return "Preset no longer exists."
        if not name:
            return "Enter a preset name."
        if len(name) > 32:
            return "Preset names can contain at most 32 characters."
        if any(
            preset["name"].casefold() == name.casefold()
            for preset_index, preset in enumerate(self._presets)
            if preset_index != index
        ):
            return "A preset with that name already exists."
        self._presets[index]["name"] = name
        save_presets(self._presets)
        self._presets = load_presets()
        self.presetsChanged.emit()
        return ""

    @pyqtSlot(int)
    def deletePreset(self, index):
        if index < 0 or index >= len(self._presets):
            return
        self._presets.pop(index)
        save_presets(self._presets)
        self.presetsChanged.emit()

    def _finish_usb_preset_launch(self, success):
        preset = self._pending_usb_preset
        self._pending_usb_preset = None
        if preset is None:
            return
        if not success:
            self._set_preset_launch_status(self.usb.status)
            return
        self._set_preset_launch_status("")
        self._start_preset(preset)

    def _start_preset(self, preset):
        primary = preset["primary"]
        self.streaming.start(
            primary["resolution"],
            primary["fps"],
            primary["bitrate"],
            primary["display_type"],
            primary["encoder"],
            primary.get("encoder_profile", "Low Latency"),
            preset["mode"] == "wifi",
            {
                "wifi": preset.get("wifi", {}),
                "general": preset["general"],
                "third": preset["third"] if preset["third"]["enabled"] else None,
            },
        )

    def should_minimize_to_tray(self):
        return load_general_settings().get("minimize_to_tray", False)

    def _set_preset_launch_status(self, value):
        if self._preset_launch_status == value:
            return
        self._preset_launch_status = value
        self.presetLaunchStatusChanged.emit(value)

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
        self.status_checker.stop()

    def _on_recent_status_updated(self, online_usb_serials, online_wifi_ips):
        raw_usb = load_recent_usb_devices()
        for dev in raw_usb:
            dev["online"] = dev.get("serial") in online_usb_serials
        self._recent_usb_devices = raw_usb
        self.recentUsbDevicesChanged.emit()

        raw_wifi = load_recent_wifi_devices()
        for dev in raw_wifi:
            dev["online"] = online_wifi_ips.get(dev.get("ip"), False)
        self._recent_wifi_devices = raw_wifi
        self.recentWifiDevicesChanged.emit()

    def _on_usb_scan_finished(self, success):
        if not success:
            return
        def worker():
            try:
                serial = getattr(self.usb, "serial", None)
                if not serial:
                    res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=2)
                    if res.returncode == 0:
                        lines = [line.split()[0] for line in res.stdout.splitlines() if line.endswith("device")]
                        if len(lines) == 1:
                            serial = lines[0]
                if serial:
                    model_res = subprocess.run(["adb", "-s", serial, "shell", "getprop", "ro.product.model"], capture_output=True, text=True, timeout=2)
                    model = model_res.stdout.strip() if model_res.returncode == 0 else "Unknown USB Device"
                    if not model:
                        model = "Unknown USB Device"
                    add_recent_usb_device({"serial": serial, "name": model})
            except Exception as e:
                app_log.error(f"Error saving USB device to recents: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _on_wifi_client_connected(self, ip, name):
        try:
            add_recent_wifi_device({"ip": ip, "name": name})
        except Exception as e:
            app_log.error(f"Error saving Wi-Fi device to recents: {e}")





class RecentDeviceStatusChecker(threading.Thread):
    def __init__(self, backend):
        super().__init__(daemon=True)
        self.backend = backend
        self.running = True


    def stop(self):
        self.running= False

    def run(self):
        while self.running:
            
            online_usb = []
            try:
                res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if line.endswith("device"):
                            online_usb.append(line.split()[0])
            except Exception:
                pass

            
            online_wifi = {}
            try:
                recent_wifi = load_recent_wifi_devices()
            except Exception:
                recent_wifi = []
            for device in recent_wifi:
                ip = device.get("ip")
                if ip:
                    try:
                        res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        online_wifi[ip] = (res.returncode == 0)
                    except Exception:  
                        online_wifi[ip] = False

            
            try:
                self.backend.recentStatusUpdated.emit(online_usb, online_wifi)
            except Exception:
                pass
            time.sleep(5)
