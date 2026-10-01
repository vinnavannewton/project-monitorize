"""QML-facing facade for Sunshine display sessions."""

import json
import logging
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from monitorize.config import app_log, autostart
from monitorize.config.settings import (
    DISPLAY_DEFAULTS,
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
from monitorize.platform.gpu_discovery import compatible_gpus, encoding_gpu_options, resolve_encoding_gpu
from monitorize.platform.sunshine_service import (
    clear_sunshine_portal_restore_tokens,
    find_sunshine_command,
    get_sunshine_config_dir,
    get_sunshine_config,
    get_saved_sunshine_config,
    get_sunshine_web_url,
    open_sunshine_dashboard,
    is_sunshine_running,
    is_sunshine_settings_instance,
    start_sunshine,
    stop_sunshine,
    sunshine_web_ready,
    pair_moonlight_pin,
    restart_sunshine,
    reset_sunshine_config,
    save_sunshine_config,
    save_sunshine_adapter,
    set_sunshine_codec,
    set_sunshine_encoder,
    set_sunshine_native_pen_touch,
)
from monitorize.platform.system_setup import apply_system_setup, get_system_setup_status
from monitorize.platform.utils import LINUX_DIR, get_local_ip
from monitorize.platform.monitorize_vkms_cli import MonitorizeVkmsClient
from monitorize.platform.vkms_backend import open_monitorize_vkms_install_page


class MonitorizeBackend(QObject):
    sunshineSettingsChanged = pyqtSignal()
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
    vkmsHelperAvailabilityChanged = pyqtSignal()
    virtualDisplayCleanupChanged = pyqtSignal()
    virtualDisplayCleanupFinished = pyqtSignal(bool, str)

    def __init__(self, de, parent=None):
        super().__init__(parent)
        self._detected_de = de
        self.native_compositor_resolver = None
        self._settings_instance = None
        self._settings_deadline = 0.0
        self._settings_message = ""
        self._settings_timer = QTimer(self)
        self._settings_timer.setInterval(250)
        self._settings_timer.timeout.connect(self._poll_sunshine_settings)
        self._virtual_display_cleanup_process = None
        self._local_ip = get_local_ip()
        self._sunshine_available = find_sunshine_command(1) is not None
        general = load_general_settings()
        self._streaming_backend = general.get("streaming_backend", "sunshine")
        self._web_settings_enabled = general.get("sunshine_web_settings_enabled", False)
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
        self._vkms_resolution_options = [
            "1280x720", "1280x800", "1920x1080", "1920x1200",
            "2560x1440", "2560x1600", "3840x2160", "Custom...",
        ]
        self._vkms_helper_available = MonitorizeVkmsClient().is_available()
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

    @pyqtSlot(result=bool)
    def ensureNativeCompositor(self):
        """Resolve an unknown compositor only when native creation is requested."""
        if self._detected_de:
            return True
        if self.native_compositor_resolver is None:
            return False
        selected = self.native_compositor_resolver()
        if selected not in ("kde", "gnome", "hyprland", "sway"):
            return False
        self._detected_de = selected
        self.streaming.de = selected
        self.detectedDeChanged.emit(selected)
        return True

    def _remember_session_log(self, category, message):
        self._session_log = (self._session_log + f"[{category}] {message}\n")[-100000:]

    @pyqtSlot(result=str)
    def sessionLog(self):
        """Return every currently retained Monitorize and Sunshine log source."""
        sources = [
            ("Sunshine instance 1", Path(get_sunshine_config_dir(1)) / "sunshine.log"),
            ("Sunshine instance 2", Path(get_sunshine_config_dir(2)) / "sunshine.log"),
            ("Monitorize", app_log.LOG_FILE),
        ]
        sections = []
        for label, path in sources:
            content = app_log.read_tail(path)
            if content:
                sections.append(f"===== {label} =====\n{content}")
        return "\n\n".join(sections) or "No retained diagnostic logs yet."

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
        return self.streaming.streaming and self.streaming.primary_ready

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
        if self.virtualDisplayCleanupRunning:
            return
        config = self.session.configuration()
        if (config["display_type"] == "Extend"
                and config["virtual_display_creator"] == "vkms"):
            self.refreshVkmsHelperAvailability()
            if not self.vkmsHelperAvailable:
                message = "Install monitorize-vkms to create a VKMS display."
                self.streaming._set_status(message)
                self.streamingStartFailed.emit()
                return
        self._sync_web_settings()
        if (config["display_type"] == "Extend"
                and config["virtual_display_creator"] == "native"
                and not self.ensureNativeCompositor()):
            return
        self._cancel_settings_open()
        self.session.start()

    @pyqtSlot()
    def stopSession(self):
        self._cancel_settings_open()
        self.session.stop()

    @pyqtSlot(int)
    def removeSessionDisplay(self, index):
        if index != 1 or not self.session.remove(index):
            return
        if not self.session.preset_configuration:
            second = load_second_display_settings()
            second["enabled"] = False
            save_second_display_settings(**second)
            self.session.configuration_changed()

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self.streaming.streaming

    @pyqtProperty(bool, notify=sessionChanged)
    def canSavePreset(self):
        if not self.streaming.streaming or not self.streaming.primary_ready or self.session.busy:
            return False
        second = (self.streaming.pending_options or {}).get("second") or {}
        return not second.get("enabled") or self.streaming.third_ready

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

    @pyqtProperty(bool, notify=detectedDeChanged)
    def canConfigureDisplay(self):
        return self._detected_de in ("hyprland", "sway")

    @pyqtProperty(bool, constant=True)
    def sunshineAvailable(self):
        return self._sunshine_available

    @pyqtProperty(bool, constant=True)
    def vkmsCreatorAvailable(self):
        return not os.path.isfile("/.flatpak-info")

    @pyqtProperty("QVariant", constant=True)
    def vkmsResolutionOptions(self):
        return list(self._vkms_resolution_options)

    @pyqtProperty(bool, notify=vkmsHelperAvailabilityChanged)
    def vkmsHelperAvailable(self):
        return self._vkms_helper_available

    @pyqtSlot()
    def refreshVkmsHelperAvailability(self):
        available = MonitorizeVkmsClient().is_available()
        if available != self._vkms_helper_available:
            self._vkms_helper_available = available
            self.vkmsHelperAvailabilityChanged.emit()

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
        self._sync_web_settings()
        return load_display_settings()

    def _sync_web_settings(self):
        """Import webpage choices before Monitorize prepares a session or UI."""
        if not self._web_settings_enabled:
            return
        changed = False
        for instance in (1, 2):
            changed = self._sync_web_settings_instance(instance) or changed
        if changed:
            self.session.configuration_changed()

    def _sync_web_settings_instance(self, instance):
        web = get_saved_sunshine_config(instance)
        if not web:
            return False
        load = load_display_settings if instance == 1 else load_second_display_settings
        save = save_display_settings if instance == 1 else save_second_display_settings
        saved = load()
        encoder = web.get("encoder", "").strip().lower()
        encoders = {"": "Auto", "auto": "Auto", "nvenc": "NVIDIA",
                    "vaapi": "VA-API", "vulkan": "Vulkan", "software": "Software"}
        if encoder in encoders:
            saved["sunshine_encoder"] = encoders[encoder]
            saved["streaming_customized"] = True
        if "hevc_mode" in web or "av1_mode" in web:
            hevc = web.get("hevc_mode", "0")
            av1 = web.get("av1_mode", "0")
            saved["sunshine_codec"] = (
                "AV1" if av1 == "2" else "HEVC" if hevc == "2" else
                "H.264" if hevc == "1" and av1 == "1" else "Auto"
            )
            saved["streaming_customized"] = True
        if "native_pen_touch" in web:
            saved["sunshine_native_pen_touch"] = web["native_pen_touch"].lower() in ("enabled", "true", "1")
        if "stream_audio" in web:
            saved["enable_audio"] = web["stream_audio"].lower() in ("enabled", "true", "1")
        if "adapter_name" in web:
            adapter = web["adapter_name"].strip()
            matched = next((gpu["id"] for gpu in compatible_gpus(saved["sunshine_encoder"])
                            if gpu.get("render_node") == adapter), "") if adapter else ""
            saved["sunshine_gpu"] = matched
        if saved != load():
            save(**saved)
            return True
        return False

    @pyqtSlot(result="QVariant")
    def loadVirtualDisplaySettings(self):
        """Return the persisted mode object for each configured virtual display."""
        primary = load_display_settings()
        displays = [{
            "id": 1,
            "resolution": primary["resolution"],
            "custom_w": primary["custom_w"],
            "custom_h": primary["custom_h"],
            "fps": primary["fps"],
            "custom_fps": primary["custom_fps"],
        }]
        second = load_second_display_settings()
        if second.get("enabled", False):
            displays.append({
                "id": 2,
                "resolution": second["resolution"],
                "custom_w": second["custom_w"],
                "custom_h": second["custom_h"],
                "fps": second["fps"],
                "custom_fps": second["custom_fps"],
            })
        return displays

    @pyqtSlot("QVariantList")
    def saveVirtualDisplaySettings(self, display_configs):
        """Persist one or two independent display-mode configurations."""
        configs = [dict(config) for config in list(display_configs or []) if isinstance(config, dict)]
        if not configs:
            return
        configs = configs[:2]
        mode_keys = ("resolution", "custom_w", "custom_h", "fps", "custom_fps")

        primary = load_display_settings()
        primary.update({key: configs[0].get(key, primary[key]) for key in mode_keys})
        save_display_settings(**primary)

        second = load_second_display_settings()
        if len(configs) == 2:
            second.update({key: configs[1].get(key, second[key]) for key in mode_keys})
            second["enabled"] = True
        else:
            second["enabled"] = False
        save_second_display_settings(**second)

        self.session.preset_configuration = None
        self.session.configuration_changed()

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
        previous_gpu = load_display_settings().get("sunshine_gpu", "")
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
        if self._web_settings_enabled and sunshine_gpu != previous_gpu:
            selected = resolve_encoding_gpu(sunshine_encoder, sunshine_gpu)
            if not save_sunshine_adapter(selected.get("render_node", "") if selected else ""):
                app_log.write("SUNSHINE", "Could not save the selected encoding GPU.", level=logging.ERROR)
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

    @pyqtProperty(bool, notify=virtualDisplayCleanupChanged)
    def virtualDisplayCleanupRunning(self):
        return self._virtual_display_cleanup_process is not None

    def _finish_virtual_display_cleanup(self, process, exit_code):
        if process is not self._virtual_display_cleanup_process:
            return
        result = {"success": False, "message": "Virtual display cleanup failed"}
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in output.splitlines():
            if line.startswith("MONITORIZE_CLEANUP "):
                try:
                    parsed = json.loads(line.split(" ", 1)[1])
                    if isinstance(parsed, dict):
                        result = parsed
                except ValueError:
                    pass
        self._virtual_display_cleanup_process = None
        process.deleteLater()
        self.virtualDisplayCleanupChanged.emit()
        self.virtualDisplayCleanupFinished.emit(
            exit_code == 0 and result.get("success") is True,
            str(result.get("message") or "Virtual display cleanup failed"),
        )

    @pyqtSlot()
    def removeStagnantVirtualDisplays(self):
        if self.virtualDisplayCleanupRunning:
            return
        if self.isStreaming:
            self.virtualDisplayCleanupFinished.emit(False, "Stop streaming before removing virtual displays")
            return
        process = QProcess(self)
        process.setWorkingDirectory(LINUX_DIR)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(lambda code, _status: self._finish_virtual_display_cleanup(process, code))
        process.errorOccurred.connect(
            lambda error: self._finish_virtual_display_cleanup(process, 1)
            if error == QProcess.ProcessError.FailedToStart else None
        )
        self._virtual_display_cleanup_process = process
        self.virtualDisplayCleanupChanged.emit()
        process.start(sys.executable, ["-m", "monitorize.platform.virtual_display_cleanup", self._detected_de])

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

    @pyqtSlot(result="QVariantMap")
    def resetSunshineSettings(self):
        if self.isStreaming or self.sessionBusy:
            return {"success": False, "message": "Stop the session before resetting Sunshine settings"}
        for instance in (1, 2):
            if is_sunshine_running(instance) and not is_sunshine_settings_instance(instance):
                return {"success": False, "message": "Stop Sunshine before resetting its settings"}
        if self._settings_instance is not None:
            self._cancel_settings_open()
        for instance in (1, 2):
            if is_sunshine_settings_instance(instance):
                stop_sunshine(instance, clear_output_name=False)
                if is_sunshine_running(instance):
                    return {"success": False, "message": f"Could not stop Sunshine instance {instance} before resetting its settings"}
            success, message = reset_sunshine_config(instance)
            if not success:
                return {"success": False, "message": message}
        defaults = {key: DISPLAY_DEFAULTS[key] for key in (
            "sunshine_encoder", "sunshine_gpu", "sunshine_codec",
            "streaming_customized", "sunshine_native_pen_touch", "enable_audio",
        )}
        primary = load_display_settings()
        primary.update(defaults)
        save_display_settings(**primary)
        second = load_second_display_settings()
        second.update(defaults)
        save_second_display_settings(**second)
        self._web_settings_enabled = False
        save_general_settings(sunshine_web_settings_enabled=False)
        self.session.preset_configuration = None
        self.session.configuration_changed()
        return {"success": True, "message": "Sunshine settings restored to Monitorize defaults"}

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
        if self.virtualDisplayCleanupRunning:
            return
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
        if instance not in (1, 2) or self.sessionBusy or self._settings_instance is not None:
            return
        if not self._web_settings_enabled:
            self._web_settings_enabled = True
            save_general_settings(sunshine_web_settings_enabled=True)
        self._settings_instance = instance
        self._settings_message = "Opening Sunshine settings…"
        self.sunshineSettingsChanged.emit()
        ok, message = start_sunshine(instance, settings_only=True)
        if not ok:
            self._finish_settings_open(message)
            return
        self._settings_deadline = time.monotonic() + 20
        self._settings_timer.start()

    @pyqtProperty(bool, notify=sunshineSettingsChanged)
    def sunshineSettingsOpening(self):
        return self._settings_instance is not None

    @pyqtProperty(str, notify=sunshineSettingsChanged)
    def sunshineSettingsMessage(self):
        return self._settings_message

    def _finish_settings_open(self, message):
        self._settings_timer.stop()
        self._settings_instance = None
        self._settings_message = message
        self.sunshineSettingsChanged.emit()

    def _cancel_settings_open(self):
        self._finish_settings_open("")

    def _poll_sunshine_settings(self):
        instance = self._settings_instance
        if instance is None:
            return
        if not is_sunshine_running(instance):
            self._finish_settings_open("Sunshine stopped before its settings page was ready. Check the session logs.")
        elif sunshine_web_ready(instance):
            opened = open_sunshine_dashboard(instance, "config")
            self._finish_settings_open(
                "Sunshine settings opened in your browser." if opened
                else "Could not open your browser. Open "
                     + get_sunshine_web_url(instance) + "/config."
            )
        elif time.monotonic() >= self._settings_deadline:
            self._finish_settings_open("Sunshine settings did not become ready. Check the session logs and try again.")

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
        if self.virtualDisplayCleanupRunning:
            return
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
        if not self.canSavePreset:
            return "Wait until the session is ready before saving a preset."
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
            return "A preset with this name already exists. Choose it under Replace preset."
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
        if self.virtualDisplayCleanupRunning:
            self._set_preset_launch_status("Wait for virtual display setup to finish.")
            return
        if index < 0 or index >= len(self._presets):
            self._set_preset_launch_status("Preset no longer exists.")
            return
        self._set_preset_launch_status("")
        preset = self._presets[index]
        import copy
        primary = preset["primary"]
        if (primary["display_type"] == "Extend"
                and primary.get("virtual_display_creator") == "vkms"):
            self.refreshVkmsHelperAvailability()
            if not self.vkmsHelperAvailable:
                self._set_preset_launch_status("Install monitorize-vkms to use this VKMS preset.")
                return
        self._sync_web_settings()
        if (primary["display_type"] == "Extend"
                and (primary.get("virtual_display_creator", "native") != "vkms"
                     or not self.vkmsCreatorAvailable)
                and not self.ensureNativeCompositor()):
            self._set_preset_launch_status("Select a supported desktop before starting this preset.")
            return
        self._cancel_settings_open()
        self.session.preset_configuration = copy.deepcopy(preset)
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
        self._cancel_settings_open()
        self.network_timer.stop()
        self.streaming.stop()
