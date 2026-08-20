import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerTest(unittest.TestCase):
    def test_uninstall_removes_normal_desktop_files(self):
        source = Path(__file__).parents[1] / "scripts" / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "project" / "linux" / "scripts" / "install.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(source, script)
            (root / "project" / "linux" / "venv").mkdir(parents=True)
            applications = root / "home" / ".local/share/applications"
            icons = root / "home" / ".local/share/icons/hicolor/192x192/apps"
            applications.mkdir(parents=True)
            icons.mkdir(parents=True)
            for path in (
                applications / "monitorize.desktop",
                applications / "monitorize-kde-virtual-output.desktop",
                icons / "monitorize.png",
                applications / "monitorize-udp.desktop",
                applications / "monitorize-udp-kde-virtual-output.desktop",
                icons / "monitorize-udp.png",
            ):
                path.write_text("installed")

            subprocess.run(
                ["bash", str(script), "remove"], check=True,
                env={**os.environ, "HOME": str(root / "home")},
            )

            self.assertFalse((applications / "monitorize.desktop").exists())
            self.assertFalse((applications / "monitorize-kde-virtual-output.desktop").exists())
            self.assertFalse((icons / "monitorize.png").exists())
            self.assertFalse((applications / "monitorize-udp.desktop").exists())
            self.assertFalse((applications / "monitorize-udp-kde-virtual-output.desktop").exists())
            self.assertFalse((icons / "monitorize-udp.png").exists())

    def test_entry_uses_normal_monitorize_identity(self):
        script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text()
        self.assertIn('APP_NAME="Monitorize"', script)
        self.assertIn('APP_ID="monitorize"', script)
        self.assertIn('HELPER_DESKTOP_FILE="${APP_ID}-kde-virtual-output.desktop"', script)
        self.assertIn('Path=${PROJECT_DIR}', script)

    def test_sunshine_install_is_required_and_project_local(self):
        script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text()
        self.assertIn("MONITORIZE_BUILD_JOBS", script)
        self.assertIn('git -C "${REPOSITORY_DIR}" submodule update --init --recursive external/sunshine', script)
        self.assertIn('-DCMAKE_CXX_COMPILER="${SUNSHINE_CXX}"', script)
        self.assertIn('SUNSHINE_VENV_ASSETS="${VENV_DIR}/share/monitorize/sunshine/assets"', script)
        self.assertIn('cp -aL "${SUNSHINE_BUILD_ASSETS}/." "${SUNSHINE_VENV_ASSETS}/"', script)
        self.assertIn("origin_web_ui_allowed = lan", script)
        self.assertIn("Post-install validation failed", script)
        self.assertNotIn("cmake --install", script)
        self.assertNotIn("/usr/local", script)
        self.assertNotIn("origin_pin_allowed = pc,lan,wan", script)

    def test_installer_declares_supported_toolchain_checks(self):
        script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text()
        self.assertIn('version_at_least "${PYTHON_VERSION}" "3.11"', script)
        self.assertIn('version_at_least "${CMAKE_VERSION}" "3.26"', script)
        self.assertIn('"gcc-14:g++-14" "gcc:g++" "clang:clang++"', script)
        self.assertIn("require_command npm", script)
        self.assertIn("Sunshine compilation failed", script)

    def test_nix_package_bundles_the_monitorize_sunshine_fork(self):
        root = Path(__file__).parents[2]
        package = (root / "nix" / "package.nix").read_text()
        flake = (root / "flake.nix").read_text()
        self.assertIn("sunshineSource = lib.cleanSource ../external/sunshine", package)
        self.assertIn("monitorizeSunshine = sunshine.overrideAttrs", package)
        self.assertIn('MONITORIZE_SUNSHINE_BIN "${monitorizeSunshine}/bin/sunshine"', package)
        self.assertIn('MONITORIZE_SUNSHINE_ASSETS_DIR "${monitorizeSunshine}/assets"', package)
        self.assertIn("self.submodules = true", flake)
        self.assertNotIn("7110", flake)
        self.assertNotIn("48989", flake)


if __name__ == "__main__":
    unittest.main()
