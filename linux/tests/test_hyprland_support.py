import json
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.display_controller import DisplayController


class HyprlandSupportTest(unittest.TestCase):
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_uses_host_hyprctl(self, _flatpak):
        self.assertEqual(
            DisplayController._hyprctl_command("monitors", "all", "-j"),
            [
                "flatpak-spawn",
                "--host",
                "hyprctl",
                "monitors",
                "all",
                "-j",
            ],
        )

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=False,
    )
    def test_native_install_uses_direct_hyprctl(self, _flatpak):
        self.assertEqual(
            DisplayController._hyprctl_command("monitors", "all", "-j"),
            ["hyprctl", "monitors", "all", "-j"],
        )

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_lists_headless_monitors_through_host(self, _flatpak, run):
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps([{"name": "HEADLESS-2"}, {"name": "eDP-1"}]),
            stderr="",
        )

        monitors = DisplayController("hyprland").headless_monitors()

        self.assertEqual(monitors, ["HEADLESS-2"])
        run.assert_called_once_with(
            [
                "flatpak-spawn",
                "--host",
                "hyprctl",
                "monitors",
                "all",
                "-j",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_creates_configures_and_verifies_with_host_hyprctl(
        self, _flatpak, run
    ):
        run.return_value = Mock(returncode=0, stdout="ok", stderr="")
        controller = DisplayController("hyprland")
        controller.headless_monitors = Mock(
            side_effect=[[], ["HEADLESS-2"]]
        )
        controller.wait_for_headless_ready = Mock(return_value=True)

        output, error = controller.prepare_hyprland(2560, 1440, 120)

        self.assertEqual((output, error), ("HEADLESS-2", ""))
        self.assertEqual(controller.created_output, "HEADLESS-2")
        controller.wait_for_headless_ready.assert_called_once_with(
            "HEADLESS-2", 2560, 1440, fps=120
        )
        commands = [item.args[0] for item in run.call_args_list]
        self.assertEqual(
            commands,
            [
                [
                    "flatpak-spawn", "--host", "hyprctl",
                    "output", "create", "headless",
                ],
                [
                    "flatpak-spawn", "--host", "hyprctl", "keyword",
                    "monitor", "HEADLESS-2,2560x1440@120,auto,1",
                ],
                [
                    "flatpak-spawn", "--host", "hyprctl", "eval",
                    "hl.monitor({ output = 'HEADLESS-2', mode = "
                    "'2560x1440@120', position = 'auto', scale = 1.0 }})",
                ],
            ],
        )

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_removes_headless_output_through_host(self, _flatpak, run):
        run.return_value = Mock(returncode=0, stdout="ok", stderr="")
        controller = DisplayController("hyprland")
        controller.created_output = "HEADLESS-2"

        controller.remove_hyprland_output()

        run.assert_called_once_with(
            [
                "flatpak-spawn", "--host", "hyprctl",
                "output", "remove", "HEADLESS-2",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertIsNone(controller.created_output)

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_missing_flatpak_spawn_returns_actionable_error(self, _flatpak, run):
        run.side_effect = FileNotFoundError("flatpak-spawn not found")
        controller = DisplayController("hyprland")
        controller.headless_monitors = Mock(return_value=[])

        output, error = controller.prepare_hyprland(1920, 1080, 60)

        self.assertEqual(output, "")
        self.assertIn("launch host hyprctl", error)
        self.assertIn("flatpak-spawn not found", error)


if __name__ == "__main__":
    unittest.main()
