import logging
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
            "", "VA-API", "H.265 (HEVC)", True, instance=1, capture="kwin",
            adapter_name="",
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

    @patch("monitorize.desktop.streaming_controller.os.path.isfile", return_value=True)
    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    def test_flatpak_kde_mirror_uses_portal_capture(
        self, sync, _save, _running, _start, _stop, _flatpak
    ):
        self.controller().start("1920x1080", "60", "Mirror")
        self.assertEqual(sync.call_args.kwargs["capture"], "portal")

    @patch("monitorize.desktop.streaming_controller.os.path.isfile", return_value=True)
    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    def test_flatpak_kde_extend_uses_portal_virtual(
        self, sync, _save, _running, start, _stop, _flatpak
    ):
        controller = self.controller("kde")
        controller.start("1920x1080", "60", "Extend")
        self.assertEqual(sync.call_args.kwargs["capture"], "portal")
        self.assertEqual(
            start.call_args.kwargs.get("extra_environment", {}).get("SUNSHINE_PORTAL_SOURCE_TYPE"),
            "virtual",
        )
        self.assertIsNone(controller.streamer)
        self.assertTrue(controller.primary_ready)

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

        _sync.assert_called_once_with(
            "Meta-0", "Auto", "Auto", True, instance=1, capture="",
            adapter_name="",
        )

    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    def test_kde_event_uses_native_kwin_capture_instead_of_direct_pipewire_node(
        self, _sync, _save, _running, start, _stop
    ):
        controller = self.controller("kde")
        controller.streaming = True
        controller._display_ready(
            "primary",
            {
                "type": "headless_ready",
                "name": "Virtual-Monitorize-1",
                "node_id": 42,
                "width": 1920,
                "height": 1200,
                "fps": 60,
            },
        )
        start.assert_called_once_with(
            1,
            pipewire_node=None,
            offset_x=0,
            offset_y=0,
            width=1920,
            height=1200,
        )
        _sync.assert_called_once_with(
            "Virtual-Monitorize-1", "Auto", "Auto", True, instance=1,
            capture="kwin", adapter_name="",
        )
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

    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    @patch("monitorize.desktop.streaming_controller.start_sunshine", return_value=(True, "started"))
    @patch("monitorize.desktop.streaming_controller.is_sunshine_running", return_value=False)
    @patch("monitorize.desktop.streaming_controller.save_sunshine_config", return_value=(True, "saved"))
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config", return_value=(True, "synced"))
    @patch("monitorize.desktop.streaming_controller.resolve_encoding_gpu")
    def test_selected_nvidia_gpu_reaches_config_and_process_environment(
        self, resolve, sync, _save, _running, start, _stop
    ):
        resolve.return_value = {
            "id": "0000:03:00.0",
            "label": "NVIDIA RTX (0000:03:00.0)",
            "render_node": "/dev/dri/renderD129",
            "cuda_index": "1",
        }
        controller = self.controller()
        controller.start(
            "1920x1080", "60", "Mirror", "NVIDIA", "H.264 (AVC)",
            True, False, gpu_id="0000:03:00.0",
        )

        resolve.assert_called_once_with("NVIDIA", "0000:03:00.0")
        self.assertEqual(sync.call_args.kwargs["adapter_name"], "/dev/dri/renderD129")
        self.assertEqual(
            start.call_args.kwargs["extra_environment"],
            {"CUDA_VISIBLE_DEVICES": "1"},
        )

    @patch("monitorize.desktop.streaming_controller.start_sunshine")
    @patch("monitorize.desktop.streaming_controller.sync_sunshine_stream_config")
    @patch("monitorize.desktop.streaming_controller.resolve_encoding_gpu", return_value=None)
    @patch("monitorize.desktop.streaming_controller.stop_sunshine")
    def test_unavailable_selected_gpu_does_not_fall_back(
        self, _stop, resolve, sync, start
    ):
        controller = self.controller()
        failures = []
        controller.startFailed.connect(lambda: failures.append(True))
        controller.start(
            "1920x1080", "60", "Mirror", "NVIDIA", "Auto", True,
            gpu_id="0000:03:00.0",
        )

        resolve.assert_called_once_with("NVIDIA", "0000:03:00.0")
        sync.assert_not_called()
        start.assert_not_called()
        self.assertFalse(controller.streaming)
        self.assertIn("unavailable", controller.status)
        self.assertEqual(failures, [True])

    @patch("monitorize.desktop.streaming_controller.app_log.write")
    @patch("monitorize.desktop.streaming_controller.QTimer.singleShot")
    @patch("monitorize.desktop.streaming_controller.check_sunshine_health")
    def test_sunshine_watchdog_instance_1_failure(
        self, mock_health, mock_singleshot, mock_log_write
    ):
        mock_health.return_value = (False, 1, "VAAPI init failed")
        controller = self.controller()
        controller.streaming = True
        controller.sunshine_watchdog_timer.start(1000)

        controller._check_sunshine_health()

        self.assertFalse(controller.sunshine_watchdog_timer.isActive())
        mock_log_write.assert_called_once_with(
            "SUNSHINE",
            "Sunshine instance 1 stopped unexpectedly (exit code 1): VAAPI init failed",
            level=logging.ERROR,
        )
        mock_singleshot.assert_called_once_with(0, controller.stop)
        self.assertIn("stopped unexpectedly", controller.status)

    @patch("monitorize.desktop.streaming_controller.app_log.write")
    @patch("monitorize.desktop.streaming_controller.QTimer.singleShot")
    @patch("monitorize.desktop.streaming_controller.check_sunshine_health")
    def test_sunshine_watchdog_instance_2_failure(
        self, mock_health, mock_singleshot, mock_log_write
    ):
        def health_side_effect(instance):
            if instance == 1:
                return (True, None, "")
            return (False, 2, "Encoder crash")

        mock_health.side_effect = health_side_effect
        controller = self.controller()
        controller.streaming = True
        controller.third_streaming = True
        controller.sunshine_watchdog_timer.start(1000)

        controller._check_sunshine_health()

        mock_log_write.assert_called_once_with(
            "SUNSHINE",
            "Sunshine instance 2 stopped unexpectedly (exit code 2): Encoder crash",
            level=logging.ERROR,
        )
        mock_singleshot.assert_called_once_with(0, controller.stop_third)

    @patch("monitorize.desktop.streaming_controller.app_log.write")
    @patch("monitorize.desktop.streaming_controller.QTimer.singleShot")
    @patch("monitorize.desktop.streaming_controller.check_sunshine_health", return_value=(True, None, ""))
    def test_sunshine_watchdog_healthy(
        self, mock_health, mock_singleshot, mock_log_write
    ):
        controller = self.controller()
        controller.streaming = True
        controller.sunshine_watchdog_timer.start(1000)

        controller._check_sunshine_health()

        self.assertTrue(controller.sunshine_watchdog_timer.isActive())
        mock_log_write.assert_not_called()
        mock_singleshot.assert_not_called()

    @patch("monitorize.desktop.streaming_controller.app_log.write")
    @patch("monitorize.desktop.streaming_controller.QTimer.singleShot")
    @patch(
        "monitorize.desktop.streaming_controller.get_sunshine_strict_selection_error",
        return_value="Error: MONITORIZE_STRICT_CODEC_REJECTED",
    )
    @patch(
        "monitorize.desktop.streaming_controller.check_sunshine_health",
        return_value=(True, None, ""),
    )
    def test_sunshine_watchdog_reports_strict_selection_error(
        self, _health, strict_error, mock_singleshot, mock_log_write
    ):
        controller = self.controller()
        controller.streaming = True

        controller._check_sunshine_health()

        strict_error.assert_called_once_with(1, 0)
        self.assertIn("rejected the selected encoder or codec", controller.status)
        mock_log_write.assert_called_once()
        mock_singleshot.assert_called_once_with(0, controller.stop)

    @patch("monitorize.desktop.streaming_controller.app_log.write")
    @patch("monitorize.desktop.streaming_controller.check_sunshine_health", side_effect=RuntimeError("disk error"))
    def test_sunshine_watchdog_handles_exception_defensively(
        self, mock_health, mock_log_write
    ):
        controller = self.controller()
        controller.streaming = True
        controller._check_sunshine_health()
        mock_log_write.assert_called_once_with(
            "SUNSHINE",
            "Failed to check Sunshine health: disk error",
            level=logging.ERROR,
        )


if __name__ == "__main__":
    unittest.main()
