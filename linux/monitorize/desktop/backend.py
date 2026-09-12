"""QML-facing facade for Sunshine display sessions."""

import json
import logging
import os
import shutil

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from monitorize.config import app_log, autostart
from monitorize.config.settings import (
    MAX_PRESETS,
    load_display_settings,
    load_general_settings,
    load_presets,
    load_second_display_settings,
    save_display_settings,
    save_general_settings,
    save_presets,
    save_second_display_settings,
)
from monitorize.desktop.streaming_controller import StreamingController
from monitorize.platform.display_controller import DisplayController
from monitorize.platform.gpu_discovery import encoding_gpu_options
from monitorize.platform.sunshine_service import (
    clear_sunshine_portal_restore_tokens,
    find_sunshine_command,
    get_sunshine_config,
    open_sunshine_dashboard,
    pair_moonlight_pin,
    restart_sunshine,
    save_sunshine_config,
    set_sunshine_codec,
    set_sunshine_encoder,
    set_sunshine_native_pen_touch,
)
from monitorize.platform.system_setup import apply_system_setup, get_system_setup_status
from monitorize.platform.utils import get_local_ip
from monitorize.platform.vkms_backend import (
    CustomEdidCapability,
    VKMS_HELPER,
    VkmsError,
    custom_edid_capability_from_response,
    open_monitorize_vkms_install_page,
    resolution_options as vkms_resolution_options,
)


class MonitorizeBackend(QObject):
    sessionChanged = pyqtSignal()
    detectedDeChanged = pyqtSignal(str)
    localIpChanged = pyqtSignal(str)
    isStreamingChanged = pyqtSignal(bool)
    streamingStartFailed = pyqtSignal()
    streamingCodecMismatch = pyqtSignal(str)
    streamingStatusChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str, str)
    secondStreamActiveChanged = pyqtSignal(bool)
    configureDisplayRequested = pyqtSignal()
    presetsChanged = pyqtSignal()
    presetLaunchStatusChanged = pyqtSignal(str)
    systemSetupAvailableChanged = pyqtSignal(bool)
    systemSetupPendingChanged = pyqtSignal(bool)
    streamingBackendChanged = pyqtSignal(str)
    vkmsResolutionOptionsChanged = pyqtSignal()
    vkmsCustomCapabilityCheckingChanged = pyqtSignal()
    vkmsCustomCapabilityChecked = pyqtSignal(str)

    def __init__(self, de, parent=None):
        super().__init__(parent)
        self._detected_de = de
        self._local_ip = get_local_ip()
        self._sunshine_available = find_sunshine_command(1) is not None
        general = load_general_settings()
        self._streaming_backend = general.get("streaming_backend", "sunshine")
        if not self._sunshine_available:
            self._streaming_backend = "none"
        self.streaming = StreamingController(de, self._local_ip, self)
        self.streaming.streaming_backend = self._streaming_backend
        from monitorize.desktop.session import Session
        self.session = Session(self.streaming, self)
        self.session.changed.connect(self.sessionChanged)
        self._session_log = ""
        self.logAppended.connect(self._remember_session_log)
        self._presets = load_presets()
        self._preset_launch_status = ""
        self._vkms_resolution_options = vkms_resolution_options()
        self._vkms_custom_capability = None
        self._vkms_custom_capability_process = None
        self._system_setup_available = bool(get_system_setup_status()["available"])
        self._system_setup_decided = bool(
            general.get("system_setup_decided", False)
        )
        self.streaming.streamingChanged.connect(self.isStreamingChanged)
        self.streaming.startFailed.connect(self.streamingStartFailed)
        self.streaming.codecMismatch.connect(self.streamingCodecMismatch)
        self.streaming.statusChanged.connect(self.streamingStatusChanged)
        self.streaming.secondStreamChanged.connect(self.secondStreamActiveChanged)
        self.streaming.logAppended.connect(app_log.write)
        self.streaming.logAppended.connect(self.logAppended)
        self.network_timer = QTimer(self)
        self.network_timer.setInterval(5000)
        self.network_timer.timeout.connect(self._check_network_ip)
        self.network_timer.start()

    @pyqtProperty(str, notify=detectedDeChanged)
    def detectedDe(self):
        return self._detected_de

    def _remember_session_log(self, category, message):
        self._session_log = (self._session_log + f"[{category}] {message}\n")[-100000:]

    @pyqtSlot(result=str)
    def sessionLog(self):
        return self._session_log

    @pyqtProperty("QVariant", notify=sessionChanged)
    def sessionDisplays(self):
        return self.session.cards()

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionHasDisplays(self):
        return self.session.count > 0

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionPendingDisplays(self):
        return self.session.pending_displays

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionBusy(self):
        return self.session.busy

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionRunning(self):
        return self.session.running

    @pyqtProperty(str, notify=sessionChanged)
    def sessionMode(self):
        return self.session.mode

    @pyqtProperty(int, notify=sessionChanged)
    def sessionMaxDisplays(self):
        return self.session.max_displays

    @pyqtSlot()
    def addSessionDisplay(self):
        self.session.add()

    @pyqtSlot()
    def startSession(self):
        self.session.start()

    @pyqtSlot()
    def stopSession(self):
        self.session.stop()

    @pyqtSlot(int)
    def removeSessionDisplay(self, index):
        self.session.remove(index)

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self.streaming.streaming

    @pyqtProperty(str, notify=streamingStatusChanged)
    def streamingStatus(self):
        return self.streaming.status

    @pyqtProperty(bool, notify=secondStreamActiveChanged)
    def secondStreamActive(self):
        return self.streaming.third_active()

    @pyqtProperty("QVariant", notify=presetsChanged)
    def presets(self):
        return list(self._presets)

    @pyqtProperty(str, notify=presetLaunchStatusChanged)
    def presetLaunchStatus(self):
        return self._preset_launch_status

    @pyqtProperty(bool, notify=systemSetupAvailableChanged)
    def systemSetupAvailable(self):
        return self._system_setup_available

    @pyqtProperty(bool, notify=systemSetupPendingChanged)
    def systemSetupPending(self):
        return self._system_setup_available and not self._system_setup_decided

    @pyqtProperty(bool, constant=True)
    def canConfigureDisplay(self):
        return self._detected_de in ("hyprland", "sway")

    @pyqtProperty(bool, constant=True)
    def sunshineAvailable(self):
        return self._sunshine_available

    @pyqtProperty(bool, constant=True)
    def vkmsCreatorAvailable(self):
        return not os.path.isfile("/.flatpak-info")

    @pyqtProperty("QVariant", notify=vkmsResolutionOptionsChanged)
    def vkmsResolutionOptions(self):
        return list(self._vkms_resolution_options)

    @pyqtProperty(bool, notify=vkmsCustomCapabilityCheckingChanged)
    def vkmsCustomCapabilityChecking(self):
        return self._vkms_custom_capability_process is not None

    @pyqtSlot()
    def refreshVkmsResolutionOptions(self):
        options = vkms_resolution_options()
        if options != self._vkms_resolution_options:
            self._vkms_resolution_options = options
            self.vkmsResolutionOptionsChanged.emit()

    def _finish_vkms_custom_capability(self, capability, detail=""):
        process = self._vkms_custom_capability_process
        self._vkms_custom_capability_process = None
        if process is not None:
            process.deleteLater()
        self.vkmsCustomCapabilityCheckingChanged.emit()

        if capability in (
            CustomEdidCapability.SUPPORTED,
            CustomEdidCapability.UNSUPPORTED,
        ):
            self._vkms_custom_capability = capability
            app_log.write(
                "VKMS",
                f"VKMS custom EDID capability: {capability.value}",
            )
        else:
            app_log.write(
                "VKMS",
                "Failed to determine VKMS custom EDID capability: " + detail,
                level=logging.ERROR,
            )
        self.vkmsCustomCapabilityChecked.emit(capability.value)

    def _complete_vkms_custom_capability(self, process, exit_code):
        if process is not self._vkms_custom_capability_process:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        response = None
        for line in reversed(output.splitlines()):
            try:
                response = json.loads(line)
                break
            except (TypeError, json.JSONDecodeError):
                continue
        try:
            if exit_code or not isinstance(response, dict) or not response.get("success"):
                detail = ""
                if isinstance(response, dict):
                    detail = str(response.get("message") or "")
                raise VkmsError(detail or "The capability check did not complete.")
            capability = custom_edid_capability_from_response(response)
        except VkmsError as exc:
            self._finish_vkms_custom_capability(
                CustomEdidCapability.CHECK_FAILED, str(exc)
            )
            return
        self._finish_vkms_custom_capability(capability)

    def _handle_vkms_custom_capability_error(self, process, error):
        if (
            error == QProcess.ProcessError.FailedToStart
            and process is self._vkms_custom_capability_process
        ):
            self._finish_vkms_custom_capability(
                CustomEdidCapability.CHECK_FAILED,
                process.errorString(),
            )

    @pyqtSlot()
    def checkVkmsCustomEdidSupport(self):
        if self._vkms_custom_capability_process is not None:
            return
        if self._vkms_custom_capability is not None:
            QTimer.singleShot(
                0,
                lambda: self.vkmsCustomCapabilityChecked.emit(
                    self._vkms_custom_capability.value
                ),
            )
            return
        if os.path.isfile("/.flatpak-info"):
            self._finish_vkms_custom_capability(
                CustomEdidCapability.CHECK_FAILED,
                "VKMS capability checks are unavailable in Flatpak.",
            )
            return
        pkexec = shutil.which("pkexec")
        if not pkexec or not VKMS_HELPER.is_file() or not os.access(VKMS_HELPER, os.X_OK):
            self._finish_vkms_custom_capability(
                CustomEdidCapability.CHECK_FAILED,
                "The VKMS privileged helper is unavailable.",
            )
            return

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(
            lambda exit_code, _status: self._complete_vkms_custom_capability(
                process, exit_code
            )
        )
        process.errorOccurred.connect(
            lambda error: self._handle_vkms_custom_capability_error(process, error)
        )
        self._vkms_custom_capability_process = process
        self.vkmsCustomCapabilityCheckingChanged.emit()
        process.start(pkexec, [str(VKMS_HELPER), "capability"])

    @pyqtSlot(result=bool)
    def openMonitorizeVkmsInstallPage(self):
        opened = open_monitorize_vkms_install_page()
        if not opened:
            app_log.write(
                "VKMS",
                "Could not open the monitorize-vkms installation page.",
                level=logging.ERROR,
            )
        return opened

    @pyqtProperty(str, notify=streamingBackendChanged)
    def streamingBackend(self):
        return self._streaming_backend

    @pyqtSlot(str)
    def setStreamingBackend(self, value):
        value = value if value in ("sunshine", "none") else "sunshine"
        if value == "sunshine" and not self._sunshine_available:
            value = "none"
        if value == self._streaming_backend:
            return
        self._streaming_backend = value
        self.streaming.streaming_backend = value
        save_general_settings(streaming_backend=value)
        self.streamingBackendChanged.emit(value)

    @pyqtSlot(result="QVariantMap")
    def getSystemSetupStatus(self):
        return get_system_setup_status()

    @pyqtSlot(bool, bool, result="QVariantMap")
    def applySystemSetup(self, enable_input: bool, enable_firewall: bool):
        was_pending = self.systemSetupPending
        result = apply_system_setup(enable_input, enable_firewall)
        updated = bool(get_system_setup_status()["available"])
        if updated != self._system_setup_available:
            self._system_setup_available = updated
            self.systemSetupAvailableChanged.emit(updated)
        if was_pending != self.systemSetupPending:
            self.systemSetupPendingChanged.emit(self.systemSetupPending)
        return result

    @pyqtSlot()
    def markSystemSetupDecided(self):
        if self._system_setup_decided:
            return
        self._system_setup_decided = True
        save_general_settings(system_setup_decided=True)
        self.systemSetupPendingChanged.emit(self.systemSetupPending)

    @pyqtSlot(result="QVariant")
    def loadDisplaySettings(self):
        return load_display_settings()

    @pyqtSlot(str, result="QVariant")
    def getEncodingGpuOptions(self, encoder):
        return encoding_gpu_options(encoder)

    @pyqtSlot(result="QVariant")
    def getMirrorOutputs(self):
        from monitorize.platform.mirror_outputs import active_outputs

        return active_outputs(self._detected_de)

    @pyqtSlot(str, str, str, str, str, str, str, str, str, bool, bool, bool, str, str)
    def saveDisplaySettings(
        self,
        resolution,
        custom_w,
        custom_h,
        fps,
        custom_fps,
        display_type,
        sunshine_encoder,
        sunshine_gpu,
        sunshine_codec,
        streaming_customized,
        sunshine_native_pen_touch,
        enable_audio,
        mirror_output="",
        virtual_display_creator="native",
    ):
        save_display_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            display_type=display_type,
            sunshine_encoder=sunshine_encoder,
            sunshine_gpu=sunshine_gpu,
            sunshine_codec=sunshine_codec,
            streaming_customized=streaming_customized,
            sunshine_native_pen_touch=sunshine_native_pen_touch,
            enable_audio=enable_audio,
            mirror_output=mirror_output,
            virtual_display_creator=(
                virtual_display_creator
                if self.vkmsCreatorAvailable else "native"
            ),
        )
        self.session.preset_configuration = None
        self.session.configuration_changed()

    @pyqtSlot(result="QVariant")
    def loadGeneralSettings(self):
        return load_general_settings()

    @pyqtSlot(bool)
    def saveGeneralSettings(self, minimize):
        save_general_settings(minimize_to_tray=minimize)

    @pyqtSlot(result=bool)
    def isAutostartEnabled(self):
        return autostart.is_enabled()

    @pyqtSlot(bool, result=str)
    def setAutostartEnabled(self, enabled):
        return autostart.set_enabled(enabled)

    @pyqtSlot(result="QVariantMap")
    def removeStagnantVirtualDisplays(self):
        removed = DisplayController(self._detected_de).remove_stagnant_virtual_displays()
        if removed:
            return {
                "success": True,
                "message": "Successfully removed stagnant virtual displays",
            }
        return {
            "success": False,
            "message": "No stagnant displays were found",
        }

    @pyqtSlot(result="QVariantMap")
    def clearRestoreTokens(self):
        if self.isStreaming:
            return {
                "success": False,
                "message": "Stop streaming before clearing restore tokens",
            }
        removed, errors = clear_sunshine_portal_restore_tokens()
        if errors:
            return {
                "success": False,
                "message": "Some restore tokens could not be cleared",
            }
        if removed:
            return {
                "success": True,
                "message": "Restore tokens cleared — select the displays again",
            }
        return {
            "success": False,
            "message": "No restore tokens were found",
        }

    @pyqtSlot(str, str, str, str, str, str, bool, bool)
    def startStreaming(
        self,
        res,
        fps,
        display_type,
        encoder,
        gpu_id,
        codec,
        native_pen_touch,
        enable_audio,
    ):
        self.streaming.start(
            res,
            fps,
            display_type,
            encoder,
            codec,
            native_pen_touch,
            enable_audio,
            gpu_id=gpu_id,
        )

    @pyqtSlot()
    def stopStreaming(self):
        self.streaming.stop()

    @pyqtSlot()
    @pyqtSlot(int)
    def openSunshineWebUi(self, instance: int = 1):
        open_sunshine_dashboard(instance, "config")

    @pyqtSlot(str, result="QVariantMap")
    @pyqtSlot(str, int, result="QVariantMap")
    def pairMoonlightPin(self, pin: str, instance: int = 1):
        success, message = pair_moonlight_pin(pin, instance=instance)
        return {"success": success, "message": message}

    @pyqtSlot(result="QVariantMap")
    @pyqtSlot(int, result="QVariantMap")
    def restartSunshine(self, instance: int = 1):
        success, message = restart_sunshine(instance)
        return {"success": success, "message": message}

    @pyqtSlot(result="QVariantMap")
    @pyqtSlot(int, result="QVariantMap")
    def getSunshineConfig(self, instance: int = 1):
        return get_sunshine_config(instance)

    @pyqtSlot("QVariantMap", result="QVariantMap")
    @pyqtSlot("QVariantMap", int, result="QVariantMap")
    def saveSunshineConfig(self, config_data, instance: int = 1):
        success, message = save_sunshine_config(
            dict(config_data or {}), instance=instance
        )
        return {"success": success, "message": message}

    @pyqtSlot(str, result="QVariantMap")
    @pyqtSlot(str, int, result="QVariantMap")
    def setSunshineEncoder(self, encoder_name: str, instance: int = 1):
        success, message = set_sunshine_encoder(encoder_name, instance=instance)
        return {"success": success, "message": message}

    @pyqtSlot(str, result="QVariantMap")
    @pyqtSlot(str, int, result="QVariantMap")
    def setSunshineCodec(self, codec_name: str, instance: int = 1):
        success, message = set_sunshine_codec(codec_name, instance=instance)
        return {"success": success, "message": message}

    @pyqtSlot(bool, result="QVariantMap")
    @pyqtSlot(bool, int, result="QVariantMap")
    def setSunshineNativePenTouch(self, enabled: bool, instance: int = 1):
        success, message = set_sunshine_native_pen_touch(enabled, instance=instance)
        return {"success": success, "message": message}

    @pyqtSlot(result="QVariant")
    def loadSecondDisplaySettings(self):
        return load_second_display_settings()

    @pyqtSlot(str, str, str, str, str, str, str, str, bool, bool)
    def saveSecondDisplaySettings(
        self,
        resolution,
        custom_w,
        custom_h,
        fps,
        custom_fps,
        encoder,
        gpu_id,
        codec,
        native_pen_touch,
        enable_audio,
    ):
        save_second_display_settings(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            sunshine_encoder=encoder,
            sunshine_gpu=gpu_id,
            sunshine_codec=codec,
            sunshine_native_pen_touch=native_pen_touch,
            enable_audio=enable_audio,
        )

    @pyqtSlot(str, str, str, str, str, bool, bool)
    def startSecondStream(
        self, res, fps, encoder, gpu_id, codec, native_pen_touch, enable_audio
    ):
        self.streaming.start_third(
            res, fps, encoder, codec, native_pen_touch, enable_audio, gpu_id=gpu_id
        )

    @pyqtSlot()
    def stopSecondStream(self):
        self.streaming.stop_third()

    @pyqtSlot()
    def configureDisplay(self):
        if not self.canConfigureDisplay:
            return
        self.configureDisplayRequested.emit()

    @pyqtSlot(str, int, result=str)
    def saveCurrentPreset(self, name, replace_index=-1):
        name = name.strip()
        if not self.streaming.streaming:
            return "No active display to save."
        if not name:
            return "Enter a preset name."
        if len(name) > 32:
            return "Preset names can contain at most 32 characters."
        duplicate = next(
            (
                index
                for index, preset in enumerate(self._presets)
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
        import copy
        self.session.preset_configuration = copy.deepcopy(preset)
        primary = preset["primary"]
        self._set_preset_launch_status("")
        self.streaming.start(
            primary["resolution"],
            primary["fps"],
            primary["display_type"],
            primary["sunshine_encoder"],
            primary["sunshine_codec"],
            primary["sunshine_native_pen_touch"],
            primary["enable_audio"],
            {"second": preset["second"]},
            gpu_id=primary.get("sunshine_gpu", ""),
            mirror_output=primary.get("mirror_output", ""),
            virtual_display_creator=(
                primary.get("virtual_display_creator", "native")
                if self.vkmsCreatorAvailable else "native"
            ),
        )

    @pyqtSlot(int, str, result=str)
    def renamePreset(self, index, name):
        name = name.strip()
        if index < 0 or index >= len(self._presets):
            return "Preset no longer exists."
        if not name or len(name) > 32:
            return "Enter a preset name of at most 32 characters."
        if any(
            preset["name"].casefold() == name.casefold()
            for preset_index, preset in enumerate(self._presets)
            if preset_index != index
        ):
            return "A preset with this name already exists."
        self._presets[index]["name"] = name
        save_presets(self._presets)
        self._presets = load_presets()
        self.presetsChanged.emit()
        return ""

    @pyqtSlot(int)
    def deletePreset(self, index):
        if 0 <= index < len(self._presets):
            self._presets.pop(index)
            save_presets(self._presets)
            self.presetsChanged.emit()

    def _set_preset_launch_status(self, value):
        if self._preset_launch_status != value:
            self._preset_launch_status = value
            self.presetLaunchStatusChanged.emit(value)

    def should_minimize_to_tray(self):
        return load_general_settings().get("minimize_to_tray", False)

    def _check_network_ip(self):
        current = get_local_ip()
        if current != self._local_ip:
            self._local_ip = current
            self.localIpChanged.emit(current)
            self.streaming.update_ip(current)

    def close(self):
        self.network_timer.stop()
        self.streaming.stop()
