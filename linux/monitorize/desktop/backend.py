"""QML-facing facade for Sunshine display sessions."""

import json
import copy
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QGuiApplication

from monitorize.config import app_log, autostart
from monitorize.config.settings import (
    CAPTURE_MODES,
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
from monitorize.platform.system_setup import (
    apply_system_setup, get_system_setup_status, parse_system_setup_result,
    system_setup_command,
)
from monitorize.platform.utils import LINUX_DIR, get_local_ip
from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient, VkmsCliNotFoundError
from monitorize.platform.vkms_backend import open_monitorize_vkms_install_page


class MonitorizeBackend(QObject):
    UI_LOG_TAIL_BYTES = 16 * 1024
    _desktopFallbackFinished = pyqtSignal(object)
    configureDisplayRequestedForDraft = pyqtSignal()
    configurationChanged = pyqtSignal()
    configurationApplyFinished = pyqtSignal(bool, str)
    themeModeChanged = pyqtSignal()
    accentChanged = pyqtSignal()
    sunshineSettingsChanged = pyqtSignal()
    sessionChanged = pyqtSignal()
    detectedDeChanged = pyqtSignal(str)
    localIpChanged = pyqtSignal(str)
    isStreamingChanged = pyqtSignal(bool)
    streamingStartFailed = pyqtSignal()
    vkmsReinstallRequired = pyqtSignal()
    streamingCodecMismatch = pyqtSignal(str)
    streamingStatusChanged = pyqtSignal(str)
    logAppended = pyqtSignal(str, str)
    secondStreamActiveChanged = pyqtSignal(bool)
    configureDisplayRequested = pyqtSignal()
    clientDisplaySettingsChanged = pyqtSignal()
    presetsChanged = pyqtSignal()
    presetLaunchStatusChanged = pyqtSignal(str)
    systemSetupAvailableChanged = pyqtSignal(bool)
    systemSetupPendingChanged = pyqtSignal(bool)
    streamingBackendChanged = pyqtSignal(str)
    vkmsHelperAvailabilityChanged = pyqtSignal()
    _vkmsWorkerFinished = pyqtSignal(bool, str, bool)
    virtualDisplayCleanupChanged = pyqtSignal()
    virtualDisplayCleanupFinished = pyqtSignal(bool, str)
    uiVisibleChanged = pyqtSignal(bool)
    systemSetupFinished = pyqtSignal("QVariantMap")
    bootWifiChanged = pyqtSignal()
    bootWifiFinished = pyqtSignal("QVariantMap")
    systemSetupRunningChanged = pyqtSignal(bool)
    pairMoonlightFinished = pyqtSignal(int, bool, str)
    pairingRunningChanged = pyqtSignal(bool)
    _pairingWorkerFinished = pyqtSignal(int, bool, str)
    encodingGpuOptionsReady = pyqtSignal(int, str, "QVariant")
    _encodingGpuWorkerFinished = pyqtSignal(int, str, object)
    mirrorScreensChanged = pyqtSignal()
    mirrorOutputsReady = pyqtSignal(int, "QVariant")
    _mirrorWorkerFinished = pyqtSignal(int, object, object)
    sunshineChoicesSavingChanged = pyqtSignal(bool)
    sunshineChoicesFinished = pyqtSignal(int, bool, str)
    _sunshineChoicesWorkerFinished = pyqtSignal(int, bool, str)
    sunshineSettingsRevisionChanged = pyqtSignal(int)

    vkmsAuthorizationReady = pyqtSignal("QVariantMap")
    _vkmsAuthorizationWorkerFinished = pyqtSignal("QVariantMap")
    remoteLoginChanged = pyqtSignal()
    remoteLoginSetupChanged = pyqtSignal()
    remoteLoginSetupFinished = pyqtSignal("QVariantMap")
    remoteLoginSetupBlocked = pyqtSignal(str)
    _remoteLoginSetupWorkerFinished = pyqtSignal("QVariantMap")

    def __init__(self, de, parent=None):
        super().__init__(parent)
        self._detected_de = de
        self._ui_visible = False
        self.native_compositor_resolver = None
        self._settings_instance = None
        self._settings_deadline = 0.0
        self._settings_message = ""
        self._settings_timer = QTimer(self)
        self._settings_timer.setInterval(250)
        self._settings_timer.timeout.connect(self._poll_sunshine_settings)
        self._virtual_display_cleanup_process = None
        self._system_setup_process = None
        self._system_setup_timed_out = False
        self._system_setup_cancelled = False
        self._background_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="monitorize-ui")
        self._closing = False
        self._vkms_authorization_busy = False
        self._vkmsAuthorizationWorkerFinished.connect(self._finish_vkms_authorization)
        self._remote_login = None
        self._remote_setup_busy = False
        self._remoteLoginSetupWorkerFinished.connect(self._finish_remote_login_setup)
        self._boot_wifi_client = None
        self._boot_wifi_profiles = []
        self._boot_wifi_status = {"code": "checking", "message": "Check NetworkManager Wi-Fi settings."}
        self._pairing_serial = 0
        self._pairing_active = 0
        self._pairingWorkerFinished.connect(self._finish_pairing)
        self._gpu_request_serial = 0
        self._encodingGpuWorkerFinished.connect(self._finish_gpu_options)
        self._mirror_request_serial = 0
        self._mirrorWorkerFinished.connect(self._finish_mirror_outputs)
        self._observed_screens = []
        self._sunshine_save_pending = {}
        self._sunshine_save_active = None
        self._sunshine_save_serial = 0
        self._sunshine_settings_revision = 0
        self._pending_session_start = False
        self._pending_listeners_only = False
        self._listen_on_open_attempted = False
        self._session_start_generation = 0
        self._sunshineChoicesWorkerFinished.connect(self._finish_sunshine_choices)
        self._system_setup_timeout = QTimer(self)
        self._system_setup_timeout.setSingleShot(True)
        self._system_setup_timeout.timeout.connect(self._timeout_system_setup)
        self._local_ip = get_local_ip()
        self._sunshine_available = find_sunshine_command(1) is not None
        from monitorize.desktop.system_accent import system_accent
        self._accent_source = system_accent(QGuiApplication.instance())
        self._accent_source.changed.connect(self._on_system_accent_changed)
        if isinstance(QGuiApplication.instance(), QGuiApplication):
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_accent_changed)
        general = load_general_settings()
        if general.get("use_system_accent", False):
            self._accent_source.refresh()
        self._streaming_backend = general.get("streaming_backend", "sunshine")
        self._web_settings_enabled = general.get("sunshine_web_settings_enabled", False)
        if not self._sunshine_available:
            self._streaming_backend = "none"
        self.streaming = StreamingController(de, self._local_ip, self)
        self.streaming.streaming_backend = self._streaming_backend
        from monitorize.desktop.session import Session
        self.session = Session(self.streaming, self)
        self.session.changed.connect(self.sessionChanged)
        self.session.changed.connect(lambda: self.isStreamingChanged.emit(self.session.active))
        self.session.changed.connect(self._resume_vkms_preset)
        from monitorize.desktop.remote_login_runtime import RemoteLoginRuntime
        self._remote_login = RemoteLoginRuntime(self)
        self._remote_login.changed.connect(self._remote_login_changed)
        self._desktop_fallback=False
        self._desktop_fallback_pending=False
        self._desktop_fallback_generation=0
        self._desktopFallbackFinished.connect(self._finish_desktop_fallback)
        try:
            from monitorize.remote_login.settings import read_settings
            from monitorize.platform.remote_login_identity import desktop_fallback_allowed
            self._desktop_fallback=self._remote_login.enabled and desktop_fallback_allowed(read_settings())
        except (OSError,ValueError):pass
        if self._desktop_fallback:
            self._remote_login.timer.stop();self._remote_login.enabled=False
        from monitorize.desktop.configuration_state import ConfigurationState
        from monitorize.desktop.restart_transaction import RestartTransaction
        effective_general = dict(general, streaming_backend="sunshine" if self._remote_login.enabled else self._streaming_backend)
        self._streaming_backend = effective_general["streaming_backend"]
        self.streaming.streaming_backend = self._streaming_backend
        primary=load_display_settings()
        # Remote services use explicit KMS independently of the saved Auto choice.
        self.configuration_state = ConfigurationState(primary, load_second_display_settings(), effective_general)
        # Persisted intent is not evidence that a stream has ever started.
        self._verified_configuration = None
        self.session.runtime_configuration = copy.deepcopy(self.configuration_state.applied)
        self._restart = RestartTransaction(self._stop_owned_session, self._owned_session_stopped,
            self._launch_configuration, self._configuration_ready, self._configuration_failure,
            self._accept_configuration, self)
        self._restart.changed.connect(self.configurationChanged)
        self._restart.finished.connect(self._restart_finished)
        self._diagnostic_log_cache = {}
        self._presets = load_presets()
        self._preset_launch_status = ""
        self._vkms_resolution_options = [
            "1280x720", "1280x800", "1920x1080", "1920x1200",
            "2560x1440", "2560x1600", "3840x2160", "Custom...",
        ]
        self._vkms_helper_available = False
        self._vkms_install_required = False
        self._vkms_status_message = "Checking host monitorize-vkms…"
        self._vkms_check_running = False
        self._vkms_pending_start = None
        self._vkms_pending_preset = None
        self.streaming.primaryReadyChanged.connect(self._resume_vkms_preset)
        self._vkmsWorkerFinished.connect(self._finish_vkms_check)
        self._system_setup_available = bool(get_system_setup_status()["available"])
        self._system_setup_decided = bool(
            general.get("system_setup_decided", False)
        )
        self.streaming.streamingChanged.connect(self.isStreamingChanged)
        self.streaming.startFailed.connect(self.streamingStartFailed)
        self.streaming.vkmsReinstallRequired.connect(self.vkmsReinstallRequired)
        self.streaming.codecMismatch.connect(self.streamingCodecMismatch)
        self.streaming.statusChanged.connect(self.streamingStatusChanged)
        self.streaming.secondStreamChanged.connect(self.secondStreamActiveChanged)
        self.streaming.logAppended.connect(app_log.write)
        self.streaming.logAppended.connect(self.logAppended)
        self.network_timer = QTimer(self)
        self.network_timer.setInterval(5000)
        self.network_timer.timeout.connect(self._check_network_ip)
        self.network_timer.start()
        app = QGuiApplication.instance()
        if app is not None and hasattr(app, "screens"):
            app.screenAdded.connect(self._screen_added)
            app.screenRemoved.connect(self._screen_removed)
            for screen in app.screens():
                self._observe_screen(screen)

    def _observe_screen(self, screen):
        if screen in self._observed_screens:
            return
        self._observed_screens.append(screen)
        for name in ("geometryChanged", "refreshRateChanged"):
            signal = getattr(screen, name, None)
            if signal is not None:
                signal.connect(self._screen_output_changed)

    def _screen_added(self, screen):
        self._observe_screen(screen)
        self.mirrorScreensChanged.emit()

    def _screen_removed(self, screen):
        if screen in self._observed_screens:
            self._observed_screens.remove(screen)
            for name in ("geometryChanged", "refreshRateChanged"):
                signal = getattr(screen, name, None)
                if signal is not None:
                    try:
                        signal.disconnect(self._screen_output_changed)
                    except (TypeError, RuntimeError):
                        pass
        self.mirrorScreensChanged.emit()

    def _screen_output_changed(self, *_args):
        self.mirrorScreensChanged.emit()

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

    @pyqtProperty(bool, notify=uiVisibleChanged)
    def uiVisible(self):
        return self._ui_visible

    def set_ui_visible(self, visible):
        visible = bool(visible)
        if visible != self._ui_visible:
            self._ui_visible = visible
            self.uiVisibleChanged.emit(visible)

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
            try:
                state = path.stat()
                signature = (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns)
            except OSError:
                signature = None
            cached = self._diagnostic_log_cache.get(label)
            if cached is not None and cached[0] == path and cached[1] == signature:
                content = cached[2]
            else:
                content = app_log.read_tail(path, max_bytes=self.UI_LOG_TAIL_BYTES)
                self._diagnostic_log_cache[label] = (path, signature, content)
            if content:
                sections.append(f"===== {label} =====\n{content}")
        return "\n\n".join(sections) or "No retained diagnostic logs yet."

    @pyqtSlot(int, result=bool)
    def openDiagnosticLog(self, source):
        if source == 0:
            path = Path(app_log.LOG_FILE)
        elif source in (1, 2):
            path = Path(get_sunshine_config_dir(source)) / "sunshine.log"
        else:
            return False
        return path.is_file() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @pyqtProperty(bool, notify=remoteLoginSetupChanged)
    def remoteLoginSetupBusy(self):
        return self._remote_setup_busy

    @pyqtSlot(result="QVariantMap")
    def getRemoteLoginSetupStatus(self):
        from monitorize.platform.system_setup import remote_login_setup_status
        return remote_login_setup_status()

    @pyqtSlot(bool)
    def configureRemoteLogin(self, enabled):
        if self.applyingConfiguration:
            self.remoteLoginSetupFinished.emit({"success":False,"message":"Wait for Apply or Restart to finish before changing remote-login setup."})
            return
        if self._remote_setup_busy or self._closing:
            return
        if enabled and str(load_display_settings().get('sunshine_capture','auto')).strip().lower() not in ('auto','kms'):
            message='Select KMS or Monitorize Auto in Sunshine settings before enabling Start at boot.'
            self.remoteLoginSetupFinished.emit({'success':False,'message':message})
            self.remoteLoginSetupBlocked.emit(message)
            return
        if self.session.active or self.session.busy or (getattr(self, "_remote_login", None) is not None and self._remote_login.status.get("capture_ready")):
            self.remoteLoginSetupFinished.emit({"success": False, "message": "Stop the current stream before configuring remote login."})
            return
        self._remote_setup_busy = True
        self.remoteLoginSetupChanged.emit()
        def work():
            from monitorize.platform.system_setup import apply_remote_login_setup
            from monitorize.desktop.remote_login_controller import configure
            try:
                result = apply_remote_login_setup(enabled)
                if result.get("success"):
                    try:
                        configure(enabled)
                    except Exception:
                        result = {**result, "success": False, "partial": True,
                                  "message": "Boot enrollment changed, but the desktop controller could not be configured. Fix the user systemd service and rerun setup before rebooting."}
            except Exception:
                result = {"success": False, "message": "Remote-login setup failed. Inspect enrollment status before retrying."}
            self._remoteLoginSetupWorkerFinished.emit(result)
        self._background_executor.submit(work)

    @pyqtSlot(bool)
    def configureLoginPrimaryDisplay(self, enabled):
        if self.applyingConfiguration:
            self.remoteLoginSetupFinished.emit({"success":False,"message":"Wait for Apply or Restart to finish before changing login-screen settings."})
            return
        if self._remote_setup_busy or self._closing:return
        self._remote_setup_busy=True
        self.remoteLoginSetupChanged.emit()
        def work():
            from monitorize.platform.system_setup import apply_login_primary_display
            try:result=apply_login_primary_display(enabled)
            except Exception:result={"success":False,"message":"Login-screen settings could not be saved. Check the saved option before retrying."}
            self._remoteLoginSetupWorkerFinished.emit({**result,"primary_display_setting":True})
        self._background_executor.submit(work)

    @pyqtSlot(int)
    def configureDisplayIdleTimeout(self, seconds):
        if self._remote_setup_busy or self._closing:return
        self._remote_setup_busy=True
        self.remoteLoginSetupChanged.emit()
        def work():
            from monitorize.platform.system_setup import apply_display_idle_timeout
            try:result=apply_display_idle_timeout(seconds)
            except Exception:result={"success":False,"message":"Display timeout could not be saved. Refresh Settings before retrying."}
            self._remoteLoginSetupWorkerFinished.emit({**result,"timeout_setting":True})
        self._background_executor.submit(work)

    def _finish_remote_login_setup(self, result):
        if self._closing:
            return
        self._remote_setup_busy = False
        if result.get('primary_display_setting') or result.get('timeout_setting'):
            # This changes next boot only. Preserve the active desktop runtime.
            self.remoteLoginSetupChanged.emit()
            self.remoteLoginSetupFinished.emit(result)
            return
        from monitorize.desktop.remote_login_runtime import RemoteLoginRuntime
        previous_enabled=self.remoteLoginEnabled
        if self._remote_login is not None:
            self._remote_login.close()
        # A timeout or partial user-unit failure can follow a successful root
        # enrollment change. Always reread the actual enrollment.
        self._remote_login = RemoteLoginRuntime(self)
        self._remote_login.changed.connect(self._remote_login_changed)
        self._verified_configuration=None
        if self.remoteLoginEnabled:
            for bundle in (self.configuration_state.applied,self.configuration_state.saved,self.configuration_state.draft):
                bundle['streaming_backend']='sunshine'
            self._streaming_backend='sunshine';self.streaming.streaming_backend='sunshine'
            self.session.runtime_configuration=copy.deepcopy(self.configuration_state.applied)
            self.configurationChanged.emit();self.clientDisplaySettingsChanged.emit()
        elif previous_enabled:
            error=self._reconcile_desktop_configuration()
            if error:result={**result,'success':False,'partial':True,'message':error}
        self.remoteLoginSetupChanged.emit()
        self._remote_login_changed()
        self.remoteLoginSetupFinished.emit(result)

    def _reconcile_desktop_configuration(self):
        from monitorize.desktop.configuration_state import save_bundle
        target=copy.deepcopy(self.configuration_state)
        target.reconcile_desktop_capture(self._detected_de)
        error=''
        try:
            if target.saved != self.configuration_state.saved:save_bundle(target.saved)
        except OSError as exc:
            # Publish corrected choices as an unsaved draft only. Disk and saved
            # state must continue to agree when persistence fails.
            self.configuration_state.draft=target.draft
            error='Desktop capture settings could not be saved: '+str(exc)+'. Use Apply or Restart to retry.'
        else:
            self.configuration_state=target
            self.session.runtime_configuration=copy.deepcopy(target.applied)
        self._verified_configuration=None
        self.configurationChanged.emit();self.clientDisplaySettingsChanged.emit()
        return error

    def _remote_login_changed(self):
        if self._closing:
            return
        self.remoteLoginChanged.emit()
        self.sessionChanged.emit()
        self.streamingStatusChanged.emit(self.streamingStatus)
        self.isStreamingChanged.emit(self.isStreaming)

    @pyqtProperty(bool, notify=remoteLoginChanged)
    def remoteLoginEnabled(self):
        runtime = getattr(self, "_remote_login", None)
        return runtime is not None and runtime.enabled and not getattr(self,'_desktop_fallback',False)


    @pyqtProperty(str, notify=remoteLoginChanged)
    def remoteLoginMessage(self):
        return self._remote_login.message if getattr(self, "remoteLoginEnabled", False) else ""

    @pyqtProperty(str, notify=remoteLoginChanged)
    def remoteLoginState(self):
        return self._remote_login.status.get("state", "unavailable") if getattr(self, "remoteLoginEnabled", False) else "disabled"

    @pyqtProperty("QVariant", notify=sessionChanged)
    def sessionDisplays(self):
        if getattr(self, "remoteLoginEnabled", False):
            return []
        return self.session.cards()

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionHasDisplays(self):
        return self.session.count > 0

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionPendingDisplays(self):
        return self.session.pending_displays

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionBusy(self):
        if getattr(self,'_desktop_fallback_pending',False):return True
        if getattr(self, "remoteLoginEnabled", False):
            return self._remote_login.busy
        return self.session.busy or self._vkms_pending_start is not None or self._vkms_pending_preset is not None or bool(self.streaming._vkms_retiring)

    @pyqtProperty(bool, notify=sessionChanged)
    def sessionRunning(self):
        if getattr(self, "remoteLoginEnabled", False):
            return self._remote_login.status.get("listening", False) or self._remote_login.status.get("capture_ready", False)
        if self.session.client_runtime and self.session.client_runtime.active:
            return self.session.ready
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

    def _remote_unavailable(self):
        return self.remoteLoginEnabled and self._remote_login.status.get('state') in ('unavailable','error')

    def _request_desktop_fallback(self, action):
        if self._desktop_fallback_pending or self._closing:return
        self._desktop_fallback_pending=True
        self._desktop_fallback_generation+=1
        generation=self._desktop_fallback_generation
        self.sessionChanged.emit()
        def work():
            from monitorize.platform.system_setup import prepare_desktop_fallback
            try:result=prepare_desktop_fallback()
            except Exception as exc:result={'success':False,'message':'Desktop fallback failed: '+str(exc)}
            result=dict(result,action=action,generation=generation)
            self._desktopFallbackFinished.emit(result)
        self._background_executor.submit(work)

    def _finish_desktop_fallback(self, result):
        if self._closing:return
        self._desktop_fallback_pending=False
        if not result.get('success'):
            self.configurationApplyFinished.emit(False,result.get('message','Could not release boot-service ownership.'))
            self.sessionChanged.emit()
            return
        # Enrollment remains on disk; only this desktop uses ordinary hosting.
        self._desktop_fallback=True
        self._remote_login.close()
        self._remote_login.enabled=False
        error=self._reconcile_desktop_configuration()
        self.remoteLoginChanged.emit();self.sessionChanged.emit()
        if result['generation']!=self._desktop_fallback_generation:return
        if error:
            self.configurationApplyFinished.emit(False,error)
            return
        action=result['action']
        if action=='apply':self.applyConfiguration()
        elif action=='restart':self.restartSession()
        else:self.startSession()

    @pyqtSlot()
    def startSession(self):
        if self.applyingConfiguration:return
        self._pending_listeners_only = False
        self._start_session_checked()

    def startConfiguredListeners(self):
        """Start hosting once per genuine launch; tray restoration is handled by the window."""
        if getattr(self, "remoteLoginEnabled", False):
            return
        if self._listen_on_open_attempted or self._closing:
            return
        self._listen_on_open_attempted = True
        if (self._streaming_backend != "sunshine"
                or not (getattr(self.session, "mode", "Extend") == "Mirror" or self.session.has_client_cards())):
            return
        self._pending_listeners_only = getattr(self.session, "mode", "Extend") != "Mirror"
        self._start_session_checked()

    def _start_session_checked(self, checked=False):
        if self._remote_unavailable():
            self._request_desktop_fallback('start')
            return
        if getattr(self, "remoteLoginEnabled", False):
            self._remote_login.start()
            return
        if self.streaming._vkms_retiring:
            self.streaming._set_status("VKMS displays are still stopping — please wait")
            return
        if self.sunshineChoicesSaving:
            self._pending_session_start = True
            return
        if self.virtualDisplayCleanupRunning:
            return
        config = self.session.configuration()
        if (config["display_type"] == "Extend"
                and config["virtual_display_creator"] == "vkms"):
            if not checked:
                self._vkms_pending_start = ("session", None)
                self.refreshVkmsHelperAvailability()
                self.sessionChanged.emit()
                return
        if (config["display_type"] == "Extend"
                and config["virtual_display_creator"] == "native"
                and not self.ensureNativeCompositor()):
            return
        self._cancel_settings_open()
        self.session.start(listeners_only=self._pending_listeners_only)
        self._pending_listeners_only = False

    @pyqtSlot()
    def stopSession(self):
        if getattr(self,'_desktop_fallback_pending',False):
            self._desktop_fallback_generation+=1
            return
        if hasattr(self, "_restart"):
            self._restart.cancel()
        if getattr(self, "remoteLoginEnabled", False):
            self._remote_login.stop()
            return
        self._session_start_generation += 1
        self._pending_listeners_only = False
        self._vkms_pending_preset = None
        self._vkms_pending_start = None
        self._pending_session_start = False
        self._cancel_settings_open()
        self.session.stop()
        if hasattr(self, "configuration_state"):
            self.session.runtime_configuration = copy.deepcopy(self.configuration_state.applied)
            self._streaming_backend = self.configuration_state.applied["streaming_backend"]
            self.streaming.streaming_backend = self._streaming_backend
            self.configurationChanged.emit()

    @pyqtSlot(int)
    def removeSessionDisplay(self, index):
        if index != 1 or self.applyingConfiguration:return
        self._update_draft("second", {"enabled":False})
        self.clientDisplaySettingsChanged.emit()
        self.configureDisplayRequestedForDraft.emit()

    @pyqtProperty(str, notify=localIpChanged)
    def localIp(self):
        return self._local_ip

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self.session.active or bool(getattr(self, "_remote_login", None) and self._remote_login.active)

    @pyqtProperty(bool, notify=sessionChanged)
    def canSavePreset(self):
        if self.session.client_runtime and self.session.client_runtime.active:
            return self.session.ready and not self.session.busy
        if not self.streaming.streaming or not self.streaming.primary_ready or self.session.busy:
            return False
        second = (self.streaming.pending_options or {}).get("second") or {}
        return not second.get("enabled") or self.streaming.third_ready

    @pyqtProperty(str, notify=streamingStatusChanged)
    def streamingStatus(self):
        return self.remoteLoginMessage if getattr(self, "remoteLoginEnabled", False) else self.streaming.status

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
        return True

    @pyqtProperty("QVariant", constant=True)
    def vkmsResolutionOptions(self):
        return list(self._vkms_resolution_options)

    @pyqtProperty(bool, notify=vkmsHelperAvailabilityChanged)
    def vkmsHelperAvailable(self):
        return self._vkms_helper_available

    @pyqtProperty(str, notify=vkmsHelperAvailabilityChanged)
    def vkmsAvailabilityMessage(self):
        return self._vkms_status_message

    @pyqtProperty(bool, notify=vkmsHelperAvailabilityChanged)
    def vkmsInstallRequired(self):
        return self._vkms_install_required

    @pyqtProperty("QVariantList", notify=bootWifiChanged)
    def bootWifiProfiles(self):
        return self._boot_wifi_profiles

    @pyqtProperty("QVariantMap", notify=bootWifiChanged)
    def bootWifiStatus(self):
        return self._boot_wifi_status

    @pyqtProperty(bool, notify=bootWifiChanged)
    def bootWifiBusy(self):
        return bool(self._boot_wifi_client and self._boot_wifi_client.busy)

    def _wifi_client(self):
        if self._boot_wifi_client is None:
            from monitorize.platform.networkmanager_boot_wifi import NetworkManagerBootWifi
            client = self._boot_wifi_client = NetworkManagerBootWifi(self)
            client.profilesChanged.connect(self._set_boot_wifi_profiles)
            client.statusChanged.connect(self._set_boot_wifi_status)
            client.busyChanged.connect(self.bootWifiChanged)
            client.operationFinished.connect(self.bootWifiFinished)
        return self._boot_wifi_client

    def _set_boot_wifi_profiles(self, profiles):
        self._boot_wifi_profiles = profiles
        self.bootWifiChanged.emit()

    def _set_boot_wifi_status(self, status):
        self._boot_wifi_status = status
        self.bootWifiChanged.emit()

    @pyqtSlot(bool)
    def setBootWifiPageVisible(self, visible):
        if not self._closing and (visible or self._boot_wifi_client is not None):
            self._wifi_client().set_visible(visible)

    @pyqtSlot()
    def refreshBootWifi(self):
        if not self._closing:
            self._wifi_client().refresh()

    @pyqtSlot(str, str, str)
    def configureBootWifi(self, uuid, version, password):
        if not self._closing:
            self._wifi_client().configure(uuid, version, password)

    @pyqtSlot()
    def refreshVkmsHelperAvailability(self):
        if self._vkms_check_running or self._closing:
            return
        self._vkms_check_running = True
        def check():
            client = MonitorizeVkmsClient()
            try:
                client.require_ready()
                result = (True, "", False)
            except Exception as exc:
                result = (False, str(exc), isinstance(exc, VkmsCliNotFoundError))
            finally:
                client.close()
            if not self._closing:
                self._vkmsWorkerFinished.emit(*result)
        self._background_executor.submit(check)

    def _finish_vkms_check(self, ready, message, install_required=False):
        self._vkms_check_running = False
        self._vkms_helper_available = ready
        self._vkms_status_message = message
        self._vkms_install_required = install_required
        self.vkmsHelperAvailabilityChanged.emit()
        pending, self._vkms_pending_start = self._vkms_pending_start, None
        self.sessionChanged.emit()
        if pending is None or self._closing:
            return
        if not ready:
            self.streaming._set_status(message)
            if pending[0] == "preset":
                self._set_preset_launch_status(message)
            self.streamingStartFailed.emit()
        elif pending[0] == "session":
            self._start_session_checked(True)
        else:
            self._launch_preset_checked(pending[1], True)

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

    @pyqtProperty(str, notify=configurationChanged)
    def appliedStreamingBackend(self):
        return self._streaming_backend

    @pyqtProperty(str, notify=streamingBackendChanged)
    def streamingBackend(self):
        return self.configuration_state.draft["streaming_backend"] if hasattr(self, "configuration_state") else self._streaming_backend

    @pyqtSlot(str)
    def setStreamingBackend(self, value):
        if self.applyingConfiguration:return
        value = value if value in ("sunshine", "none") else "sunshine"
        if value == self.streamingBackend:return
        self.configuration_state.draft["streaming_backend"] = value
        self.streamingBackendChanged.emit(value)
        self.clientDisplaySettingsChanged.emit()
        self.configurationChanged.emit()

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

    @pyqtProperty(bool, notify=systemSetupRunningChanged)
    def systemSetupRunning(self):
        return self._system_setup_process is not None

    @pyqtSlot(bool, bool)
    @pyqtSlot(bool, bool, bool)
    def startSystemSetup(self, enable_input: bool, enable_firewall: bool, enable_vkms=False):
        if self._system_setup_process is not None:
            return
        command, error = (system_setup_command(enable_input, enable_firewall, True) if enable_vkms
                          else system_setup_command(enable_input, enable_firewall))
        if error:
            self.systemSetupFinished.emit(error)
            return
        process = QProcess(self)
        self._system_setup_timed_out = False
        self._system_setup_cancelled = False
        self._system_setup_process = process
        self.systemSetupRunningChanged.emit(True)
        process.finished.connect(self._finish_system_setup)
        process.errorOccurred.connect(self._system_setup_process_error)
        process.start(command[0], command[1:])
        if self._system_setup_process is process:
            self._system_setup_timeout.start(60000)

    @pyqtSlot()
    def refreshVkmsAuthorization(self):
        if self._closing or self._vkms_authorization_busy:
            return
        self._vkms_authorization_busy = True
        def work():
            from monitorize.platform.monitorize_vkms_dbus import VkmsServiceUnavailableError, VkmsProtocolError, VkmsCliNotFoundError
            result = None
            for attempt in range(3):
                client = MonitorizeVkmsClient()
                try:
                    result = client.get_authorization_state()
                    break
                except VkmsServiceUnavailableError as exc:
                    result = {"success": False, "message": str(exc)}
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                except (VkmsProtocolError, VkmsCliNotFoundError) as exc:
                    result = {"success": False, "host_update_required": True, "message": str(exc)}
                    break
                except Exception as exc:
                    result = {"success": False, "message": str(exc)}
                    break
                finally:
                    client.close()
            self._vkmsAuthorizationWorkerFinished.emit(result)
        self._background_executor.submit(work)

    @pyqtSlot("QVariantMap")
    def _finish_vkms_authorization(self, result):
        self._vkms_authorization_busy = False
        if not self._closing:
            self.vkmsAuthorizationReady.emit(result)

    @pyqtSlot(result='QVariantMap')
    def getVkmsAuthorizationStatus(self):
        from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient
        client = MonitorizeVkmsClient()
        try:
            return client.get_authorization_state()
        except Exception as exc:
            return {'success': False, 'message': str(exc)}
        finally:
            client.close()

    @pyqtSlot(bool)
    def setVkmsUnattended(self, enabled):
        if self._system_setup_process is not None or self.session.active or self.session.busy:
            return
        from monitorize.platform.system_setup import vkms_authorization_command
        command, error = vkms_authorization_command(enabled)
        if error:
            self.systemSetupFinished.emit(error)
            return
        process = QProcess(self)
        self._system_setup_timed_out = self._system_setup_cancelled = False
        self._system_setup_process = process
        self.systemSetupRunningChanged.emit(True)
        process.finished.connect(self._finish_system_setup)
        process.errorOccurred.connect(self._system_setup_process_error)
        process.start(command[0], command[1:])
        if self._system_setup_process is process:
            self._system_setup_timeout.start(60000)

    def _complete_system_setup(self, result):
        process = self._system_setup_process
        if process is None:
            return
        self._system_setup_process = None
        self._system_setup_timeout.stop()
        process.deleteLater()
        self.systemSetupRunningChanged.emit(False)
        updated = bool(get_system_setup_status()["available"])
        if updated != self._system_setup_available:
            self._system_setup_available = updated
            self.systemSetupAvailableChanged.emit(updated)
        self.systemSetupFinished.emit(result)

    def _finish_system_setup(self, exit_code, _exit_status):
        process = self._system_setup_process
        if process is None:
            return
        if self._system_setup_cancelled:
            result = {"success": False, "message": "System setup cancelled."}
        elif self._system_setup_timed_out:
            result = {"success": False, "message": "System setup timed out."}
        else:
            result = parse_system_setup_result(
                exit_code,
                bytes(process.readAllStandardOutput()).decode("utf-8", "replace"),
                bytes(process.readAllStandardError()).decode("utf-8", "replace"),
            )
        self._complete_system_setup(result)

    def _system_setup_process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._complete_system_setup({"success": False, "message": "Could not start system setup."})

    def _timeout_system_setup(self):
        process = self._system_setup_process
        if process is not None:
            self._system_setup_timed_out = True
            process.kill()

    @pyqtSlot()
    def markSystemSetupDecided(self):
        if self._system_setup_decided:
            return
        self._system_setup_decided = True
        save_general_settings(system_setup_decided=True)
        self.systemSetupPendingChanged.emit(self.systemSetupPending)

    @pyqtSlot(result="QVariant")
    def loadDisplaySettings(self):
        return copy.deepcopy(self.configuration_state.draft["primary"])

    @pyqtSlot(result="QVariantMap")
    def loadDisplayBehavior(self):
        from monitorize.platform.session_layout import layout_supported
        return {"value": self.configuration_state.draft['display_behavior'],
                "supported": layout_supported(self.streaming.de)}

    @pyqtSlot(str, result=bool)
    def saveDisplayBehavior(self, value):
        from monitorize.platform.session_layout import MODES, layout_supported
        if self.applyingConfiguration or value not in MODES:
            return False
        if value != 'extend' and not layout_supported(self.streaming.de):
            return False
        self.configuration_state.draft["display_behavior"] = value
        self.configurationChanged.emit()
        return True

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
        primary = self.loadDisplaySettings()
        displays = [{
            "id": 1,
            "resolution": primary["resolution"],
            "custom_w": primary["custom_w"],
            "custom_h": primary["custom_h"],
            "fps": primary["fps"],
            "custom_fps": primary["custom_fps"],
            "use_client_display_settings": primary["use_client_display_settings"],
        }]
        second = self.loadSecondDisplaySettings()
        if second.get("enabled", False):
            displays.append({
                "id": 2,
                "resolution": second["resolution"],
                "custom_w": second["custom_w"],
                "custom_h": second["custom_h"],
                "fps": second["fps"],
                "custom_fps": second["custom_fps"],
                "use_client_display_settings": second["use_client_display_settings"],
            })
        return displays

    @pyqtSlot("QVariantList")
    def saveVirtualDisplaySettings(self, display_configs):
        """Persist one or two independent display-mode configurations."""
        if self.applyingConfiguration:
            return
        configs = [dict(config) for config in list(display_configs or []) if isinstance(config, dict)]
        if not configs:
            return
        configs = configs[:2]
        mode_keys = ("resolution", "custom_w", "custom_h", "fps", "custom_fps",
                     "use_client_display_settings")

        primary = self.loadDisplaySettings()
        primary.update({key: configs[0].get(key, primary[key]) for key in mode_keys})
        self._update_draft("primary", primary)

        second = self.loadSecondDisplaySettings()
        if len(configs) == 2:
            second.update({key: configs[1].get(key, second[key]) for key in mode_keys})
            second["enabled"] = True
        else:
            second["enabled"] = False
        self._update_draft("second", second)
        self.clientDisplaySettingsChanged.emit()

        self.session.preset_configuration = None
        self.session.configuration_changed()

    @pyqtSlot(str, result="QVariant")
    def getEncodingGpuOptions(self, encoder):
        return encoding_gpu_options(encoder)

    @pyqtSlot(str, result=int)
    def requestEncodingGpuOptions(self, encoder):
        if self._closing:
            return 0
        self._gpu_request_serial += 1
        request = self._gpu_request_serial

        def work():
            try:
                options = encoding_gpu_options(encoder)
            except Exception:
                options = []
            self._encodingGpuWorkerFinished.emit(request, encoder, options)

        self._background_executor.submit(work)
        return request

    @pyqtSlot(int, str, object)
    def _finish_gpu_options(self, request, encoder, options):
        if not self._closing:
            self.encodingGpuOptionsReady.emit(request, encoder, options)

    @pyqtSlot(result="QVariant")
    def getMirrorOutputs(self):
        from monitorize.platform.mirror_outputs import active_outputs

        return active_outputs(self._detected_de)

    @pyqtSlot(result=int)
    def requestMirrorOutputs(self):
        if self._closing:
            return 0
        from monitorize.platform.mirror_outputs import screen_outputs, _compositor_modes

        outputs = screen_outputs()
        self._mirror_request_serial += 1
        request = self._mirror_request_serial
        desktop = self._detected_de

        def work():
            try:
                modes = _compositor_modes(str(desktop or "").lower())
            except Exception:
                modes = {}
            self._mirrorWorkerFinished.emit(request, outputs, modes)

        self._background_executor.submit(work)
        return request

    @pyqtSlot(int, object, object)
    def _finish_mirror_outputs(self, request, outputs, modes):
        if self._closing:
            return
        from monitorize.platform.mirror_outputs import apply_modes

        self.mirrorOutputsReady.emit(request, apply_modes(outputs, modes))

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
        if self.applyingConfiguration:return
        previous = self.loadDisplaySettings()
        previous_gpu = previous.get("sunshine_gpu", "")
        self._update_draft("primary", dict(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            display_type=display_type,
            sunshine_encoder=sunshine_encoder,
            sunshine_gpu=sunshine_gpu,
            sunshine_codec=sunshine_codec,
            sunshine_capture=previous.get("sunshine_capture", "auto"),
            use_client_display_settings=previous.get("use_client_display_settings", False),
            streaming_customized=streaming_customized,
            sunshine_native_pen_touch=sunshine_native_pen_touch,
            enable_audio=enable_audio,
            mirror_output=mirror_output,
            virtual_display_creator=(
                virtual_display_creator
                if self.vkmsCreatorAvailable else "native"
            ),
        ))
    @pyqtSlot(int, "QVariantMap")
    def saveSunshineDisplaySettings(self, instance, values, sync_adapter=True):
        if instance not in (1, 2) or self.applyingConfiguration:
            return
        load = self.loadDisplaySettings if instance == 1 else self.loadSecondDisplaySettings
        save = lambda **values: self._update_draft("primary" if instance == 1 else "second", values)
        saved = load()
        previous_gpu = saved.get("sunshine_gpu", "")
        capture = str(values.get("sunshine_capture", "auto")).lower()
        if capture not in CAPTURE_MODES:
            capture = "auto"
        for key in (
            "sunshine_encoder", "sunshine_gpu", "sunshine_codec",
            "sunshine_native_pen_touch", "enable_audio", "streaming_customized",
        ):
            if key in values:
                saved[key] = values[key]
        saved["sunshine_capture"] = capture
        save(**saved)
        self._sunshine_settings_revision += 1
        self.sunshineSettingsRevisionChanged.emit(self._sunshine_settings_revision)

    @pyqtProperty(int, notify=sunshineSettingsRevisionChanged)
    def sunshineSettingsRevision(self):
        return self._sunshine_settings_revision

    def _prepare_sunshine_choices(self, instance, submitted):
        if instance not in (1, 2) or self.applyingConfiguration:
            return {"error": "Wait for Apply to finish."}
        if self._closing:
            return {"error": "Monitorize is closing."}
        customized = bool(submitted.get("streaming_customized"))
        encoder = str(submitted.get("sunshine_encoder", "Auto")) if customized else "Auto"
        codec = str(submitted.get("sunshine_codec", "Auto")) if customized else "Auto"
        encoder_values = {"Auto": "", "NVIDIA": "nvenc", "VA-API": "vaapi",
                          "Vulkan": "vulkan", "Software": "software"}
        codec_values = {"Auto": ("0", "0"), "H.264": ("1", "1"),
                        "HEVC": ("2", "1"), "AV1": ("1", "2")}
        if encoder not in encoder_values or codec not in codec_values:
            return {"error": "Unsupported Sunshine encoder or codec."}
        capture = str(submitted.get("sunshine_capture", "auto")).lower()
        if capture not in CAPTURE_MODES:
            return {"error": "Unsupported capture mode."}
        values = {
            "sunshine_encoder": encoder,
            "sunshine_gpu": str(submitted.get("sunshine_gpu", "")) if customized else "",
            "sunshine_codec": codec,
            "sunshine_capture": capture,
            "streaming_customized": customized,
            "sunshine_native_pen_touch": bool(submitted.get("sunshine_native_pen_touch")),
            "enable_audio": bool(submitted.get("enable_audio")),
        }
        saved = self.loadDisplaySettings() if instance == 1 else self.loadSecondDisplaySettings()
        return {"values":values, "config_patch":{}, "config_changed":False,
                "settings_changed":any(saved.get(key) != value for key,value in values.items())}

    @pyqtSlot(int, "QVariantMap", result="QVariantMap")
    def saveSunshineChoices(self, instance, submitted):
        prepared = self._prepare_sunshine_choices(instance, submitted)
        if "error" in prepared:return {"success": False, "message": prepared["error"]}
        self.saveSunshineDisplaySettings(instance, prepared["values"], sync_adapter=False)
        return {"success": True, "message": "Changes remain unapplied."}

    @pyqtProperty(bool, notify=sunshineChoicesSavingChanged)
    def sunshineChoicesSaving(self):
        return self._sunshine_save_active is not None or bool(self._sunshine_save_pending)

    @pyqtSlot(int, "QVariantMap", result="QVariantMap")
    def requestSaveSunshineChoices(self, instance, submitted):
        result = self.saveSunshineChoices(instance, submitted)
        QTimer.singleShot(0, lambda: self.sunshineChoicesFinished.emit(instance, result["success"], "" if result["success"] else result["message"]))
        return {"accepted": result["success"], "message": "" if result["success"] else result["message"]}

    def _launch_next_sunshine_save(self):
        if self._closing or self._sunshine_save_active is not None:
            return
        while self._sunshine_save_pending:
            instance = next(iter(self._sunshine_save_pending))
            submitted = self._sunshine_save_pending.pop(instance)
            prepared = self._prepare_sunshine_choices(instance, submitted)
            if "error" in prepared:
                self.sunshineChoicesFinished.emit(instance, False, prepared["error"])
                continue
            if not prepared["config_changed"]:
                if prepared["settings_changed"]:
                    self.saveSunshineDisplaySettings(instance, prepared["values"], sync_adapter=False)
                self.sunshineChoicesFinished.emit(instance, True, "")
                continue
            self._sunshine_save_serial += 1
            request = self._sunshine_save_serial
            self._sunshine_save_active = (request, instance, prepared)
            config_patch = prepared["config_patch"]

            def work():
                try:
                    success, message = save_sunshine_config(config_patch, instance=instance)
                except Exception as exc:
                    success, message = False, f"Could not save Sunshine settings: {exc}"
                self._sunshineChoicesWorkerFinished.emit(request, success, message)

            self._background_executor.submit(work)
            return
        self.sunshineChoicesSavingChanged.emit(False)
        if self._pending_session_start:
            self._pending_session_start = False
            generation = self._session_start_generation
            def resume_start():
                if not self._closing and generation == self._session_start_generation:
                    self._start_session_checked()
            QTimer.singleShot(0, resume_start)

    @pyqtSlot(int, bool, str)
    def _finish_sunshine_choices(self, request, success, message):
        active = self._sunshine_save_active
        if self._closing or active is None or active[0] != request:
            return
        _, instance, prepared = active
        self._sunshine_save_active = None
        if success and prepared["settings_changed"]:
            if self.isStreaming or self.sessionBusy:
                success, message = False, "Session started before Sunshine settings were saved."
            else:
                self.saveSunshineDisplaySettings(instance, prepared["values"], sync_adapter=False)
        if not success:
            self._pending_session_start = False
            app_log.write("SUNSHINE", message, level=logging.ERROR)
        self.sunshineChoicesFinished.emit(instance, success, "" if success else message)
        self._launch_next_sunshine_save()

    def _restart_finished(self, success, message):
        if not success and self._restart.publish:
            message='Settings saved. Restart failed: '+message
        if success:
            self._verified_configuration=copy.deepcopy(self.session.runtime_configuration)
            if self.remoteLoginEnabled and not self._remote_login.status.get('capture_ready'):
                message='Changes applied. Listener ready; video is checked when Moonlight connects.'
        if success and self._restart.publish and getattr(self, "_display_only_idle", False):
            message = "Changes saved. Use Create in Session to create the display."
        self._restart.message=message
        self.configurationChanged.emit()
        self.configurationApplyFinished.emit(success, message)

    def _update_draft(self, group, values):
        if self.applyingConfiguration:return
        self.configuration_state.update(group, values)
        self.configurationChanged.emit()

    @pyqtProperty(bool, notify=configurationChanged)
    def configurationDirty(self):
        return self.configuration_state.dirty

    @pyqtProperty(bool, notify=configurationChanged)
    def configurationRestartRequired(self):
        return self.configuration_state.restart_required

    @pyqtSlot()
    def saveConfigurationForLater(self):
        if self.applyingConfiguration or self._closing:return
        try:
            candidate=self.configuration_state.candidate(desktop=self._detected_de,remote_login=self.remoteLoginEnabled)
            from monitorize.desktop.configuration_state import save_bundle
            save_bundle(candidate)
            self.configuration_state.save_later(candidate)
            self.configurationChanged.emit()
            self.configurationApplyFinished.emit(True,'Changes saved. Restart the session to apply them.')
        except Exception as exc:
            self.configurationApplyFinished.emit(False,str(exc))

    @pyqtProperty(bool, notify=configurationChanged)
    def configurationValid(self):
        return not self.configurationValidationMessage

    @pyqtProperty(str, notify=configurationChanged)
    def configurationValidationMessage(self):
        try:self.configuration_state.candidate(desktop=self._detected_de,remote_login=self.remoteLoginEnabled)
        except ValueError as exc:return str(exc)
        return ""

    @pyqtProperty(bool, notify=configurationChanged)
    def applyingConfiguration(self):
        return hasattr(self, "_restart") and self._restart.busy

    @pyqtProperty(str, notify=configurationChanged)
    def configurationMessage(self):
        return self._restart.message if hasattr(self, "_restart") else ""

    @pyqtSlot()
    def applyConfiguration(self):
        if self.applyingConfiguration or self._closing:return
        saved=False
        try:
            candidate = self.configuration_state.candidate(desktop=self._detected_de,remote_login=self.remoteLoginEnabled)
            # Persist selected intent before any runtime authorization or restart.
            # A failed launch may restore a working session, never the saved choices.
            from monitorize.desktop.configuration_state import save_bundle
            save_bundle(candidate)
            self.configuration_state.save_later(candidate)
            saved=True
            self.configurationChanged.emit()
            if self._remote_unavailable():
                self._request_desktop_fallback('apply')
                return
            if self.remoteLoginEnabled:self._validate_remote_candidate(candidate)
            if candidate["streaming_backend"] == "sunshine" and not self.remoteLoginEnabled:
                if not self.sunshineAvailable:raise ValueError("Install Monitorize Sunshine before enabling streaming.")
                from monitorize.platform.sunshine_service import get_sunshine_kms_setup_error
                for instance, raw in ((1, candidate["primary"]), (2, candidate["second"])):
                    if instance == 2 and not raw.get("enabled"):continue
                    if raw.get("sunshine_capture") == "kms":
                        error = get_sunshine_kms_setup_error(instance)
                        if error:raise ValueError(error)
                if candidate["primary"].get("virtual_display_creator") == "vkms":
                    MonitorizeVkmsClient().require_unattended()
            for instance, raw in ((1,candidate["primary"]),(2,candidate["second"])):
                if instance == 2 and not raw.get("enabled"):continue
                if raw.get("streaming_customized") and raw.get("sunshine_gpu") and not resolve_encoding_gpu(raw.get("sunshine_encoder","Auto"),raw["sunshine_gpu"]):
                    raise ValueError("Selected encoding GPU is unavailable.")
            self._restart_display_only = self.session.active and self._streaming_backend == "none"
            self._restart.begin(candidate, self._verified_configuration)
        except Exception as exc:
            message=('Settings saved. Restart failed: ' if saved else 'Settings could not be saved: ')+str(exc)
            self.configurationApplyFinished.emit(False, message)

    @pyqtSlot()
    def restartSession(self):
        if self.applyingConfiguration or self._closing:return
        if self._remote_unavailable():
            self._request_desktop_fallback('restart')
            return
        # An explicit Restart is also an explicit request to use current choices.
        self.applyConfiguration()

    def _stop_owned_session(self):
        # Intentional stop cancels timers before cleanup; do not cancel this transaction.
        self._session_start_generation += 1
        self._vkms_pending_start = None
        self._vkms_pending_preset = None
        self._pending_session_start = False
        self._pending_listeners_only = False
        self._cancel_settings_open()
        if self.remoteLoginEnabled:self._remote_login.stop()
        else:self.session.stop()

    def _owned_session_stopped(self):
        if self.remoteLoginEnabled:
            return not self._remote_login.busy and self._remote_login.status.get("state") == "stopped"
        runtime = self.session.client_runtime
        return (not self.session.active and not self.session.busy
                and not (runtime and runtime.cleanup_blocked)
                and not any(is_sunshine_running(i) for i in (1,2)))

    def _launch_configuration(self, bundle):
        self._streaming_backend = bundle["streaming_backend"]
        self.streaming.streaming_backend = self._streaming_backend
        self.session.preset_configuration = None
        self.session.runtime_configuration = copy.deepcopy(bundle)
        self.session.count = 0 if bundle["primary"].get("display_type") == "Mirror" else (2 if bundle["second"].get("enabled") else 1)
        if self.remoteLoginEnabled:
            # The fixed desktop service consumes its own saved Sunshine profile.
            options = self._remote_stream_options(bundle)
            if self._remote_login.status.get("desktop_options_version", 0) < 1:
                from monitorize.desktop.remote_login_runtime import legacy_stream_options
                legacy_stream_options(options,write=True,keep_enrolled_encoder=True)
            self._remote_login.start(options,display_behavior=bundle['display_behavior'])
        elif bundle["streaming_backend"] == "none":
            # Saving an idle display-only draft never creates a display on its own.
            self._display_only_idle = not getattr(self, "_restart_display_only", False)
            if not self._display_only_idle:self._start_session_checked()
        else:
            self._display_only_idle = False
            self._start_session_checked()
        self.sessionChanged.emit()

    def _configuration_ready(self):
        if self.remoteLoginEnabled:
            return self._remote_login.status.get("listening", False) or self._remote_login.status.get("capture_ready", False)
        if getattr(self, "_display_only_idle", False):return True
        runtime = self.session.client_runtime
        if runtime and runtime.active:
            expected = self.session.count
            return len(runtime.slots) == expected and all(slot.status in ("waiting", "ready", "streaming", "reconnecting") for slot in runtime.slots.values())
        return self.session.ready

    def _configuration_failure(self):
        if self.remoteLoginEnabled:
            if self._remote_login.status.get("state") in ("error", "unavailable"):
                return self._remote_login.message
            return ""
        runtime = self.session.client_runtime
        if runtime:
            for slot in runtime.slots.values():
                if slot.status == "error":return slot.error or runtime.error or "Listener startup failed."
        if self._vkms_pending_start is None and not self.session.active and not self.session.busy and not getattr(self, "_display_only_idle", False):
            return self.streaming.status or "Session failed to start."
        return ""

    def _accept_configuration(self, bundle):
        draft=copy.deepcopy(self.configuration_state.draft)
        self.configuration_state.accept(bundle)
        if draft != bundle:
            self.configuration_state.draft=draft
        self.configurationChanged.emit()
        self.clientDisplaySettingsChanged.emit()

    def _validate_remote_candidate(self, candidate):
        if str(candidate['primary'].get('sunshine_capture','auto')).strip().lower() not in ('auto','kms'):
            raise ValueError('Remote login requires KMS or Monitorize Auto capture.')
        if candidate['streaming_backend']!='sunshine':
            raise ValueError('Disable remote login before switching to display-only mode.')
        if self._remote_login.status.get('desktop_options_version',0)<1:
            self._validate_legacy_remote_changes(candidate)
        allowed={'sunshine_encoder','sunshine_gpu','sunshine_codec','sunshine_capture',
                 'streaming_customized','sunshine_native_pen_touch','enable_audio'}
        if self._remote_login.status.get('desktop_mode_version',0)>=1:
            allowed.update({'use_client_display_settings','resolution','fps','custom_w','custom_h','custom_fps'})
        old=self.configuration_state.applied
        if (any(candidate['primary'].get(k)!=old['primary'].get(k) for k in set(candidate['primary'])-allowed)
                or candidate['second']!=old['second']):
            raise ValueError('Remote login currently uses its reserved VKMS display. Disable remote login before changing its display modes or number of displays.')

    def _validate_legacy_remote_changes(self, bundle):
        # Old services already force the enrolled encoder even when the UI has
        # another selection. Do not block a codec-only change on that old mismatch.
        old=self.configuration_state.applied['primary']
        if any(bundle['primary'].get(k)!=old.get(k) for k in ('sunshine_encoder','sunshine_gpu')):
            raise ValueError('The installed remote-login service fixes its encoder and GPU at setup. Update remote-login setup to change those; codec changes are supported.')
        from monitorize.desktop.remote_login_runtime import legacy_stream_options
        legacy_stream_options(self._remote_stream_options(bundle),keep_enrolled_encoder=True)

    def _remote_stream_options(self, bundle):
        config = self.session._saved_values(bundle['primary'])
        hevc,av1 = {"Auto":("0","0"), "H.264":("1","1"), "HEVC":("2","1"), "AV1":("1","2")}[config["codec"]]
        device = resolve_encoding_gpu(config["encoder"], config["gpu_id"])
        if config["gpu_id"] and not device:raise ValueError("The selected encoding GPU is unavailable.")
        return {"encoder": {"Auto":"", "NVIDIA":"nvenc", "VA-API":"vaapi", "Vulkan":"vulkan", "Software":"software"}[config["encoder"]],
            "hevc_mode":hevc, "av1_mode":av1,
            "adapter_name":device.get("render_node", "") if device else "",
            "native_pen_touch":"enabled" if config["native_pen_touch"] else "disabled",
            "stream_audio":"enabled" if config["enable_audio"] else "disabled"}

    def _on_system_accent_changed(self):
        if self.useSystemAccent:
            from monitorize.desktop.appearance import apply_palette
            apply_palette(QGuiApplication.instance(), self.themeMode)
            self.accentChanged.emit()

    @pyqtProperty(bool, notify=accentChanged)
    def useSystemAccent(self):
        return load_general_settings().get("use_system_accent", False)

    @pyqtProperty("QVariantMap", notify=accentChanged)
    def systemAccentColors(self):
        from monitorize.desktop.appearance import accent_colors
        from PyQt6.QtCore import Qt
        dark = self.themeMode == "dark" or (self.themeMode == "system" and
               QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        return accent_colors(self._accent_source.color, dark)

    @pyqtSlot(bool)
    def setUseSystemAccent(self, enabled):
        save_general_settings(use_system_accent=enabled)
        if enabled:
            self._accent_source.refresh()
        from monitorize.desktop.appearance import apply_palette
        apply_palette(QGuiApplication.instance(), self.themeMode, enabled)
        self.accentChanged.emit()

    @pyqtProperty(str, notify=themeModeChanged)
    def themeMode(self):
        return load_general_settings().get("theme_mode", "system")

    @pyqtSlot(str)
    def setThemeMode(self, mode):
        if mode not in ("system", "light", "dark"):
            return
        save_general_settings(theme_mode=mode)
        from monitorize.desktop.appearance import apply_palette
        from PyQt6.QtWidgets import QApplication
        apply_palette(QApplication.instance(), mode)
        self.themeModeChanged.emit()
        self.accentChanged.emit()

    @pyqtSlot(result="QVariant")
    def loadGeneralSettings(self):
        return load_general_settings()

    @pyqtSlot(bool)
    def saveGeneralSettings(self, minimize):
        save_general_settings(minimize_to_tray=minimize)

    @pyqtProperty(bool, notify=clientDisplaySettingsChanged)
    def clientDisplaySettingsAvailable(self):
        primary = load_display_settings()
        return self.streamingBackend == "sunshine" and primary["display_type"] in ("Mirror","Extend")

    @pyqtSlot(bool, bool)
    def saveClientListenerSettings(self, listen_on_open, restart_on_crash):
        """Legacy caller compatibility; hosting recovery is now built in."""
        return

    @pyqtSlot(result=bool)
    def isAutostartEnabled(self):
        if getattr(self, "remoteLoginEnabled", False):
            return True
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
        if self.isStreaming or self.sessionBusy:
            self.virtualDisplayCleanupFinished.emit(False, "Stop streaming before removing virtual displays")
            return
        from monitorize.streaming.recovery_guardian import recover_before_cleanup
        try:
            if not recover_before_cleanup():
                raise RuntimeError('Display recovery is still running; owned outputs cannot be removed yet.')
        except Exception as exc:
            self.virtualDisplayCleanupFinished.emit(False, str(exc))
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
        if hasattr(self, "configuration_state"):
            if self.applyingConfiguration:return {"success":False,"message":"Wait for Apply to finish."}
            keys=("sunshine_encoder","sunshine_gpu","sunshine_codec","sunshine_capture","streaming_customized","sunshine_native_pen_touch","enable_audio")
            for group in ("primary","second"):
                self._update_draft(group,{key:DISPLAY_DEFAULTS[key] for key in keys})
            self._sunshine_settings_revision += 1
            self.sunshineSettingsRevisionChanged.emit(self._sunshine_settings_revision)
            return {"success":True,"message":"Defaults loaded. Open Configure and click Apply to restart."}
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
            "sunshine_encoder", "sunshine_gpu", "sunshine_codec", "sunshine_capture",
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
            capture=load_display_settings().get("sunshine_capture", "auto"),
        )

    @pyqtSlot()
    def stopStreaming(self):
        self.streaming.stop()

    @pyqtSlot()
    @pyqtSlot(int)
    def openSunshineWebUi(self, instance: int = 1):
        if getattr(self, "remoteLoginEnabled", False) and instance == 1:
            QDesktopServices.openUrl(QUrl("https://localhost:47990"))
            return
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

    @pyqtProperty(bool, notify=pairingRunningChanged)
    def pairingRunning(self):
        return self._pairing_active != 0

    @pyqtSlot(str, int, result=int)
    def startPairMoonlightPin(self, pin, instance):
        if self._closing or self._pairing_active:
            return 0
        self._pairing_serial += 1
        request = self._pairing_serial
        self._pairing_active = request
        self.pairingRunningChanged.emit(True)

        def work():
            try:
                success, message = pair_moonlight_pin(pin, instance=instance)
            except Exception as exc:
                success, message = False, f"Pairing failed: {exc}"
            self._pairingWorkerFinished.emit(request, success, message)

        self._background_executor.submit(work)
        return request

    @pyqtSlot(int, bool, str)
    def _finish_pairing(self, request, success, message):
        if self._closing or request != self._pairing_active:
            return
        self._pairing_active = 0
        self.pairingRunningChanged.emit(False)
        self.pairMoonlightFinished.emit(request, success, message)

    @pyqtSlot(result="QVariantMap")
    @pyqtSlot(int, result="QVariantMap")
    def restartSunshine(self, instance: int = 1):
        runtime = self.session.client_runtime
        if runtime and instance in runtime.slots:
            success = runtime.restart_slot(instance)
            return {"success": success, "message": "Restarting listener." if success else runtime.error}
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
        return copy.deepcopy(self.configuration_state.draft["second"])

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
        previous = self.loadSecondDisplaySettings()
        self._update_draft("second", dict(
            resolution=resolution,
            custom_w=custom_w,
            custom_h=custom_h,
            fps=fps,
            custom_fps=custom_fps,
            sunshine_encoder=encoder,
            sunshine_gpu=gpu_id,
            sunshine_codec=codec,
            sunshine_capture=previous.get("sunshine_capture", "auto"),
            sunshine_native_pen_touch=native_pen_touch,
            enable_audio=enable_audio,
        ))

    @pyqtSlot(str, str, str, str, str, bool, bool)
    def startSecondStream(
        self, res, fps, encoder, gpu_id, codec, native_pen_touch, enable_audio
    ):
        if self.virtualDisplayCleanupRunning:
            return
        self.streaming.start_third(
            res, fps, encoder, codec, native_pen_touch, enable_audio,
            gpu_id=gpu_id,
            capture=load_second_display_settings().get("sunshine_capture", "auto"),
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
        preset = (self.session.configured_snapshot() if self.session.client_runtime and self.session.client_runtime.active
                  else self.streaming.active_configuration())
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
        if self.applyingConfiguration or index < 0 or index >= len(self._presets):return
        preset = copy.deepcopy(self._presets[index])
        draft = self.configuration_state.draft
        draft["primary"].update(preset["primary"])
        draft["primary"]["streaming_customized"] = True
        draft["second"].update(preset.get("second", {}))
        draft["second"]["streaming_customized"] = True
        draft["display_behavior"] = preset.get("display_behavior", "extend")
        draft["streaming_backend"] = "sunshine"
        self.configurationChanged.emit()
        self.clientDisplaySettingsChanged.emit()
        self._set_preset_launch_status("Preset loaded as unapplied changes. Open Configure and click Apply.")
        self.configureDisplayRequestedForDraft.emit()

    def _resume_vkms_preset(self, *_args):
        if self._vkms_pending_preset is None or self.streaming._vkms_retiring or self.session.retiring or self._closing:
            return
        index = self._vkms_pending_preset
        def resume():
            if (self._vkms_pending_preset == index and not self._closing
                    and not self.streaming._vkms_retiring and not self.session.retiring):
                self._vkms_pending_preset = None
                self._launch_preset_checked(index)
        QTimer.singleShot(0, resume)

    def _launch_preset_checked(self, index, checked=False):
        if getattr(self, "remoteLoginEnabled", False):
            self._set_preset_launch_status("Disable remote login at boot before starting a manual preset.")
            return
        if self._closing:
            return
        if index < 0 or index >= len(self._presets):
            self._set_preset_launch_status("Preset no longer exists.")
            return
        candidate = self._presets[index]
        target_client = candidate.get("primary", {}).get("use_client_display_settings", False) or (
            candidate.get("second", {}).get("enabled", False)
            and candidate.get("second", {}).get("use_client_display_settings", False))
        target_client = target_client or candidate.get('display_behavior', 'extend') != 'extend'
        if ((self.session.client_runtime and self.session.client_runtime.active)
                or (self.streaming.streaming and (target_client or self.streaming.virtual_display_creator == "vkms"))):
            self.session.stop()
        if self.streaming._vkms_retiring or self.session.retiring:
            self._vkms_pending_preset = index
            self._set_preset_launch_status("Stopping the previous VKMS session…")
            return
        if self.sunshineChoicesSaving:
            self._set_preset_launch_status("Wait for Sunshine settings to finish saving.")
            return
        if self.virtualDisplayCleanupRunning:
            self._set_preset_launch_status("Wait for virtual display setup to finish.")
            return
        self._set_preset_launch_status("")
        preset = self._presets[index]
        import copy
        primary = preset["primary"]
        if (primary["display_type"] == "Extend"
                and primary.get("virtual_display_creator") == "vkms"):
            if not checked:
                self._vkms_pending_start = ("preset", index)
                self.refreshVkmsHelperAvailability()
                self.sessionChanged.emit()
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
        self.session.count = 2 if preset.get("second", {}).get("enabled") else 1
        if self.session.uses_managed_runtime():
            self.session.start()
            return
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
            capture=primary.get("sunshine_capture", "auto"),
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
        if hasattr(self, "_restart"):
            self._restart.cancel()
        self._vkms_pending_preset = None
        self._vkms_pending_start = None
        self._closing = True
        if self._remote_login is not None:
            self._remote_login.close()
        if self._boot_wifi_client is not None:
            self._boot_wifi_client.close()
        self._background_executor.shutdown(wait=False, cancel_futures=True)
        if self._system_setup_process is not None:
            process = self._system_setup_process
            self._system_setup_cancelled = True
            process.kill()
            process.waitForFinished(1000)
            self._complete_system_setup({"success": False, "message": "System setup cancelled."})
        self._cancel_settings_open()
        self.network_timer.stop()
        self.session.stop()
