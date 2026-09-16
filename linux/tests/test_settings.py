import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QSettings

from monitorize.config import settings


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_file = os.path.join(self.tmp.name, "settings.ini")
        self.patches = (
            patch.object(settings, "CONFIG_DIR", self.tmp.name),
            patch.object(settings, "CONFIG_FILE", self.config_file),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_display_settings_round_trip(self):
        settings.save_display_settings(
            resolution="2560x1440",
            fps="90",
            display_type="Extend",
            virtual_display_creator="vkms",
            sunshine_encoder="NVIDIA",
            sunshine_gpu="0000:03:00.0",
            sunshine_codec="AV1",
            streaming_customized=True,
            sunshine_native_pen_touch=False,
            enable_audio=True,
        )
        saved = settings.load_display_settings()
        self.assertEqual(saved["resolution"], "2560x1440")
        self.assertEqual(saved["virtual_display_creator"], "vkms")
        self.assertEqual(saved["sunshine_codec"], "AV1")
        self.assertEqual(saved["sunshine_gpu"], "0000:03:00.0")
        self.assertTrue(saved["streaming_customized"])
        self.assertFalse(saved["sunshine_native_pen_touch"])
        self.assertTrue(saved["enable_audio"])

    def test_second_display_mode_is_persisted_independently(self):
        settings.save_display_settings(resolution="2560x1600", fps="120")
        settings.save_second_display_settings(
            enabled=True,
            resolution="1920x1080",
            fps="75",
        )

        primary = settings.load_display_settings()
        second = settings.load_second_display_settings()
        self.assertEqual((primary["resolution"], primary["fps"]), ("2560x1600", "120"))
        self.assertTrue(second["enabled"])
        self.assertEqual((second["resolution"], second["fps"]), ("1920x1080", "75"))

        settings.save_second_display_settings(enabled=False)
        self.assertFalse(settings.load_second_display_settings()["enabled"])

    def test_v1_wifi_preset_migrates_and_usb_preset_is_dropped(self):
        store = QSettings(self.config_file, QSettings.Format.IniFormat)
        store.setValue(
            "presets/items",
            json.dumps(
                [
                    {
                        "version": 1,
                        "name": "Wi-Fi",
                        "mode": "wifi",
                        "primary": {
                            "resolution": "1920x1080",
                            "fps": "60",
                            "display_type": "Extend",
                        },
                        "third": {"enabled": False},
                    },
                    {
                        "version": 1,
                        "name": "USB",
                        "mode": "usb",
                        "primary": {},
                        "third": {"enabled": False},
                    },
                ]
            ),
        )
        store.sync()
        presets = settings.load_presets()
        self.assertEqual([preset["name"] for preset in presets], ["Wi-Fi"])
        self.assertEqual(presets[0]["version"], 2)
        self.assertIn("sunshine_encoder", presets[0]["primary"])
        self.assertNotIn("mode", presets[0])

    def test_gpu_selection_round_trips_through_presets_and_rejects_bad_ids(self):
        settings.save_presets([{
            "version": 2,
            "name": "Hybrid",
            "primary": {
                "resolution": "1920x1080",
                "fps": "60",
                "display_type": "Extend",
                "virtual_display_creator": "vkms",
                "sunshine_encoder": "VA-API",
                "sunshine_gpu": "0000:03:00.0",
            },
            "second": {"enabled": False},
        }])
        self.assertEqual(
            settings.load_presets()[0]["primary"]["sunshine_gpu"],
            "0000:03:00.0",
        )
        self.assertEqual(
            settings.load_presets()[0]["primary"]["virtual_display_creator"],
            "vkms",
        )

        settings.save_display_settings(
            resolution="1920x1080",
            sunshine_encoder="VA-API",
            sunshine_gpu="../../dev/dri/renderD128",
        )
        self.assertEqual(settings.load_display_settings()["sunshine_gpu"], "")

    def test_custom_resolution_round_trip(self):
        settings.save_display_settings(
            resolution="Custom...",
            custom_w="2340",
            custom_h="1080",
            fps="60",
            virtual_display_creator="vkms",
        )
        saved = settings.load_display_settings()
        self.assertEqual(saved["resolution"], "Custom...")
        self.assertEqual(saved["custom_w"], "2340")
        self.assertEqual(saved["custom_h"], "1080")


if __name__ == "__main__":
    unittest.main()
