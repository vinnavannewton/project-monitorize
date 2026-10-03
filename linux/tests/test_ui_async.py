"""Bounded checks for UI requests that must not block the event loop."""

import json
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from monitorize.desktop.backend import MonitorizeBackend
from monitorize.desktop.main_window import MonitorizeWindow


class UiAsyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

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
            self.assertTrue(backend.sunshineChoicesSaving)
            self.wait_for(backend.sunshineChoicesFinished)
        self.assertEqual(responses, [(1, True, "")])
        write.assert_called_once()
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
            self.assertTrue(first_started.wait(1))
            newer = dict(choices, enable_audio=True)
            backend.requestSaveSunshineChoices(1, newer)
            release_first.set()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
        self.assertEqual(len(results), 2)
        self.assertEqual(writes[-1]["stream_audio"], "enabled")
        self.assertTrue(persist.call_args.args[1]["enable_audio"])
        self.assertFalse(backend.sunshineChoicesSaving)


if __name__ == "__main__":
    unittest.main()
