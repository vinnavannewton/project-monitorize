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


if __name__ == "__main__":
    unittest.main()
