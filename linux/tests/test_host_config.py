import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitorize.config import host_config, settings
from monitorize.platform.sunshine_service import get_sunshine_config_path


class HostConfigTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "CONFIG_DIR", directory))
        self.enterContext(patch.object(settings, "CONFIG_FILE", str(Path(directory) / "settings.ini")))
        self.enterContext(patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}))
        self.boot = self.enterContext(patch.object(host_config, "_boot_state",
            return_value={"enabled": False, "create_login_virtual_display": False}))
        self.path = host_config.initialize()

    def read(self):
        return json.loads(self.path.read_text())

    def write(self, document):
        self.path.write_text(json.dumps(document))

    def test_migrates_existing_settings_and_refreshes_unedited_file(self):
        settings.save_general_settings(theme_mode="dark")
        self.assertEqual(host_config.initialize(), self.path)
        self.assertEqual(self.read()["general"]["theme_mode"], "dark")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_reload_saves_and_validates_settings_through_the_gui_store(self):
        document = self.read()
        document["general"]["theme_mode"] = "dark"
        document["primary"]["resolution"] = "2560x1440"
        document["primary"]["fps"] = 75
        self.write(document)

        result = host_config.reload_config()

        self.assertTrue(result["streaming_changed"])
        self.assertEqual(result["saved_revision"], host_config.saved_revision())
        self.assertEqual(settings.load_general_settings()["theme_mode"], "dark")
        self.assertEqual(settings.load_display_settings()["resolution"], "2560x1440")
        self.assertEqual(settings.load_display_settings()["fps"], "75")
        self.assertFalse(host_config.reload_config()["changed"])

    def test_invalid_edit_preserves_last_accepted_settings(self):
        old_revision = host_config.saved_revision()
        document = self.read()
        document["general"]["theme_mode"] = "dark"
        document["primary"]["fps"] = True
        self.write(document)
        with self.assertRaisesRegex(ValueError, r"primary\.fps"):
            host_config.reload_config()
        self.assertEqual(host_config.saved_revision(), old_revision)
        self.assertEqual(settings.load_general_settings()["theme_mode"], "system")
        host_config.initialize()
        self.assertEqual(self.read(), document)

    def test_duplicate_key_is_rejected(self):
        self.path.write_text('{"schema_version": 1, "schema_version": 1}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            host_config.reload_config()

    def test_unrelated_gui_edit_is_preserved_during_reload(self):
        document = self.read()
        document["primary"]["resolution"] = "2560x1440"
        self.write(document)
        settings.save_general_settings(theme_mode="dark")

        host_config.reload_config()

        self.assertEqual(settings.load_general_settings()["theme_mode"], "dark")
        self.assertEqual(settings.load_display_settings()["resolution"], "2560x1440")

    def test_concurrent_same_field_edit_is_detected(self):
        document = self.read()
        document["general"]["theme_mode"] = "light"
        self.write(document)
        settings.save_general_settings(theme_mode="dark")

        with self.assertRaisesRegex(ValueError, r"general\.theme_mode also changed"):
            host_config.reload_config()
        self.assertEqual(settings.load_general_settings()["theme_mode"], "dark")

    def test_boot_settings_require_an_authorized_command(self):
        document = self.read()
        document["boot"]["enabled"] = True
        self.write(document)
        with self.assertRaisesRegex(ValueError, "authorized boot commands"):
            host_config.reload_config()

    def test_desktop_startup_updates_through_existing_adapter(self):
        document = self.read()
        document["startup"]["desktop"] = True
        self.write(document)
        host_config.reload_config()
        self.assertTrue(host_config.autostart.is_enabled())
        document["startup"]["desktop"] = False
        self.write(document)
        host_config.reload_config()
        self.assertFalse(host_config.autostart.is_enabled())

    def test_advanced_options_are_staged_without_restarting_sunshine(self):
        path = Path(get_sunshine_config_path(2))
        path.parent.mkdir(parents=True)
        path.write_text("# user profile\nport = 47989\nfile_state = identity.json\nmax_bitrate = 0\n")
        document = self.read()
        document["sunshine"]["2"]["max_bitrate"] = 40000
        self.write(document)
        revision = host_config.streaming_revision()

        result = host_config.reload_config()

        self.assertTrue(result["streaming_changed"])
        self.assertNotEqual(result["streaming_revision"], revision)
        self.assertIn("file_state = identity.json", path.read_text())
        self.assertIn("port = 47989", path.read_text())
        self.assertIn("max_bitrate = 40000", path.read_text())
        self.assertFalse(host_config.reload_config()["streaming_changed"])

    def test_sunshine_settings_reject_newlines_and_restore_on_save_failure(self):
        document = self.read()
        document["sunshine"]["2"]["audio_sink"] = "sink\nport = 0"
        self.write(document)
        with self.assertRaisesRegex(ValueError, r"sunshine\.2\.audio_sink"):
            host_config.reload_config()
        path = Path(get_sunshine_config_path(2))
        self.assertFalse(path.exists())

        document["sunshine"]["2"]["audio_sink"] = "sink"
        self.write(document)
        with patch.object(host_config, "_publish", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                host_config.reload_config()
        self.assertFalse(path.exists())

    def test_appearance_does_not_change_streaming_revision(self):
        revision = host_config.streaming_revision()
        document = self.read()
        document["general"]["theme_mode"] = "dark"
        self.write(document)
        result = host_config.reload_config()
        self.assertFalse(result["streaming_changed"])
        self.assertEqual(result["streaming_revision"], revision)

    def test_fifo_profile_is_rejected_without_blocking(self):
        import os

        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ValueError, "regular file"):
            host_config.reload_config()

    def test_gui_write_after_reload_snapshot_preserves_an_unrelated_preference(self):
        document = self.read()
        document["primary"]["resolution"] = "2560x1440"
        self.write(document)
        publish = host_config._publish

        def save_after_gui_update(store, values):
            settings.save_general_settings(theme_mode="dark")
            publish(store, values)

        with patch.object(host_config, "_publish", side_effect=save_after_gui_update):
            host_config.reload_config()

        self.assertEqual(settings.load_general_settings()["theme_mode"], "dark")
        self.assertEqual(settings.load_display_settings()["resolution"], "2560x1440")

    def test_accepted_editor_spelling_can_refresh_after_a_later_gui_save(self):
        document = self.read()
        document["primary"]["resolution"] = "2560X1440"
        document["primary"]["fps"] = 75
        self.write(document)
        host_config.reload_config()
        self.assertFalse(host_config.reload_config()["changed"])

        settings.save_general_settings(theme_mode="dark")
        host_config.initialize()

        self.assertEqual(self.read()["general"]["theme_mode"], "dark")
        self.assertEqual(self.read()["primary"]["resolution"], "2560x1440")

    def test_sunshine_accepts_existing_log_names_and_fractional_rates(self):
        path = Path(get_sunshine_config_path(2))
        path.parent.mkdir(parents=True)
        path.write_text("min_log_level = warning\nminimum_fps_target = 59.94\n")
        host_config.initialize()
        document = self.read()
        document["sunshine"]["2"]["minimum_fps_target"] = 29.97
        self.write(document)

        host_config.reload_config()

        self.assertIn("min_log_level = warning", path.read_text())
        self.assertIn("minimum_fps_target = 29.97", path.read_text())
