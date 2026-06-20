import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gui import settings
from gui.main_window import _disable_sway_output, _prepare_sway_output, _sway_outputs

old_argv = sys.argv
sys.argv = [sys.argv[0]]
import touch_daemon
sys.argv = old_argv


class SwaySupportTest(unittest.TestCase):
    def test_reads_sway_outputs(self):
        result = Mock(returncode=0, stdout=json.dumps([{"name": "HEADLESS-1"}]))
        with patch("gui.main_window.subprocess.run", return_value=result):
            self.assertEqual(_sway_outputs()[0]["name"], "HEADLESS-1")

    def test_persists_sway_output(self):
        with tempfile.TemporaryDirectory() as directory:
            settings.CONFIG_DIR = directory
            settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
            settings.save_sway_output("HEADLESS-2")
            self.assertEqual(settings.load_sway_output(), "HEADLESS-2")

    def test_creates_and_configures_sway_output(self):
        before = [{"name": "eDP-1", "active": True, "rect": {"x": 0, "width": 1920}}]
        after = before + [{"name": "HEADLESS-1", "active": True, "rect": {}}]
        ok = Mock(returncode=0, stdout="", stderr="")
        with (
            patch(
                "gui.main_window._sway_outputs",
                side_effect=[before, after],
            ),
            patch("gui.main_window.subprocess.run", return_value=ok) as run,
        ):
            output, error = _prepare_sway_output(2560, 1600, 60)
        self.assertEqual((output, error), ("HEADLESS-1", ""))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["swaymsg", "create_output"], commands)
        self.assertIn(
            ["swaymsg", "output", "HEADLESS-1", "custom_mode", "2560x1600@60Hz"],
            commands,
        )
        self.assertIn(
            ["swaymsg", "output", "HEADLESS-1", "pos", "1920", "0"],
            commands,
        )

    def test_reuses_saved_sway_output(self):
        outputs = [{"name": "HEADLESS-2", "active": False, "rect": {}}]
        ok = Mock(returncode=0, stdout="", stderr="")
        with (
            patch("gui.main_window._sway_outputs", side_effect=[outputs, outputs]),
            patch("gui.main_window.subprocess.run", return_value=ok) as run,
        ):
            output, error = _prepare_sway_output(1920, 1080, 60, "HEADLESS-2")
        self.assertEqual((output, error), ("HEADLESS-2", ""))
        self.assertNotIn(
            ["swaymsg", "create_output"],
            [call.args[0] for call in run.call_args_list],
        )

    def test_disables_sway_output(self):
        with patch(
            "gui.main_window.subprocess.run",
            return_value=Mock(returncode=0),
        ) as run:
            self.assertTrue(_disable_sway_output("HEADLESS-3"))
        run.assert_called_once_with(
            ["swaymsg", "output", "HEADLESS-3", "disable"],
            capture_output=True,
        )

    def test_maps_sway_input_identifier(self):
        touch_daemon._DETECTED_DE = "sway"
        inputs = Mock(
            returncode=0,
            stdout=json.dumps([
                {"name": "Monitorize-Touch", "identifier": "1:2:Monitorize_Touch"}
            ]),
        )
        mapped = Mock(returncode=0, stdout="", stderr="")
        with (
            patch.dict(touch_daemon.os.environ, {"MONITORIZE_OUTPUT": "HEADLESS-1"}),
            patch("subprocess.run", side_effect=[inputs, mapped]) as run,
        ):
            self.assertTrue(touch_daemon._map_sway_uinput_devices(["monitorize-touch"]))
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "swaymsg", "input", "1:2:Monitorize_Touch",
                "map_to_output", "HEADLESS-1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
