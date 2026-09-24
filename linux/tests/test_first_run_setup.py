import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from monitorize.config import settings
from monitorize.desktop import backend as backend_module
from monitorize.desktop.backend import MonitorizeBackend


ROOT = Path(__file__).resolve().parents[2]


class FirstRunSetupTest(unittest.TestCase):

    @patch("monitorize.desktop.backend.get_sunshine_config_dir")
    @patch("monitorize.desktop.backend.app_log.read_tail")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.load_general_settings", return_value={})
    def test_session_log_combines_both_sunshine_instances_and_monitorize_log(
        self, _settings, _presets, _ip, _streaming, _status, read_tail, config_dir
    ):
        config_dir.side_effect = ["/tmp/sunshine-1", "/tmp/sunshine-2"]
        read_tail.side_effect = ["first", "second", "monitorize"]
        backend = MonitorizeBackend("kde")
        self.addCleanup(backend.network_timer.stop)
        logs = backend.sessionLog()
        self.assertIn("===== Sunshine instance 1 =====\nfirst", logs)
        self.assertIn("===== Sunshine instance 2 =====\nsecond", logs)
        self.assertIn("===== Monitorize =====\nmonitorize", logs)
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        config_dir = Path(self.tempdir.name) / "monitorize"
        self.config_patch = patch.multiple(
            settings,
            CONFIG_DIR=str(config_dir),
            CONFIG_FILE=str(config_dir / "settings.ini"),
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_setup_decision_defaults_false_and_persists_without_resetting_tray_setting(self):
        self.assertFalse(settings.load_general_settings()["system_setup_decided"])
        settings.save_general_settings(minimize_to_tray=True)
        settings.save_general_settings(system_setup_decided=True)
        values = settings.load_general_settings()
        self.assertTrue(values["minimize_to_tray"])
        self.assertTrue(values["system_setup_decided"])

    @patch("monitorize.desktop.backend.save_general_settings")
    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": False})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": True})
    def test_packaged_setup_is_pending_until_a_decision_is_saved(
        self, _status, _streaming, _ip, _presets, _settings, save
    ):
        backend = MonitorizeBackend("sway")
        self.addCleanup(backend.network_timer.stop)
        self.assertTrue(backend.systemSetupPending)
        backend.markSystemSetupDecided()
        self.assertFalse(backend.systemSetupPending)
        save.assert_called_once_with(system_setup_decided=True)

    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": False})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    def test_source_or_nix_install_has_no_pending_setup(
        self, _status, _streaming, _ip, _presets, _settings
    ):
        backend = MonitorizeBackend("kde")
        self.addCleanup(backend.network_timer.stop)
        self.assertFalse(backend.systemSetupPending)

    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": True})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    def test_vkms_custom_capability_is_session_cached_but_starts_unknown(
        self, _status, _streaming, _ip, _presets, _settings
    ):
        backend = MonitorizeBackend("kde")
        self.addCleanup(backend.network_timer.stop)
        self.assertEqual(backend.vkmsCustomEdidCapability, "unknown")
        backend._vkms_custom_capability = backend_module.CustomEdidCapability.SUPPORTED
        self.assertEqual(backend.vkmsCustomEdidCapability, "supported")
        with patch("monitorize.desktop.backend.QTimer.singleShot") as deferred:
            backend.checkVkmsCustomEdidSupport()
        deferred.assert_called_once()

    @patch("monitorize.desktop.backend.apply_system_setup", return_value={"success": False, "message": "Cancelled"})
    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": False})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": True})
    def test_failed_setup_does_not_clear_the_first_run_gate(
        self, _status, _streaming, _ip, _presets, _settings, _apply
    ):
        backend = MonitorizeBackend("kde")
        self.addCleanup(backend.network_timer.stop)
        self.assertFalse(backend.applySystemSetup(True, True)["success"])
        self.assertTrue(backend.systemSetupPending)

    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": True})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": True})
    def test_display_configuration_is_limited_to_wlroots_desktops(
        self, _status, _streaming, _ip, _presets, _settings
    ):
        for desktop, expected in (("hyprland", True), ("sway", True), ("kde", False), ("gnome", False), ("", False)):
            backend = MonitorizeBackend(desktop)
            self.addCleanup(backend.network_timer.stop)
            self.assertEqual(backend.canConfigureDisplay, expected)

    @patch("monitorize.desktop.backend.load_general_settings", return_value={})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    def test_unknown_compositor_is_resolved_only_for_native_session(
        self, _status, _streaming, _ip, _presets, _settings
    ):
        backend = MonitorizeBackend("")
        self.addCleanup(backend.network_timer.stop)
        backend.native_compositor_resolver = lambda: "sway"
        backend.session.configuration = lambda: {
            "display_type": "Extend", "virtual_display_creator": "vkms"
        }
        with patch.object(backend.session, "start") as start:
            backend.startSession()
            self.assertEqual(backend.detectedDe, "")
            start.assert_called_once()
            start.reset_mock()
            backend.session.configuration = lambda: {
                "display_type": "Extend", "virtual_display_creator": "native"
            }
            backend.startSession()
            self.assertEqual(backend.detectedDe, "sway")
            self.assertEqual(backend.streaming.de, "sway")
            self.assertTrue(backend.canConfigureDisplay)
            start.assert_called_once()

    @patch("monitorize.desktop.backend.load_general_settings", return_value={})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    def test_unsupported_compositor_does_not_start_native_session(
        self, _status, _streaming, _ip, _presets, _settings
    ):
        backend = MonitorizeBackend("")
        self.addCleanup(backend.network_timer.stop)
        backend.native_compositor_resolver = lambda: ""
        backend.session.configuration = lambda: {
            "display_type": "Extend", "virtual_display_creator": "native"
        }
        with patch.object(backend.session, "start") as start:
            backend.startSession()
            start.assert_not_called()
        self.assertEqual(backend.detectedDe, "")

    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": True})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    @patch("monitorize.desktop.backend.QProcess")
    def test_stagnant_display_cleanup_returns_toast_result(
        self, process_class, _status, streaming, _ip, _presets, _settings
    ):
        streaming.return_value.streaming = False
        for desktop in ("hyprland", "sway", "kde", "gnome"):
            backend = MonitorizeBackend(desktop)
            self.addCleanup(backend.network_timer.stop)
            results = []
            backend.virtualDisplayCleanupFinished.connect(lambda ok, message: results.append((ok, message)))
            process = process_class.return_value
            process.readAllStandardOutput.return_value = b'MONITORIZE_CLEANUP {"success": true, "message": "Removed virtual displays"}\n'
            backend.removeStagnantVirtualDisplays()
            self.assertTrue(backend.virtualDisplayCleanupRunning)
            self.assertEqual(process.start.call_args.args[1][-1], desktop)
            backend._finish_virtual_display_cleanup(process, 0)
            self.assertFalse(backend.virtualDisplayCleanupRunning)
            self.assertEqual(results, [(True, "Removed virtual displays")])
            process.start.reset_mock()
            backend.removeStagnantVirtualDisplays()
            backend.removeStagnantVirtualDisplays()
            process.start.assert_called_once()
            process.readAllStandardOutput.return_value = b'not a cleanup result'
            backend._finish_virtual_display_cleanup(process, 1)
            self.assertFalse(results[-1][0])
            self.assertFalse(backend.virtualDisplayCleanupRunning)
            streaming.return_value.streaming = True
            process.start.reset_mock()
            backend.removeStagnantVirtualDisplays()
            process.start.assert_not_called()
            self.assertIn("Stop streaming", results[-1][1])
            streaming.return_value.streaming = False



    @patch(
        "monitorize.desktop.backend.clear_sunshine_portal_restore_tokens",
        return_value=(2, []),
    )
    @patch("monitorize.desktop.backend.load_general_settings", return_value={"system_setup_decided": True})
    @patch("monitorize.desktop.backend.load_presets", return_value=[])
    @patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1")
    @patch("monitorize.desktop.backend.StreamingController")
    @patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False})
    def test_restore_token_clear_returns_actionable_toast_result(
        self, _status, _streaming, _ip, _presets, _settings, clear_tokens
    ):
        backend = MonitorizeBackend("hyprland")
        self.addCleanup(backend.network_timer.stop)
        backend.streaming.streaming = False

        self.assertEqual(
            backend.clearRestoreTokens(),
            {
                "success": True,
                "message": "Restore tokens cleared — select the displays again",
            },
        )
        clear_tokens.assert_called_once_with()

        backend.streaming.streaming = True
        self.assertEqual(
            backend.clearRestoreTokens(),
            {
                "success": False,
                "message": "Stop streaming before clearing restore tokens",
            },
        )

    def test_qml_has_a_non_dismissible_first_run_gate_and_manual_setup_entry(self):
        main = (ROOT / "linux/monitorize/qml/main.qml").read_text()
        setup = (ROOT / "linux/monitorize/qml/SystemSetupPage.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        streaming = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()
        settings_page = (ROOT / "linux/monitorize/qml/SettingsPage.qml").read_text()

        self.assertIn("backend.systemSetupPending", main)
        self.assertIn("closePolicy: Popup.NoAutoClose", main)
        self.assertIn("I know what I’m doing", main)
        self.assertIn("Run system setup again", settings_page)
        self.assertIn("visible: backend.systemSetupAvailable", settings_page)
        self.assertNotIn("visible: backend.canConfigureDisplay", settings_page)
        self.assertIn("Remove virtual display", settings_page)
        self.assertIn("backend.removeStagnantVirtualDisplays()", main)
        self.assertIn('root.stagnantCleanupSucceeded ? "#15803d" : "#b91c1c"', main)
        self.assertIn("Clear restore tokens", settings_page)
        self.assertIn("backend.clearRestoreTokens()", main)
        self.assertIn('enabled: !backend.isStreaming', settings_page)
        self.assertIn("backend.markSystemSetupDecided()", main)
        self.assertIn("property bool firstRun: false", setup)
        self.assertIn("if (statusSucceeded && page.firstRun)", setup)
        self.assertNotIn("Finish system setup", menu)
        self.assertIn("visible: backend.canConfigureDisplay", streaming)


if __name__ == "__main__":
    unittest.main()
