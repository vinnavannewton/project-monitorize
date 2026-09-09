import unittest
from unittest.mock import Mock, patch
from monitorize.platform.mirror_outputs import physical_outputs, select_output


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

    def test_inventory_keeps_physical_connectors_and_excludes_virtual_outputs(self):
        from PyQt6.QtCore import QRect
        app = Mock()
        screens = []
        for name in ["eDP-1", "DP-1", "HDMI-A-1", "Monitorize-1", "HEADLESS-2", "Meta-0", "Virtual-1", "XWAYLAND0"]:
            screen = Mock()
            screen.name.return_value = name
            screen.manufacturer.return_value = "Vendor"
            screen.model.return_value = "Panel"
            screen.geometry.return_value = QRect(1920, 0, 2560, 1440)
            screens.append(screen)
        app.screens.return_value = screens
        app.primaryScreen.return_value = screens[0]
        with patch("PyQt6.QtGui.QGuiApplication.instance", return_value=app):
            result = physical_outputs()
        self.assertEqual([r["id"] for r in result], ["eDP-1", "DP-1", "HDMI-A-1"])
        self.assertTrue(result[0]["primary"])
        self.assertEqual(result[1]["x"], 1920)
