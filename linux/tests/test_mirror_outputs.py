import unittest
from unittest.mock import Mock, patch
from monitorize.platform.mirror_outputs import active_outputs, select_output


class MirrorOutputsTest(unittest.TestCase):
    def test_selection_never_substitutes_an_unplugged_monitor(self):
        outputs = [{"id": "DP-1"}, {"id": "HDMI-A-1"}]
        self.assertEqual(select_output(outputs, "HDMI-A-1")["id"], "HDMI-A-1")
        for requested in ("", "missing"):
            with self.assertRaises(ValueError):
                select_output(outputs, requested)
        with self.assertRaises(ValueError):
            select_output([{"id": "DP-1"}], "HDMI-A-1")

    def test_single_output_can_be_selected_without_a_saved_preference(self):
        self.assertEqual(select_output([{"id": "eDP-1"}], "")["id"], "eDP-1")

    def test_empty_or_ambiguous_inventory_is_rejected(self):
        for outputs in ([], [{"id": "DP-1"}, {"id": "DP-1"}]):
            with self.assertRaises(ValueError):
                select_output(outputs, "DP-1")

    def test_inventory_keeps_active_outputs_regardless_of_origin_or_metadata(self):
        from PyQt6.QtCore import QRect

        app = Mock()
        screens = []
        output_data = [
            ("eDP-2", "Framework", "Laptop Display"),
            ("Virtual-1", "", ""),
            ("Meta-0", "", ""),
            ("HEADLESS-1", "", ""),
            ("Monitorize-1", "", ""),
            ("Unknown-Connector", "", ""),
        ]
        for name, manufacturer, model in output_data:
            screen = Mock()
            screen.name.return_value = name
            screen.manufacturer.return_value = manufacturer
            screen.model.return_value = model
            screen.geometry.return_value = QRect(1920, 0, 2560, 1440)
            screens.append(screen)
        app.screens.return_value = screens
        app.primaryScreen.return_value = screens[0]
        with patch("PyQt6.QtGui.QGuiApplication.instance", return_value=app):
            result = active_outputs()
        self.assertEqual([r["id"] for r in result], [row[0] for row in output_data])
        self.assertEqual(result[0]["label"], "eDP-2 — Framework Laptop Display")
        self.assertEqual(result[1]["label"], "Virtual-1")
        self.assertTrue(result[0]["primary"])
        self.assertEqual(result[1]["x"], 1920)

    def test_inventory_rejects_only_unnamed_or_unusable_screens(self):
        from PyQt6.QtCore import QRect

        app = Mock()
        screens = []
        for name, geometry in [
            ("", QRect(0, 0, 1920, 1080)),
            ("Disabled-1", QRect(0, 0, 0, 1080)),
            ("Disconnected-1", QRect(0, 0, 1920, 0)),
            ("Virtual-1", QRect(0, 0, 1024, 768)),
        ]:
            screen = Mock()
            screen.name.return_value = name
            screen.manufacturer.return_value = ""
            screen.model.return_value = ""
            screen.geometry.return_value = geometry
            screens.append(screen)
        app.screens.return_value = screens
        app.primaryScreen.return_value = screens[-1]
        with patch("PyQt6.QtGui.QGuiApplication.instance", return_value=app):
            result = active_outputs()
        self.assertEqual([item["id"] for item in result], ["Virtual-1"])

    def test_inventory_uses_authoritative_mode_instead_of_scaled_qt_geometry(self):
        from PyQt6.QtCore import QRect

        screen = Mock()
        screen.name.return_value = "eDP-2"
        screen.manufacturer.return_value = "Vendor"
        screen.model.return_value = "Panel"
        screen.geometry.return_value = QRect(0, 0, 1463, 914)
        screen.devicePixelRatio.return_value = 2.0
        app = Mock()
        app.screens.return_value = [screen]
        app.primaryScreen.return_value = screen
        modes = {
            "eDP-2": {
                "width": 2560,
                "height": 1600,
                "refresh_rate": 165.002,
            }
        }
        with (
            patch("PyQt6.QtGui.QGuiApplication.instance", return_value=app),
            patch(
                "monitorize.platform.mirror_outputs._compositor_modes",
                return_value=modes,
            ),
        ):
            result = active_outputs("kde")

        self.assertEqual(result[0]["width"], 1463)
        self.assertEqual(result[0]["height"], 914)
        self.assertEqual(result[0]["native_width"], 2560)
        self.assertEqual(result[0]["native_height"], 1600)
        self.assertAlmostEqual(result[0]["refresh_rate"], 165.002)
