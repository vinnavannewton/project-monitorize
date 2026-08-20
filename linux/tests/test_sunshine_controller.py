import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication

from monitorize.desktop.streaming_controller import StreamingController


class SunshineControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def controller(self, de="kde"):
        controller = StreamingController(de, "192.0.2.1")
        self.addCleanup(controller.sunshine_watchdog_timer.stop)
        return controller

    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    def test_mirror_starts_only_sunshine(
        self, sync, save, running, start, _stop
    ):
        controller = self.controller()
        controller.start(
            "1920x1080", "60", "Mirror", "VA-API", "H.265 (HEVC)", True, True
        )
        self.assertTrue(controller.streaming)
        self.assertTrue(controller.primary_ready)
        self.assertIsNone(controller.streamer)
        sync.assert_called_once_with(
            "", "VA-API", "H.265 (HEVC)", True, instance=1
        )
        save.assert_called_once_with({"stream_audio": "enabled"}, instance=1)
        start.assert_called_once_with(
            1,
            pipewire_node=None,
            offset_x=0,
            offset_y=0,
            width=1920,
            height=1080,
        )

    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    def test_gnome_event_propagates_pipewire_node_and_offsets(
        self, _sync, _save, _running, start, _stop
    ):
        controller = self.controller("gnome")
        controller.streaming = True
        controller._display_ready(
            "primary",
            {
                "type": "headless_ready",
                "name": "Meta-0",
                "node_id": 42,
                "offset_x": 1920,
                "offset_y": 120,
                "width": 1280,
                "height": 800,
                "fps": 60,
            },
        )
        start.assert_called_once_with(
            1,
            pipewire_node=42,
            offset_x=1920,
            offset_y=120,
            width=1280,
            height=800,
        )
        self.assertEqual(controller.gnome_outputs["primary"], "Meta-0")
        self.assertTrue(controller.primary_ready)

    @patch.object(StreamingController, "_start_display_process", return_value=Mock())
    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    def test_extend_and_second_display_use_headless_holder(self, _stop, launch):
        controller = self.controller("hyprland")
        controller.start("2560x1440", "90", "Extend")
        launch.assert_called_once_with("primary", 2560, 1440, 90, controller.generation)
        controller.primary_ready = True
        controller.start_third("1920x1080", "60", "NVIDIA", "AV1", False, True)
        self.assertEqual(launch.call_args.args[:4], ("additional", 1920, 1080, 60))
        self.assertTrue(controller.third_streaming)

    def test_active_configuration_contains_only_sunshine_fields(self):
        controller = self.controller()
        controller.streaming = True
        controller.width, controller.height, controller.fps = 1920, 1080, 60
        controller.encoder = "VA-API"
        controller.codec = "H.265 (HEVC)"
        config = controller.active_configuration()
        self.assertEqual(config["version"], 2)
        self.assertEqual(config["primary"]["sunshine_encoder"], "VA-API")
        self.assertNotIn("bitrate", config["primary"])
        self.assertNotIn("mode", config)


if __name__ == "__main__":
    unittest.main()
