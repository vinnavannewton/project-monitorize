import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QProcess
from monitorize.desktop.session import Session
from monitorize.desktop.streaming_controller import StreamingController


class SessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        p = patch("monitorize.platform.mirror_outputs.physical_outputs", return_value=[{"id": "eDP-1"}])
        p.start()
        self.addCleanup(p.stop)
        self.config = {"display_type": "Extend", "resolution": "1920x1080", "fps": "60"}
        p = patch("monitorize.desktop.session.load_display_settings", side_effect=lambda: self.config.copy())
        p.start()
        self.addCleanup(p.stop)
        p = patch("monitorize.desktop.streaming_controller.stop_sunshine")
        self.stop_sunshine = p.start()
        self.addCleanup(p.stop)
        self.c = StreamingController("hyprland", "192.0.2.1")
        self.c._start_display_process = Mock(side_effect=self.process)
        self.c._start_instance = Mock(return_value=True)
        self.s = Session(self.c)
        self.addCleanup(self.c.sunshine_watchdog_timer.stop)

    @staticmethod
    def process(*_args):
        p = Mock()
        p.state.return_value = QProcess.ProcessState.NotRunning
        return p

    def ready(self, slot="primary", **extra):
        self.c._display_ready(slot, dict(name="Monitorize-1" if slot == "primary" else "Monitorize-2",
                                       width=1920, height=1080, fps=60, **extra))
        QCoreApplication.processEvents()

    def launch_one(self):
        self.s.add()
        self.s.start()
        self.ready()

    def launch_two(self):
        self.launch_one()
        self.s.add()
        self.s.start()
        self.ready("additional")

    def test_add_only_creates_cards_in_both_backend_modes(self):
        for backend in ("sunshine", "none"):
            self.c.streaming_backend = backend
            self.s.count = 0
            self.s.add()
            self.s.add()
            self.s.add()
            self.assertEqual(self.s.count, 2)
            self.assertFalse(self.s.busy)
            self.assertFalse(self.c.streaming)
            self.c._start_display_process.assert_not_called()
            self.c._start_instance.assert_not_called()

    def test_start_creates_display_and_streams_only_after_ready(self):
        self.config.update(streaming_customized=True, sunshine_codec="AV1", sunshine_encoder="NVIDIA")
        self.s.add()
        self.s.start()
        self.assertTrue(self.s.busy)
        self.c._start_instance.assert_not_called()
        self.ready()
        self.assertTrue(self.s.running)
        self.assertEqual(self.c.codec, "AV1")
        self.c._start_instance.assert_called_once()
        self.assertEqual(self.c._start_display_process.call_count, 1)

    def test_stop_removes_resources_but_keeps_cards_and_restart_recreates(self):
        self.launch_one()
        self.s.stop()
        self.assertFalse(self.c.streaming)
        self.assertIsNone(self.c.streamer)
        self.assertEqual(self.s.count, 1)
        self.assertEqual(self.s.cards()[0]["state"], "Not started")
        self.s.start()
        self.ready()
        self.assertTrue(self.s.running)
        self.assertEqual(self.c._start_display_process.call_count, 2)

    def test_second_card_added_while_running_waits_for_explicit_start(self):
        self.launch_one()
        self.s.add()
        self.assertTrue(self.s.pending_displays)
        self.assertFalse(self.c.third_streaming)
        self.assertEqual(self.c._start_display_process.call_count, 1)
        self.s.start()
        self.ready("additional")
        self.assertFalse(self.s.pending_displays)
        self.assertEqual(self.c._start_instance.call_args.args[0], 2)
        self.assertEqual(self.s.cards()[1]["address"], "192.0.2.1:49089")

    def test_two_cards_start_and_restart_in_order(self):
        self.s.add()
        self.s.add()
        self.s.start()
        self.ready()
        self.assertTrue(self.c.third_streaming)
        self.ready("additional")
        self.assertTrue(self.s.running)
        self.s.stop()
        self.s.start()
        self.ready()
        self.ready("additional")
        self.assertTrue(self.s.running)
        self.assertEqual(self.c._start_display_process.call_count, 4)

    def test_remove_pending_card_never_touches_resources(self):
        self.s.add()
        self.s.remove(0)
        self.assertEqual(self.s.count, 0)
        self.stop_sunshine.assert_not_called()
        self.c._start_display_process.assert_not_called()

    def test_remove_second_leaves_primary_running(self):
        self.launch_two()
        self.s.remove(1)
        self.assertTrue(self.s.running)
        self.assertTrue(self.c.streaming)
        self.assertEqual(self.s.count, 1)
        self.assertFalse(self.c.third_streaming)

    def test_remove_first_stops_before_renumbering_remaining_card(self):
        self.launch_two()
        self.s.remove(0)
        self.assertFalse(self.c.streaming)
        self.assertEqual(self.s.cards()[0]["number"], 1)

    def test_mirror_rejects_add_and_starts_without_virtual_outputs(self):
        self.config["display_type"] = "Mirror"
        self.s.add()
        self.assertEqual(self.s.count, 0)
        self.s.start()
        self.assertTrue(self.s.running)
        self.assertEqual(self.s.cards(), [])
        self.c._start_display_process.assert_not_called()

    def test_virtual_only_creates_on_start_without_sunshine(self):
        self.c.streaming_backend = "none"
        self.s.add()
        self.c._start_display_process.assert_not_called()
        self.s.start()
        self.ready()
        self.c._start_instance.assert_not_called()
        self.assertFalse(self.s.running)
        self.assertTrue(self.c.streaming)
        self.s.add()
        self.assertFalse(self.c.third_streaming)
        self.s.start()
        self.ready("additional")
        self.assertTrue(self.c.third_streaming)
        self.c._start_instance.assert_not_called()
        self.s.stop()
        self.assertFalse(self.c.streaming)

    def test_late_ready_after_stop_is_ignored(self):
        self.s.add()
        self.s.start()
        process = self.c.streamer
        generation = self.c.generation
        self.s.stop()
        self.c._read_display_process("primary", generation, process)
        process.readAllStandardOutput.assert_not_called()
        self.assertFalse(self.s.running)

    def test_portal_node_is_retained_for_start(self):
        self.s.add()
        self.s.start()
        self.ready(portal=True, node_id=42)
        self.assertEqual(self.c._start_instance.call_args.kwargs["pipewire_node"], 42)

    def test_stopped_extend_can_be_switched_to_mirror(self):
        self.launch_one()
        self.s.stop()
        self.config["display_type"] = "Mirror"
        self.assertEqual(self.s.mode, "Mirror")
        self.s.start()
        self.assertEqual(self.s.count, 0)
        self.assertTrue(self.s.running)

    def test_failed_process_start_clears_busy_and_emits_failure(self):
        failed = Mock()
        self.c.startFailed.connect(failed)
        self.s.add()
        self.s.start()
        process = self.c.streamer
        process.error.return_value = QProcess.ProcessError.FailedToStart
        self.c._process_error("primary", self.c.generation, process)
        QCoreApplication.processEvents()
        failed.assert_called_once()
        self.assertFalse(self.s.busy)
        self.assertEqual(self.s.cards()[0]["state"], "Not started")

    def test_second_creation_failure_does_not_retry_forever(self):
        self.s.count = 2
        self.s.start()
        self.ready()
        process = self.c.third_streamer
        process.error.return_value = QProcess.ProcessError.FailedToStart
        self.c._process_error("additional", self.c.third_generation, process)
        QCoreApplication.processEvents()
        QCoreApplication.processEvents()
        self.assertFalse(self.s.start_requested)
        self.assertFalse(self.s.busy)
        self.assertEqual(self.c._start_display_process.call_count, 2)

    def test_preset_restart_retains_independent_second_settings(self):
        self.s.preset_configuration = {
            "primary": {"resolution": "2560x1440", "fps": "90", "sunshine_codec": "AV1"},
            "second": {"enabled": True, "resolution": "1280x800", "fps": "30", "sunshine_codec": "HEVC"},
        }
        self.s.count = 2
        self.s.start()
        self.ready()
        self.assertEqual(self.c.third_width, 1280)
        self.assertEqual(self.c.third_fps, 30)
        self.ready("additional")
        self.assertEqual(self.c.codec, "AV1")
        self.assertEqual(self.c.third_codec, "HEVC")
