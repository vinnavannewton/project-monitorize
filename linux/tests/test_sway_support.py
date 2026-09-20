import os
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.display_controller import DisplayController
from monitorize.platform.utils import detect_desktop_environment


class SwaySupportTest(unittest.TestCase):
    @patch("monitorize.platform.display_controller.os.path.isfile", return_value=True)
    def test_flatpak_launches_host_nwg_displays_through_sway(self, _flatpak):
        controller = DisplayController("sway")
        controller._run_swaymsg = Mock(
            return_value=Mock(returncode=0, stdout='[{"success":true}]', stderr="")
        )
        with patch(
            "monitorize.platform.display_controller.subprocess.run",
            return_value=Mock(returncode=0, stdout="nwg-displays 0.4.4", stderr=""),
        ):
            error = controller.launch_host_display_settings()

        self.assertEqual(error, "")
        controller._run_swaymsg.assert_called_once_with("exec", "nwg-displays")

    def test_detects_sway_from_its_session_socket(self):
        with patch.dict(os.environ, {"SWAYSOCK": "/run/user/1000/sway-ipc.sock"}, clear=True):
            self.assertEqual(detect_desktop_environment(), "sway")

    @patch("monitorize.platform.display_controller.os.path.isfile", return_value=True)
    def test_flatpak_uses_host_swaymsg(self, _flatpak):
        self.assertEqual(
            DisplayController._swaymsg_command("-t", "get_outputs", "-r"),
            [
                "flatpak-spawn",
                "--host",
                "--directory=/",
                "swaymsg",
                "-t",
                "get_outputs",
                "-r",
            ],
        )

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
            ["swaymsg", "output", "HEADLESS-2", "unplug"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertIsNone(controller.additional_output)

    def test_removes_all_exact_stagnant_headless_outputs(self):
        controller = DisplayController("sway")
        controller.sway_outputs = Mock(return_value=[
            {"name": "HEADLESS-1"},
            {"name": "HEADLESS-old"},
            {"name": "HEADLESS-22"},
            {"name": "Monitorize-2"},
            {"name": "Monitorize-old"},
            {"name": "DP-1"},
        ])
        controller._run_swaymsg = Mock(
            return_value=Mock(returncode=0, stdout='[{"success":true}]', stderr="")
        )

        removed = controller.remove_stagnant_virtual_displays()

        self.assertEqual(removed, 3)
        self.assertEqual(
            [call.args for call in controller._run_swaymsg.call_args_list],
            [
                ("output", "HEADLESS-1", "unplug"),
                ("output", "HEADLESS-22", "unplug"),
                ("output", "Monitorize-2", "unplug"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
