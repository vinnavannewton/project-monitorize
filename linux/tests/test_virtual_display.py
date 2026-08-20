import io
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import gnome_virtual_monitor
from monitorize.streaming import headless_virtual_display


class VirtualDisplayTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
