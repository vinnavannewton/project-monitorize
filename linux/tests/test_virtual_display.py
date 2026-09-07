import io
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import gnome_virtual_monitor
from monitorize.streaming import headless_virtual_display


class VirtualDisplayTest(unittest.TestCase):
    @staticmethod
    def gnome_state(*monitors):
        physical = []
        for connector, width, height, refresh_rate in monitors:
            mode = (
                f"{width}x{height}@{refresh_rate}",
                width,
                height,
                refresh_rate,
                1.0,
                [1.0, 1.25],
                {"is-current": True},
            )
            physical.append(
                ((connector, "Meta", "Virtual", connector), [mode], {})
            )
        return 1, physical, [], {}

    def test_gnome_virtual_connector_detection_no_longer_uses_input_bridge(self):
        state = (
            1,
            [
                (("eDP-1", "Vendor", "Panel", "1"), [], {}),
                (("Meta-0", "Meta", "Virtual", "2"), [], {}),
            ],
            [],
            {},
        )
        self.assertEqual(
            gnome_virtual_monitor.virtual_connectors_from_state(state), ["Meta-0"]
        )

    def test_gnome_virtual_monitor_requires_exact_mode_and_refresh(self):
        state = self.gnome_state(("Meta-0", 1920, 1080, 59.94))
        connector, info, error = gnome_virtual_monitor.verified_new_virtual_monitor(
            state, set(), 1920, 1080, 60
        )
        self.assertEqual(connector, "Meta-0")
        self.assertEqual(info["refresh_rate"], 59.94)
        self.assertEqual(error, "")

        connector, info, error = gnome_virtual_monitor.verified_new_virtual_monitor(
            state, set(), 2560, 1440, 60
        )
        self.assertEqual(connector, "")
        self.assertIsNotNone(info)
        self.assertIn("expected 2560x1440", error)

        connector, info, error = gnome_virtual_monitor.verified_new_virtual_monitor(
            state, set(), 1920, 1080, 75
        )
        self.assertEqual(connector, "")
        self.assertIsNotNone(info)
        self.assertIn("expected 75Hz", error)

    def test_gnome_virtual_monitor_never_guesses_between_new_connectors(self):
        state = self.gnome_state(
            ("Meta-0", 1920, 1080, 60),
            ("Meta-1", 1920, 1080, 60),
        )
        connector, info, error = gnome_virtual_monitor.verified_new_virtual_monitor(
            state, set(), 1920, 1080, 60
        )
        self.assertEqual(connector, "")
        self.assertIsNone(info)
        self.assertIn("found 2", error)

    @patch("monitorize.platform.gnome_virtual_monitor.load_gnome_virtual_layout")
    def test_gnome_additional_scale_comes_from_saved_topology_role(self, load):
        load.return_value = {
            "logical_monitors": [
                {"virtual": True, "role": "primary", "scale": 1.0},
                {"virtual": True, "role": "additional", "scale": 1.25},
            ]
        }
        self.assertEqual(
            gnome_virtual_monitor.load_saved_virtual_scale(
                "primary+additional", role="additional"
            ),
            1.25,
        )
        load.assert_called_once_with("primary+additional")

    @patch("monitorize.platform.display_controller.DisplayController")
    @patch("monitorize.streaming.headless_virtual_display.select.select", return_value=([object()], [], []))
    @patch.object(headless_virtual_display.sys, "stdin", io.StringIO("quit\n"))
    def test_hyprland_holder_creates_and_removes_requested_slot(
        self, _select, display_controller
    ):
        controller = display_controller.return_value
        controller.prepare_hyprland.return_value = ("HEADLESS-2", "")
        self.assertEqual(
            headless_virtual_display.run_hyprland_headless(
                "additional", 1920, 1080, 60
            ),
            0,
        )
        controller.remove_hyprland_output.assert_called_once_with(slot="additional")

    @patch("monitorize.platform.display_controller.DisplayController")
    @patch("monitorize.streaming.headless_virtual_display.select.select", return_value=([object()], [], []))
    @patch.object(headless_virtual_display.sys, "stdin", io.StringIO("quit\n"))
    def test_sway_holder_creates_and_removes_requested_slot(
        self, _select, display_controller
    ):
        controller = display_controller.return_value
        controller.prepare_sway.return_value = ("HEADLESS-2", "")
        self.assertEqual(
            headless_virtual_display.run_sway_headless("additional", 1920, 1080, 60),
            0,
        )
        controller.remove_sway_output.assert_called_once_with(slot="additional")


if __name__ == "__main__":
    unittest.main()
