import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class UdpInstallerTest(unittest.TestCase):
    def test_udp_uninstall_leaves_normal_desktop_files_alone(self):
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
            normal = applications / "monitorize.desktop"
            normal_helper = applications / "monitorize-kde-virtual-output.desktop"
            normal_icon = icons / "monitorize.png"
            for path in (normal, normal_helper, normal_icon):
                path.write_text("normal")
            (applications / "monitorize-udp.desktop").write_text("udp")
            (applications / "monitorize-udp-kde-virtual-output.desktop").write_text("udp")
            (icons / "monitorize-udp.png").write_text("udp")

            env = {**os.environ, "HOME": str(root / "home")}
            subprocess.run(["bash", str(script), "remove"], check=True, env=env)

            for path in (normal, normal_helper, normal_icon):
                self.assertEqual(path.read_text(), "normal")
            self.assertFalse((applications / "monitorize-udp.desktop").exists())
            self.assertFalse((applications / "monitorize-udp-kde-virtual-output.desktop").exists())
            self.assertFalse((icons / "monitorize-udp.png").exists())

    def test_udp_entry_identity_and_checkout_path_are_distinct(self):
        script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text()
        self.assertIn('APP_NAME="Monitorize UDP"', script)
        self.assertIn('APP_ID="monitorize-udp"', script)
        self.assertIn('HELPER_DESKTOP_FILE="${APP_ID}-kde-virtual-output.desktop"', script)
        self.assertIn('Path=${PROJECT_DIR}', script)
        self.assertNotIn('StartupWMClass=monitorize', script)


if __name__ == "__main__":
    unittest.main()
