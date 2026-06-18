import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QSettings

from linux.gui import settings


class SettingsTest(unittest.TestCase):
    def test_migrates_capitalized_general_group(self):
        with tempfile.TemporaryDirectory() as directory:
            settings.CONFIG_DIR = directory
            settings.CONFIG_FILE = str(Path(directory) / "settings.ini")

            old = QSettings(settings.CONFIG_FILE, QSettings.Format.IniFormat)
            old.setValue("General/enable_touch", False)
            old.setValue("General/enable_stylus_features", True)
            old.sync()

            self.assertEqual(
                settings.load_general_settings(),
                {
                    "minimize_to_tray": False,
                    "enable_touch": False,
                    "enable_stylus_features": True,
                },
            )
            migrated = QSettings(settings.CONFIG_FILE, QSettings.Format.IniFormat)
            self.assertFalse(any(key.startswith("General/") for key in migrated.allKeys()))


if __name__ == "__main__":
    unittest.main()
