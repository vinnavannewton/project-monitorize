import json
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.display_controller import DisplayController


class HyprlandSupportTest(unittest.TestCase):
    def test_active_output_modes_include_headless_and_ignore_disabled(self):
        controller = DisplayController("hyprland")
        controller._monitor_json = Mock(
            return_value=[
                {
                    "name": "eDP-1",
                    "width": 2560,
                    "height": 1600,
                    "refreshRate": 120,
                    "disabled": False,
                },
                {
                    "name": "HEADLESS-1",
                    "width": 1920,
                    "height": 1080,
                    "refreshRate": 60,
                    "disabled": False,
                },
                {
                    "name": "DP-2",
                    "width": 1920,
                    "height": 1080,
                    "disabled": True,
                },
            ]
        )

        modes = controller.active_output_modes()

        self.assertEqual(modes["eDP-1"]["width"], 2560)
        self.assertEqual(modes["HEADLESS-1"]["height"], 1080)
        self.assertNotIn("DP-2", modes)

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_flatpak_launches_host_nwg_displays_through_hyprland(
        self, run, _flatpak
    ):
        run.return_value = Mock(
            returncode=0, stdout="nwg-displays version 0.4.4", stderr=""
        )
        controller = DisplayController("hyprland")
        controller._verify_hyprland_ipc = Mock(return_value="")
        controller._run_hyprctl = Mock(
            return_value=Mock(returncode=0, stdout="ok", stderr="")
        )

        error = controller.launch_host_display_settings()

        self.assertEqual(error, "")
        run.assert_called_once_with(
            [
                "flatpak-spawn", "--host", "--directory=/",
                "nwg-displays", "--version",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        controller._run_hyprctl.assert_called_once_with(
            "dispatch", 'hl.dsp.exec_cmd("nwg-displays")'
        )

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_nwg_displays_launch_falls_back_for_legacy_hyprland(
        self, run, _flatpak
    ):
        run.return_value = Mock(returncode=0, stdout="nwg-displays 0.4.4", stderr="")
        controller = DisplayController("hyprland")
        controller._verify_hyprland_ipc = Mock(return_value="")
        controller._run_hyprctl = Mock(side_effect=[
            Mock(returncode=1, stdout="", stderr="unknown Lua dispatcher"),
            Mock(returncode=0, stdout="ok", stderr=""),
        ])

        error = controller.launch_host_display_settings()

        self.assertEqual(error, "")
        self.assertEqual(
            [call.args for call in controller._run_hyprctl.call_args_list],
            [
                ("dispatch", 'hl.dsp.exec_cmd("nwg-displays")'),
                ("dispatch", "exec", "nwg-displays"),
            ],
        )

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    @patch("monitorize.platform.display_controller.subprocess.run")
    def test_flatpak_reports_missing_host_nwg_displays(self, run, _flatpak):
        run.return_value = Mock(returncode=1, stdout="", stderr="not found")

        error = DisplayController("hyprland").launch_host_display_settings()

        self.assertEqual(error, "nwg-displays is not installed on the host")

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_uses_host_hyprctl(self, _flatpak):
        self.assertEqual(
            DisplayController._hyprctl_command("-j", "monitors", "all"),
            [
                "flatpak-spawn",
                "--host",
                "--directory=/",
                "hyprctl",
                "-j",
                "monitors",
                "all",
            ],
        )

    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=False,
    )
    def test_native_install_uses_direct_hyprctl(self, _flatpak):
        self.assertEqual(
            DisplayController._hyprctl_command("-j", "monitors", "all"),
            ["hyprctl", "-j", "monitors", "all"],
        )

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_flatpak_lists_headless_monitors_through_host(self, _flatpak, run):
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps([
                {"name": "HEADLESS-2"},
                {"name": "Monitorize-1"},
                {"name": "HEADLESS-old"},
                {"name": "Monitorize-old"},
                {"name": "HEADLESS-3-extra"},
                {"name": "eDP-1"},
            ]),
            stderr="",
        )

        monitors = DisplayController("hyprland").headless_monitors()

        self.assertEqual(monitors, ["HEADLESS-2", "Monitorize-1"])
        run.assert_called_once_with(
            [
                "flatpak-spawn",
                "--host",
                "--directory=/",
                "hyprctl",
                "-j",
                "monitors",
                "all",
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
        success = Mock(returncode=0, stdout="ok", stderr="")
        run.side_effect = [
            success,
            success,
            Mock(returncode=0, stdout="[]", stderr=""),
            Mock(returncode=0, stdout="[]", stderr=""),
            success,
            success,
        ]
        controller = DisplayController("hyprland")
        controller.headless_monitors = Mock(
            side_effect=[[], ["Monitorize-1"]]
        )
        controller.wait_for_headless_ready = Mock(return_value=True)

        output, error = controller.prepare_hyprland(2560, 1440, 120)

        self.assertEqual((output, error), ("Monitorize-1", ""))
        self.assertEqual(controller.created_output, "Monitorize-1")
        controller.wait_for_headless_ready.assert_called_once_with(
            "Monitorize-1", 2560, 1440, fps=120
        )
        commands = [item.args[0] for item in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["flatpak-spawn", "--host", "--directory=/", "hyprctl", "version"],
                ["flatpak-spawn", "--host", "--directory=/", "hyprctl", "status"],
                ["flatpak-spawn", "--host", "--directory=/", "hyprctl", "-j", "instances"],
                ["flatpak-spawn", "--host", "--directory=/", "hyprctl", "-j", "monitors", "all"],
                [
                    "flatpak-spawn", "--host", "--directory=/", "hyprctl",
                    "output", "create", "headless", "Monitorize-1",
                ],
                [
                    "flatpak-spawn", "--host", "--directory=/", "hyprctl", "eval",
                    "hl.monitor({ output = 'Monitorize-1', mode = "
                    "'2560x1440@120', position = 'auto', scale = 1.0, "
                    "disabled = false })",
                ],
            ],
        )

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_legacy_configuration_is_only_a_fallback(self, _flatpak, run):
        success = Mock(returncode=0, stdout="ok", stderr="")
        run.side_effect = [
            success,
            success,
            Mock(returncode=0, stdout="[]", stderr=""),
            Mock(returncode=0, stdout="[]", stderr=""),
            success,
            Mock(returncode=1, stdout="", stderr="Lua API unavailable"),
            success,
        ]
        controller = DisplayController("hyprland")
        controller.headless_monitors = Mock(side_effect=[[], ["Monitorize-1"]])
        controller.wait_for_headless_ready = Mock(return_value=True)

        output, error = controller.prepare_hyprland(1920, 1080, 60)

        self.assertEqual((output, error), ("Monitorize-1", ""))
        commands = [item.args[0] for item in run.call_args_list]
        self.assertEqual(commands[-2:], [
            [
                "flatpak-spawn", "--host", "--directory=/", "hyprctl", "eval",
                "hl.monitor({ output = 'Monitorize-1', mode = '1920x1080@60', "
                "position = 'auto', scale = 1.0, disabled = false })",
            ],
            [
                "flatpak-spawn", "--host", "--directory=/", "hyprctl", "keyword", "monitor",
                "Monitorize-1,1920x1080@60,auto,1",
            ],
        ])

    @patch("monitorize.platform.display_controller.subprocess.run")
    @patch(
        "monitorize.platform.display_controller.os.path.isfile",
        return_value=True,
    )
    def test_retries_one_discovered_instance_before_creating_output(self, _flatpak, run):
        success = Mock(returncode=0, stdout="ok", stderr="")
        run.side_effect = [
            success,
            success,
            Mock(returncode=0, stdout=json.dumps([{"instance": "test-instance"}]), stderr=""),
            Mock(returncode=1, stdout="", stderr="missing signature"),
            Mock(returncode=0, stdout="[]", stderr=""),
            success,
            success,
        ]
        controller = DisplayController("hyprland")
        controller.headless_monitors = Mock(side_effect=[[], ["Monitorize-1"]])
        controller.wait_for_headless_ready = Mock(return_value=True)

        output, error = controller.prepare_hyprland(1920, 1080, 60)

        self.assertEqual((output, error), ("Monitorize-1", ""))
        commands = [item.args[0] for item in run.call_args_list]
        self.assertEqual(
            commands[4],
            [
                "flatpak-spawn", "--host", "--directory=/", "hyprctl", "-i", "test-instance",
                "-j", "monitors", "all",
            ],
        )
        self.assertTrue(all("-i" in command for command in commands[5:]))

    def test_additional_slot_uses_stable_monitorize_2_name(self):
        controller = DisplayController("hyprland")
        controller._verify_hyprland_ipc = Mock(return_value="")
        controller.headless_monitors = Mock(side_effect=[[], ["Monitorize-2"]])
        controller._run_hyprctl = Mock(
            return_value=Mock(returncode=0, stdout="ok", stderr="")
        )
        controller.wait_for_headless_ready = Mock(return_value=True)

        output, error = controller.prepare_hyprland(
            1920, 1080, 60, slot="additional"
        )

        self.assertEqual((output, error), ("Monitorize-2", ""))
        self.assertEqual(controller.additional_output, "Monitorize-2")
        self.assertEqual(
            controller._run_hyprctl.call_args_list[0].args,
            ("output", "create", "headless", "Monitorize-2"),
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
                "flatpak-spawn", "--host", "--directory=/", "hyprctl",
                "output", "remove", "HEADLESS-2",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertIsNone(controller.created_output)

    def test_removes_all_exact_stagnant_headless_outputs(self):
        controller = DisplayController("hyprland")
        controller._verify_hyprland_ipc = Mock(return_value="")
        controller.headless_monitors = Mock(
            return_value=[
                "HEADLESS-2", "HEADLESS-old", "HEADLESS-17",
                "Monitorize-1", "Monitorize-old", "Monitorize-20-extra",
                "Monitorize-2",
            ]
        )
        controller._remove_hyprland_output = Mock(return_value=True)

        removed = controller.remove_stagnant_virtual_displays()

        self.assertEqual(removed, 4)
        self.assertEqual(
            [call.args[0] for call in controller._remove_hyprland_output.call_args_list],
            ["HEADLESS-17", "HEADLESS-2", "Monitorize-1", "Monitorize-2"],
        )

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
