import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from monitorize.config import settings
from monitorize.desktop.backend import MonitorizeBackend


ROOT = Path(__file__).resolve().parents[2]


class FirstRunSetupTest(unittest.TestCase):
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

    def test_qml_has_a_non_dismissible_first_run_gate_and_manual_setup_entry(self):
        main = (ROOT / "linux/monitorize/qml/main.qml").read_text()
        setup = (ROOT / "linux/monitorize/qml/SystemSetupPage.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        streaming = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()

        self.assertIn("backend.systemSetupPending", main)
        self.assertIn("closePolicy: Popup.NoAutoClose", main)
        self.assertIn("I know what I’m doing", main)
        self.assertIn("Run system setup again", main)
        self.assertIn("visible: backend.systemSetupAvailable", main)
        self.assertIn("backend.markSystemSetupDecided()", main)
        self.assertIn("property bool firstRun: false", setup)
        self.assertIn("if (statusSucceeded && page.firstRun)", setup)
        self.assertNotIn("Finish system setup", menu)
        self.assertIn("visible: backend.canConfigureDisplay", streaming)


if __name__ == "__main__":
    unittest.main()
