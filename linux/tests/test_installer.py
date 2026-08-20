import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SunshineOnlyPackagingTest(unittest.TestCase):
    def test_installer_builds_only_project_local_sunshine_backend(self):
        script = (ROOT / "linux/scripts/install.sh").read_text()
        requirements = (ROOT / "linux/requirements.txt").read_text()
        self.assertIn("external/sunshine", script)
        self.assertIn("MONITORIZE_BUILD_JOBS", script)
        self.assertIn("Jinja2", requirements)
        self.assertNotIn("monitorize-rtp-sender", script)
        self.assertNotIn("cmake --install", script)
        self.assertNotIn("/usr/local", script)
        self.assertNotIn("cryptography", script)
        self.assertNotIn("zeroconf", script)
        self.assertNotIn("evdev", script)

    def test_nix_closure_has_no_monitorize_gstreamer_or_adb_runtime(self):
        package = (ROOT / "nix/package.nix").read_text()
        self.assertIn("monitorizeSunshine", package)
        self.assertNotIn("gst_all_1", package)
        self.assertNotIn("android-tools", package)
        self.assertNotIn("monitorize-rtp-sender", package)

    def test_qml_exposes_only_sunshine_display_flow(self):
        qml = "\n".join(
            path.read_text()
            for path in (ROOT / "linux/monitorize/qml").glob("*.qml")
        )
        self.assertIn("Create a Virtual Display", qml)
        for legacy in ("USB Mode", "Receiver Mode", 'model: ["Monitorize", "Sunshine"]'):
            self.assertNotIn(legacy, qml)

    def test_successful_pairing_closes_the_pin_popup(self):
        qml = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()
        self.assertIn("interval: 2000", qml)
        self.assertIn('if (result["success"]) pinSuccessCloseTimer.restart()', qml)
        self.assertIn("onTriggered: pinPopup.close()", qml)

    def test_retired_runtime_modules_are_absent(self):
        package = ROOT / "linux/monitorize"
        retired = (
            "desktop/receiver_controller.py",
            "desktop/usb_controller.py",
            "desktop/discovery_service.py",
            "input_bridge",
            "streaming/gst_session.py",
            "streaming/pipeline_builder.py",
            "streaming/audio_sender.py",
        )
        for relative_path in retired:
            self.assertFalse((package / relative_path).exists(), relative_path)


if __name__ == "__main__":
    unittest.main()
