import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


class SunshineOnlyPackagingTest(unittest.TestCase):
    def test_installer_builds_only_project_local_sunshine_backend(self):
        script = (ROOT / "linux/scripts/install.sh").read_text()
        requirements = (ROOT / "linux/requirements.txt").read_text()
        self.assertIn("external/sunshine", script)
        self.assertIn("MONITORIZE_BUILD_JOBS", script)
        self.assertIn("check_sunshine_node_modules_permissions", script)
        self.assertIn("Sunshine's generated npm cache is not writable", script)
        self.assertIn("Then rerun this installer without sudo.", script)
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

    def test_choice_chips_and_preset_menu_use_the_requested_layout(self):
        chips = (ROOT / "linux/monitorize/qml/ChoiceChips.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        self.assertIn("columns: 3", chips)
        self.assertIn('text: "⋮"', menu)
        self.assertIn('text: "Rename"', menu)
        self.assertIn('text: "Remove"', menu)
        self.assertIn("backend.renamePreset", menu)
        self.assertNotIn('text: "×"', menu)

    def test_successful_pairing_closes_the_pin_popup(self):
        qml = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()
        self.assertIn("interval: 2000", qml)
        self.assertIn('if (result["success"]) pinSuccessCloseTimer.restart()', qml)
        self.assertIn("onTriggered: pinPopup.close()", qml)

    def test_choice_chips_and_start_card_fit_their_containers(self):
        chips = (ROOT / "linux/monitorize/qml/ChoiceChips.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        self.assertIn("GridLayout", chips)
        self.assertIn("columns: 3", chips)
        self.assertIn("rowSpacing: 8", chips)
        self.assertIn("Layout.preferredWidth: Math.min(440, page.width - 40)", menu)

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

    def test_fedora_rpm_bundles_the_private_sunshine_backend(self):
        spec = (ROOT / "packaging/rpm/monitorize.spec").read_text()
        builder = (ROOT / "packaging/rpm/build.sh").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()

        project_version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
        spec_version = re.search(r'^Version:\s+(\S+)', spec, re.MULTILINE).group(1)
        self.assertEqual(project_version, spec_version)

        self.assertIn("fedora:${FEDORA_VERSION}", builder)
        self.assertIn("git submodule status --recursive", builder)
        self.assertIn("rpmbuild -ba", builder)
        self.assertIn("Smoke-testing", builder)
        self.assertLess(
            builder.index("test ! -e /root/.config/monitorize"),
            builder.index("from monitorize.desktop import main_window"),
        )
        self.assertIn("%{_libexecdir}/monitorize/sunshine", spec)
        self.assertIn("%dir %{_datadir}/monitorize", spec)
        self.assertIn("%dir %{_datadir}/monitorize/sunshine", spec)
        self.assertIn("MONITORIZE_SUNSHINE_BIN", spec)
        self.assertIn("MONITORIZE_SUNSHINE_ASSETS_DIR", spec)
        self.assertIn("sunshine_ffmpeg_sha256", spec)
        self.assertIn("BuildRequires:  boost-devel >= 1.89.0", spec)
        self.assertIn("BuildRequires:  firewalld-filesystem", spec)
        self.assertIn("Requires(post): kmod", spec)
        self.assertIn("printf 'uinput\\n'", spec)
        self.assertIn("%{_modulesloaddir}/monitorize.conf", spec)
        self.assertIn("/usr/sbin/modprobe uinput", spec)
        self.assertNotIn("Source3:", spec)
        self.assertIn("%sysusers_create_compat", spec)
        self.assertNotIn("cmake --install", spec)
        self.assertNotRegex(spec, r"install .*%\{_bindir\}/sunshine")
        self.assertNotRegex(spec, r"install .*?/usr/local")
        for retired_dependency in ("gstreamer", "android-tools", "zeroconf", "python3-evdev"):
            self.assertNotIn(retired_dependency, spec.lower())

    def test_fedora_uinput_access_uses_the_dedicated_group(self):
        rules = (ROOT / "packaging/fedora/70-monitorize-uinput.rules").read_text()
        sysusers = (ROOT / "packaging/fedora/monitorize.sysusers").read_text()
        self.assertIn('GROUP="monitorize-input"', rules)
        self.assertEqual(sysusers.strip(), "g monitorize-input - -")
        self.assertNotIn("Monitorize-Touch", rules)


if __name__ == "__main__":
    unittest.main()
