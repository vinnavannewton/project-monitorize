import io
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import gnome_virtual_monitor
from monitorize.streaming import headless_virtual_display


class VirtualDisplayTest(unittest.TestCase):
    class FakeDbus:
        @staticmethod
        def Array(values, signature=None):
            return list(values)

        @staticmethod
        def Struct(values, signature=None):
            return tuple(values)

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

    def test_gnome_active_output_modes_include_physical_and_virtual_connectors(self):
        serial, physical, _logical, properties = self.gnome_state(
            ("eDP-1", 2560, 1600, 60),
            ("Meta-0", 1920, 1080, 60),
        )
        logical = [
            (0, 0, 1.0, 0, True, [("eDP-1",)]),
            (2560, 0, 1.0, 0, False, [("Meta-0",)]),
        ]

        modes = gnome_virtual_monitor.active_output_modes(
            (serial, physical, logical, properties)
        )

        self.assertEqual(modes["eDP-1"]["width"], 2560)
        self.assertEqual(modes["Meta-0"]["height"], 1080)


    def test_new_vkms_connector_uses_before_after_identity_and_excludes_meta(self):
        before = self.gnome_state(
            ("eDP-1", 1920, 1080, 60),
            ("Virtual-1", 1024, 768, 60),
        )
        after = self.gnome_state(
            ("eDP-1", 1920, 1080, 60),
            ("Virtual-1", 1024, 768, 60),
            ("Meta-0", 1920, 1080, 60),
            ("Virtual-2", 2560, 1600, 60),
        )

        connector, error = gnome_virtual_monitor.new_vkms_connector_from_state(
            after,
            gnome_virtual_monitor.physical_monitor_identities(before),
            2560,
            1600,
        )

        self.assertEqual(connector, "Virtual-2")
        self.assertEqual(error, "")

    def test_vkms_activation_payload_preserves_all_active_displays_and_appends_virtual(self):
        def monitor(name, width, height, current=True):
            return (
                (name, "Vendor", name, "1"),
                [
                    (
                        f"{width}x{height}@60",
                        width, height, 60.0, 1.0, [1.0, 1.25],
                        {"is-current": current},
                    )
                ],
                {},
            )

        state = (
            7,
            [
                monitor("eDP-1", 1920, 1080),
                monitor("HDMI-A-1", 2560, 1440),
                monitor("Virtual-2", 2560, 1600, current=False),
            ],
            [
                (0, 0, 1.0, 0, True, [("eDP-1",)]),
                (1920, 0, 1.0, 0, False, [("HDMI-A-1",)]),
            ],
            {"layout-mode": 1},
        )

        payload, details, error = gnome_virtual_monitor.build_vkms_activation_config(
            state, "Virtual-2", 2560, 1600, 60, self.FakeDbus()
        )

        self.assertEqual(error, "")
        self.assertEqual(len(payload), 3)
        self.assertEqual(payload[0][5][0][0], "eDP-1")
        self.assertEqual(payload[1][5][0][0], "HDMI-A-1")
        self.assertEqual(payload[2][0:5], (4480, 0, 1.0, 0, False))
        self.assertEqual(payload[2][5][0][0], "Virtual-2")
        self.assertEqual(details["mode_id"], "2560x1600@60")

    def test_vkms_activation_never_substitutes_a_different_size(self):
        state = self.gnome_state(("Virtual-2", 1024, 768, 60))
        selected, error = gnome_virtual_monitor.select_vkms_mode(
            state, "Virtual-2", 2560, 1600, 60
        )
        self.assertIsNone(selected)
        self.assertIn("2560x1600", error)

    def test_vkms_activation_retries_one_stale_serial_and_verifies_logical_state(self):
        before = self.gnome_state(("eDP-1", 1920, 1080, 60))
        discovered = self.gnome_state(
            ("eDP-1", 1920, 1080, 60),
            ("Virtual-2", 2560, 1600, 60),
        )
        # A discovered physical monitor has a selectable mode but no current
        # mode until ApplyMonitorsConfig activates it.
        discovered[1][1][1][0] = (*discovered[1][1][1][0][:6], {})
        discovered = (
            9,
            discovered[1],
            [(0, 0, 1.0, 0, True, [("eDP-1",)])],
            {"layout-mode": 1},
        )
        active = self.gnome_state(
            ("eDP-1", 1920, 1080, 60),
            ("Virtual-2", 2560, 1600, 60),
        )
        active = (
            10,
            active[1],
            [
                (0, 0, 1.0, 0, True, [("eDP-1",)]),
                (1920, 0, 1.0, 0, False, [("Virtual-2",)]),
            ],
            {"layout-mode": 1},
        )

        class Display:
            def __init__(self):
                self.states = [discovered, discovered, active]
                self.calls = 0
                self.applies = 0

            def GetCurrentState(self):
                value = self.states[min(self.calls, len(self.states) - 1)]
                self.calls += 1
                return value

            def ApplyMonitorsConfig(self, *_args):
                self.applies += 1
                if self.applies == 1:
                    raise RuntimeError("stale serial")

        display = Display()
        ok, details, message = gnome_virtual_monitor.activate_discovered_vkms_monitor(
            gnome_virtual_monitor.physical_monitor_identities(before),
            2560, 1600, 60,
            display_config=display,
            dbus=self.FakeDbus(),
            discovery_attempts=1,
            activation_attempts=1,
            delay=0,
        )

        self.assertTrue(ok, message)
        self.assertEqual(details["name"], "Virtual-2")
        self.assertEqual(display.applies, 2)

    def test_vkms_cleanup_removes_only_new_virtual_layout_entries(self):
        before = self.gnome_state(("eDP-1", 1920, 1080, 60))
        active = self.gnome_state(
            ("eDP-1", 1920, 1080, 60),
            ("Virtual-2", 2560, 1600, 60),
        )
        active = (
            10,
            active[1],
            [
                (0, 0, 1.0, 0, True, [("eDP-1",)]),
                (1920, 0, 1.0, 0, False, [("Virtual-2",)]),
            ],
            {"layout-mode": 1},
        )
        removed = (11, active[1], [active[2][0]], {"layout-mode": 1})

        class Display:
            def __init__(self):
                self.states = [active, removed]
                self.calls = 0
                self.applies = 0

            def GetCurrentState(self):
                value = self.states[min(self.calls, len(self.states) - 1)]
                self.calls += 1
                return value

            def ApplyMonitorsConfig(self, *_args):
                self.applies += 1

        display = Display()
        self.assertTrue(
            gnome_virtual_monitor.remove_new_vkms_monitors_from_layout(
                gnome_virtual_monitor.physical_monitor_identities(before),
                display_config=display,
                dbus=self.FakeDbus(),
                attempts=1,
                delay=0,
            )
        )
        self.assertEqual(display.applies, 1)


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
