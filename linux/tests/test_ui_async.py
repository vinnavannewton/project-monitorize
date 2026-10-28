"""Bounded checks for UI requests that must not block the event loop."""

import json
import sys
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
from monitorize.config import settings
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from monitorize.desktop.backend import MonitorizeBackend
from monitorize.desktop.main_window import MonitorizeWindow


class UiAsyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        config=patch.multiple(settings,CONFIG_DIR=self.temp.name,CONFIG_FILE=str(Path(self.temp.name)/"settings.ini"))
        config.start();self.addCleanup(config.stop)
        isolated_remote=patch('monitorize.desktop.remote_login_runtime.SETTINGS',Path(self.temp.name)/'no-remote.json')
        isolated_remote.start();self.addCleanup(isolated_remote.stop)

    def make_backend(self):
        with (
            patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1"),
            patch("monitorize.desktop.backend.load_general_settings", return_value={}),
            patch("monitorize.desktop.backend.load_presets", return_value=[]),
            patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False}),
            patch("monitorize.desktop.backend.find_sunshine_command", return_value=None),
            patch("monitorize.desktop.backend.StreamingController"),
        ):
            backend = MonitorizeBackend("sway")
        backend.streaming._vkms_retiring = {}
        backend.streaming.streaming = False
        backend.streaming.third_streaming = False
        backend.streaming.primary_ready = False
        backend.streaming.third_ready = False
        self.addCleanup(backend.close)
        return backend

    def wait_for(self, signal):
        loop = QEventLoop()
        signal.connect(loop.quit)
        QTimer.singleShot(1500, loop.quit)
        loop.exec()

    def test_preset_waits_for_vkms_cleanup_and_stop_cancels_restart(self):
        backend = self.make_backend()
        backend.streaming._vkms_retiring = {"holder": {"slot":"mon2"}}
        backend._presets = [{"primary": {}}]
        backend.launchPreset(0)
        self.assertIsNone(backend._vkms_pending_preset)
        self.assertTrue(backend.configurationDirty)
        backend.streaming.start.assert_not_called()
        backend.stopSession()
        self.assertIsNone(backend._vkms_pending_preset)
        backend.streaming._vkms_retiring = {}
        with patch.object(backend,"_launch_preset_checked") as launch:
            backend._resume_vkms_preset()
            self.app.processEvents()
            launch.assert_not_called()

    def test_preset_waits_for_client_holder_cleanup_and_stop_cancels_restart(self):
        backend = self.make_backend()
        backend.session.client_runtime = SimpleNamespace(active=False, busy=False,
            retiring={1: object()}, slots={}, stop=lambda: None)
        backend._presets = [{"primary": {"display_type": "Extend"}}]
        backend.launchPreset(0)
        self.assertIsNone(backend._vkms_pending_preset)
        self.assertTrue(backend.configurationDirty)
        backend.streaming.start.assert_not_called()
        backend.stopSession()
        self.assertIsNone(backend._vkms_pending_preset)

    def test_switching_native_manual_session_to_listener_preset_stops_previous_host(self):
        from monitorize.config.settings import _normalize_preset
        backend = self.make_backend()
        backend._streaming_backend = "sunshine"
        backend.streaming.streaming_backend = "sunshine"
        backend.streaming.streaming = True
        backend.streaming.virtual_display_creator = "native"
        backend.streaming.de = "sway"
        backend.streaming.stop.side_effect = lambda: setattr(backend.streaming, "streaming", False)
        runtime = Mock(active=False, busy=False, retiring={})
        runtime.slots = {}
        def start_slot(instance, config):
            runtime.slots[instance] = SimpleNamespace(status="waiting", config=config, error="")
            runtime.active = True
            return True
        runtime.start_slot.side_effect = start_slot
        backend.session.client_runtime = runtime
        backend._presets = [_normalize_preset({"name": "Remote", "version": 2,
            "primary": {"resolution": "1920x1080", "fps": "60",
                        "use_client_display_settings": True}, "second": {"enabled": False}})]
        with patch.object(backend, "_sync_web_settings"), patch.object(backend, "ensureNativeCompositor", return_value=True):
            backend.launchPreset(0)
        backend.streaming.stop.assert_not_called()
        self.assertEqual(set(runtime.slots), set())
        self.assertTrue(backend.configurationDirty)
        self.assertTrue(backend.configuration_state.draft["primary"]["use_client_display_settings"])

    def test_invalid_apply_does_not_disrupt_existing_engine(self):
        backend=self.make_backend()
        backend.configuration_state.update('primary',{'use_client_display_settings':False,'resolution':'invalid'})
        result=[];backend.configurationApplyFinished.connect(lambda *args:result.append(args))
        backend.applyConfiguration()
        self.assertFalse(result[-1][0])
        self.assertIn('resolution',result[-1][1])
        backend.streaming.stop.assert_not_called()
        self.assertFalse(backend.applyingConfiguration)

    def test_remote_apply_requires_updated_service_before_stopping(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend.configuration_state.draft['streaming_backend']='sunshine'
        backend.configuration_state.update('primary',{'sunshine_capture':'kms','sunshine_encoder':'NVIDIA'})
        result=[];backend.configurationApplyFinished.connect(lambda *args:result.append(args))
        with patch.object(backend._remote_login,'stop') as stop, patch('monitorize.desktop.remote_login_runtime.read_settings',return_value=__import__('types').SimpleNamespace(encoder='nvenc',adapter='')):
            backend.applyConfiguration()
            stop.assert_not_called()
        self.assertIn('fixes its encoder and GPU',result[-1][1])

    def test_legacy_remote_codec_apply_uses_profile_without_version_gate(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        baseline=backend.configuration_state.applied
        baseline['streaming_backend']='sunshine'
        baseline['primary'].update(sunshine_capture='kms',streaming_customized=True,sunshine_encoder='Software',sunshine_codec='HEVC')
        backend.configuration_state.draft=__import__('copy').deepcopy(baseline)
        backend.configuration_state.saved=__import__('copy').deepcopy(baseline)
        backend.configuration_state.update('primary',{'sunshine_codec':'Auto'})
        with patch('monitorize.desktop.remote_login_runtime.legacy_stream_options') as legacy, \
             patch.object(backend._kms_authorization, 'ensure', return_value=True), \
             patch.object(backend._restart,'begin') as begin:
            backend.applyConfiguration()
            self.assertEqual(legacy.call_args.args[0]['hevc_mode'],'0')
            begin.assert_called_once()

    def test_unavailable_remote_restart_requests_desktop_fallback(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._remote_login.status={'state':'unavailable'}
        with patch.object(backend,'_request_desktop_fallback') as request, patch.object(backend._restart,'begin') as begin:
            backend.restartSession()
            request.assert_called_once_with('restart');begin.assert_not_called()
        with patch.object(backend,'_request_desktop_fallback') as request:
            backend.applyConfiguration();request.assert_called_once_with('apply')

    def test_fallback_handoff_changes_runtime_not_boot_preferences(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._desktop_fallback_pending=True;backend._desktop_fallback_generation=7
        policy=SimpleNamespace(enabled=True)
        with patch('monitorize.remote_login.settings.read_settings',return_value=policy), \
             patch('monitorize.platform.remote_login_identity.desktop_fallback_allowed',return_value=True) as allowed, \
             patch.object(backend._remote_login,'close') as close, \
             patch.object(backend,'restartSession') as restart, \
             patch('monitorize.config.settings.save_general_settings') as save:
            backend._finish_desktop_fallback(dict(success=True,generation=7,action='restart'))
            allowed.assert_called_once_with(policy)
            close.assert_called_once();restart.assert_called_once();save.assert_not_called()
        self.assertFalse(backend.remoteLoginEnabled)
        self.assertTrue(backend._desktop_fallback)

    def test_stop_cancels_launch_after_fallback_authorization(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._desktop_fallback_pending=True;backend._desktop_fallback_generation=1
        backend.stopSession()
        with patch('monitorize.remote_login.settings.read_settings',return_value=SimpleNamespace(enabled=True)), \
             patch('monitorize.platform.remote_login_identity.desktop_fallback_allowed',return_value=True), \
             patch.object(backend._remote_login,'close'), patch.object(backend,'startSession') as start:
            backend._finish_desktop_fallback(dict(success=True,generation=1,action='start'))
            start.assert_not_called()

    def test_remote_save_later_keeps_display_intent_without_applying_reserved_runtime(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._remote_login.status={'desktop_options_version':1}
        baseline=backend.configuration_state.applied
        baseline['streaming_backend']='sunshine'
        baseline['primary']['sunshine_capture']='kms'
        backend.configuration_state.draft=__import__('copy').deepcopy(baseline)
        backend.configuration_state.update('primary',{'display_type':'Mirror','mirror_output':'eDP-1'})
        result=[];backend.configurationApplyFinished.connect(lambda *args:result.append(args))
        with patch('monitorize.desktop.configuration_state.save_bundle') as save:
            backend.saveConfigurationForLater();save.assert_called_once()
        self.assertTrue(result[-1][0])
        self.assertEqual(backend.configuration_state.saved['primary']['display_type'],'Mirror')
        self.assertEqual(backend.configuration_state.applied['primary']['display_type'],'Extend')

    def test_remote_layout_behavior_is_accepted_for_desktop(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._remote_login.status={'desktop_options_version':1}
        baseline=backend.configuration_state.applied
        baseline['streaming_backend']='sunshine'
        baseline['primary']['sunshine_capture']='kms'
        backend.configuration_state.draft=__import__('copy').deepcopy(baseline)
        backend.configuration_state.draft['display_behavior']='virtual_only'
        with patch('monitorize.desktop.configuration_state.save_bundle'), patch.object(backend._remote_login,'stop') as stop:
            backend.saveConfigurationForLater();stop.assert_not_called()
        self.assertEqual(backend.configuration_state.saved['display_behavior'],'virtual_only')

    def test_legacy_launch_keeps_target_options_for_participant(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        candidate=__import__('copy').deepcopy(backend.configuration_state.applied)
        candidate['streaming_backend']='sunshine'
        candidate['primary'].update(sunshine_capture='kms',sunshine_codec='Auto')
        with patch('monitorize.desktop.remote_login_runtime.legacy_stream_options'), patch.object(backend,'_remote_stream_options',return_value={'hevc_mode':'0','av1_mode':'0'}), patch.object(backend._remote_login,'start') as start:
            backend._launch_configuration(candidate)
            start.assert_called_once_with({'hevc_mode':'0','av1_mode':'0'},display_behavior=candidate['display_behavior'])

    def test_remote_unsupported_layout_change_does_not_stop_service(self):
        backend=self.make_backend();backend._remote_login.enabled=True
        backend._remote_login.status={'desktop_options_version':1}
        backend.configuration_state.draft['streaming_backend']='sunshine'
        backend.configuration_state.update('primary',{'sunshine_capture':'kms','display_type':'Mirror','mirror_output':'eDP-1'})
        result=[];backend.configurationApplyFinished.connect(lambda *args:result.append(args))
        with patch.object(backend._remote_login,'stop') as stop:
            backend.applyConfiguration();stop.assert_not_called()
        self.assertIn('reserved VKMS display',result[-1][1])

    def test_pending_auto_start_keeps_listener_only_and_stop_cancels_callback(self):
        backend = self.make_backend()
        backend._pending_session_start = True
        backend._pending_listeners_only = True
        with patch("monitorize.desktop.backend.QTimer.singleShot") as schedule:
            backend._launch_next_sunshine_save()
            callback = schedule.call_args.args[1]
        with patch.object(backend, "_start_session_checked") as start:
            callback()
            start.assert_called_once()
            self.assertTrue(backend._pending_listeners_only)
        backend._pending_session_start = True
        with patch("monitorize.desktop.backend.QTimer.singleShot") as schedule:
            backend._launch_next_sunshine_save()
            callback = schedule.call_args.args[1]
        backend.stopSession()
        with patch.object(backend, "_start_session_checked") as start:
            callback()
            start.assert_not_called()

    def test_vkms_preflight_does_not_block_and_stop_cancels_pending_start(self):
        backend = self.make_backend()
        gate = threading.Event()
        self.addCleanup(gate.set)
        def check():
            gate.wait(1)
        with patch("monitorize.desktop.backend.MonitorizeVkmsClient") as client:
            client.return_value.require_ready.side_effect = check
            backend._vkms_pending_start = ("session",None)
            backend.refreshVkmsHelperAvailability()
            self.assertTrue(backend._vkms_check_running)
            backend.stopSession()
            self.assertIsNone(backend._vkms_pending_start)
            gate.set()
            self.wait_for(backend.vkmsHelperAvailabilityChanged)
        self.assertTrue(backend.vkmsHelperAvailable)
        backend.streaming.start.assert_not_called()

    def test_missing_vkms_authorization_api_is_reported_as_host_update(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsProtocolError
        backend=self.make_backend();results=[]
        backend.vkmsAuthorizationReady.connect(results.append)
        with patch('monitorize.desktop.backend.MonitorizeVkmsClient') as client:
            client.return_value.get_authorization_state.side_effect=VkmsProtocolError('Update host service')
            backend.refreshVkmsAuthorization()
            self.wait_for(backend.vkmsAuthorizationReady)
        self.assertTrue(results[-1]['host_update_required'])
        self.assertFalse(results[-1]['success'])
        client.return_value.get_authorization_state.assert_called_once()

    def test_window_visibility_before_backend_initialization(self):
        window = Mock(spec=["isVisible", "isMinimized"])
        window.isVisible.return_value = False
        window.isMinimized.return_value = False
        MonitorizeWindow._update_ui_visibility(window)
        window.backend = Mock()
        MonitorizeWindow._update_ui_visibility(window)
        window.backend.set_ui_visible.assert_called_once_with(False)

    def test_packaged_setup_reports_async_process_result(self):
        backend = self.make_backend()
        responses = []
        backend.systemSetupFinished.connect(responses.append)
        payload = json.dumps({"success": True, "message": "Configured"})
        command = [sys.executable, "-c", f"print({payload!r})"]
        with patch("monitorize.desktop.backend.system_setup_command", return_value=(command, None)):
            backend.startSystemSetup(True, False)
            self.assertTrue(backend.systemSetupRunning)
            self.wait_for(backend.systemSetupFinished)
        self.assertEqual(responses[0]["message"], "Configured")
        self.assertFalse(backend.systemSetupRunning)

    def test_pairing_returns_before_worker_finishes(self):
        backend = self.make_backend()
        responses = []
        backend.pairMoonlightFinished.connect(lambda *args: responses.append(args))
        with patch("monitorize.desktop.backend.pair_moonlight_pin", return_value=(True, "Paired")):
            request = backend.startPairMoonlightPin("1234", 1)
            self.assertGreater(request, 0)
            self.assertTrue(backend.pairingRunning)
            self.assertEqual(backend.startPairMoonlightPin("1234", 1), 0)
            self.wait_for(backend.pairMoonlightFinished)
        self.assertEqual(responses, [(request, True, "Paired")])
        self.assertFalse(backend.pairingRunning)

    def test_mirror_result_keeps_request_identity(self):
        backend = self.make_backend()
        responses = []
        backend.mirrorOutputsReady.connect(lambda *args: responses.append(args))
        output = {"id": "TEST-1", "native_width": 0, "native_height": 0,
                  "refresh_rate": 0.0}
        modes = {"TEST-1": {"width": 1920, "height": 1080, "refresh_rate": 60}}
        with (
            patch("monitorize.platform.mirror_outputs.screen_outputs", return_value=[output]),
            patch("monitorize.platform.mirror_outputs._compositor_modes", return_value=modes),
        ):
            request = backend.requestMirrorOutputs()
            self.wait_for(backend.mirrorOutputsReady)
        self.assertEqual(responses[0][0], request)
        self.assertEqual(responses[0][1][0]["native_width"], 1920)

    def test_sunshine_config_write_finishes_without_blocking_ui(self):
        backend = self.make_backend()
        responses = []
        backend.sunshineChoicesFinished.connect(lambda *args: responses.append(args))
        choices = {"sunshine_encoder": "NVIDIA", "sunshine_codec": "HEVC",
                   "sunshine_capture": "kwin", "sunshine_gpu": "",
                   "streaming_customized": True,
                   "sunshine_native_pen_touch": True, "enable_audio": False}
        with (
            patch("monitorize.desktop.backend.load_display_settings", return_value={}),
            patch("monitorize.desktop.backend.get_saved_sunshine_config", return_value={}),
            patch("monitorize.desktop.backend.save_sunshine_config", return_value=(True, "")) as write,
            patch.object(backend, "saveSunshineDisplaySettings") as persist,
        ):
            accepted = backend.requestSaveSunshineChoices(1, choices)
            self.assertTrue(accepted["accepted"])
            self.assertFalse(backend.sunshineChoicesSaving)
            self.wait_for(backend.sunshineChoicesFinished)
        self.assertEqual(responses, [(1, True, "")])
        write.assert_not_called()
        persist.assert_called_once()
        self.assertFalse(backend.sunshineChoicesSaving)

    def test_rapid_sunshine_choices_finish_with_latest_snapshot(self):
        backend = self.make_backend()
        first_started = threading.Event()
        release_first = threading.Event()
        writes = []
        results = []
        choices = {"sunshine_encoder": "NVIDIA", "sunshine_codec": "HEVC",
                   "sunshine_capture": "kwin", "sunshine_gpu": "",
                   "streaming_customized": True,
                   "sunshine_native_pen_touch": True, "enable_audio": False}

        def save_config(config, instance):
            writes.append(dict(config))
            if len(writes) == 1:
                first_started.set()
                release_first.wait(1)
            return True, ""

        loop = QEventLoop()
        def finished(*args):
            results.append(args)
            if len(results) == 2:
                loop.quit()

        backend.sunshineChoicesFinished.connect(finished)
        with (
            patch("monitorize.desktop.backend.load_display_settings", return_value={}),
            patch("monitorize.desktop.backend.get_saved_sunshine_config", return_value={}),
            patch("monitorize.desktop.backend.save_sunshine_config", side_effect=save_config),
            patch.object(backend, "saveSunshineDisplaySettings") as persist,
        ):
            backend.requestSaveSunshineChoices(1, choices)
            self.assertFalse(first_started.is_set())
            newer = dict(choices, enable_audio=True)
            backend.requestSaveSunshineChoices(1, newer)
            release_first.set()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
        self.assertEqual(len(results), 2)
        self.assertEqual(writes, [])
        self.assertTrue(persist.call_args.args[1]["enable_audio"])
        self.assertFalse(backend.sunshineChoicesSaving)


if __name__ == "__main__":
    unittest.main()
