import os
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.display_controller import DisplayController
from monitorize.platform.utils import detect_desktop_environment


class SwaySupportTest(unittest.TestCase):
    def test_detects_sway_from_its_session_socket(self):
        with patch.dict(os.environ, {"SWAYSOCK": "/run/user/1000/sway-ipc.sock"}, clear=True):
            self.assertEqual(detect_desktop_environment(), "sway")

    @patch.object(DisplayController, "_wait_for_sway_output_ready", return_value=True)
    @patch.object(
        DisplayController,
        "_wait_for_new_sway_output",
        return_value=("HEADLESS-1", [{"active": True, "rect": {"x": 0, "width": 1920}}]),
    )
    @patch.object(DisplayController, "sway_outputs", return_value=[])
    @patch.object(DisplayController, "sway_version_supported", return_value=True)
    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_creates_configures_and_tracks_sway_output(
        self, run, _version, _outputs, _created, _ready
    ):
        run.side_effect = [Mock(returncode=0), Mock(returncode=0)]
        controller = DisplayController("sway")

        self.assertEqual(controller.prepare_sway(1920, 1080, 60), ("HEADLESS-1", ""))
        self.assertEqual(controller.created_output, "HEADLESS-1")
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["swaymsg", "output", "HEADLESS-1", "mode", "--custom", "1920x1080@60Hz",
             "pos", "1920", "0", "scale", "1"],
        )

    @patch.object(DisplayController, "sway_version_supported", return_value=False)
    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_rejects_sway_without_virtual_output_cleanup(self, run, _version):
        output, error = DisplayController("sway").prepare_sway(1920, 1080, 60)
        self.assertEqual(output, "")
        self.assertIn("1.8", error)
        run.assert_not_called()

    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_removes_sway_output_with_unplug(self, run):
        controller = DisplayController("sway")
        controller.additional_output = "HEADLESS-2"
        controller.remove_sway_output("additional")
        run.assert_called_once_with(
            ["swaymsg", "output", "HEADLESS-2", "unplug"], capture_output=True
        )
        self.assertIsNone(controller.additional_output)


if __name__ == "__main__":
    unittest.main()
