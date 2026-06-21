import sys
import socket
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from subprocess import TimeoutExpired

from PyQt6.QtCore import QCoreApplication, QProcess

import pipeline_builder
from gui import process_utils, settings
from gui.discovery_service import DiscoveryService
from gui.backend import MonitorizeBackend
from gui.receiver_controller import ReceiverController
from gui.streaming_controller import StreamingController
from gui.third_stream_controller import ThirdStreamController
from gui.usb_controller import UsbController
from gui.validation import DEFAULT_PRIMARY_RESOLUTION, sanitize_resolution


app = QCoreApplication.instance() or QCoreApplication(sys.argv)


def process_mock():
    process = Mock()
    process.started.connect = Mock()
    process.readyReadStandardOutput.connect = Mock()
    process.finished.connect = Mock()
    process.errorOccurred.connect = Mock()
    process.errorString.return_value = "process error"
    process.state.return_value = QProcess.ProcessState.Running
    return process


class DiscoveryServiceTest(unittest.TestCase):
    def test_device_updates_do_not_duplicate_host(self):
        service = DiscoveryService()
        service.add_device("Old", "10.0.0.2", 7110, False)
        service.add_device("New", "10.0.0.2", 7110, True, "fingerprint", True)
        self.assertEqual(len(service.devices), 1)
        self.assertEqual(service.devices[0]["name"], "New")
        self.assertTrue(service.devices[0]["encrypted"])
        self.assertTrue(service.devices[0]["thirdAvailable"])

    def test_advertisement_contains_encryption_and_third_display_state(self):
        registered = []

        class FakeZeroconf:
            def register_service(self, info):
                registered.append(info)

            def close(self):
                pass

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.properties = kwargs["properties"]

        fake_module = types.SimpleNamespace(
            ServiceInfo=FakeInfo, Zeroconf=FakeZeroconf
        )
        service = DiscoveryService()
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
            service.advertise("127.0.0.1", False, True)
        self.assertEqual(registered[0].properties["encrypted"], "0")
        self.assertEqual(registered[0].properties["third_available"], "1")

    def test_lost_service_removes_device(self):
        service = DiscoveryService()
        service.add_device("Host", "10.0.0.2", 7110, service_name="svc")
        self.assertNotIn("serviceName", service.devices[0])
        service.remove_device("svc")
        self.assertEqual(service.devices, [])

    def test_service_update_by_name_replaces_old_endpoint(self):
        service = DiscoveryService()
        service.add_device("Old", "10.0.0.2", 7110, service_name="svc")
        service.add_device("New", "10.0.0.3", 7110, service_name="svc")
        self.assertEqual(len(service.devices), 1)
        self.assertEqual(service.devices[0]["ip"], "10.0.0.3")

    def test_discovery_ignores_ipv6_only_service(self):
        class FakeInfo:
            addresses = [b"0123456789abcdef"]
            port = 7110
            properties = {}

        class FakeZeroconf:
            def get_service_info(self, _type, _name):
                return FakeInfo()

            def close(self):
                pass

        class FakeBrowser:
            def __init__(self, zc, type_, listener):
                listener.add_service(zc, type_, "svc")

            def cancel(self):
                pass

        fake_module = types.SimpleNamespace(
            Zeroconf=FakeZeroconf,
            ServiceBrowser=FakeBrowser,
            ServiceListener=object,
        )
        service = DiscoveryService()
        with (
            patch.dict(sys.modules, {"zeroconf": fake_module}),
            patch("gui.discovery_service.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
        ):
            service.start()
        self.assertEqual(service.devices, [])

    def test_discovery_falls_back_for_bad_third_port(self):
        class FakeInfo:
            addresses = [socket.inet_aton("10.0.0.2")]
            port = 7110
            properties = {b"third_port": b"bad"}

        class FakeZeroconf:
            def get_service_info(self, _type, _name):
                return FakeInfo()

            def close(self):
                pass

        class FakeBrowser:
            def __init__(self, zc, type_, listener):
                listener.add_service(zc, type_, "svc")

            def cancel(self):
                pass

        fake_module = types.SimpleNamespace(
            Zeroconf=FakeZeroconf,
            ServiceBrowser=FakeBrowser,
            ServiceListener=object,
        )
        service = DiscoveryService()
        with (
            patch.dict(sys.modules, {"zeroconf": fake_module}),
            patch("gui.discovery_service.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
        ):
            service.start()
        self.assertEqual(service.devices[0]["thirdPort"], 7114)

    def test_advertise_is_idempotent_for_same_state(self):
        registered = []

        class FakeZeroconf:
            def register_service(self, info):
                registered.append(info)

            def unregister_service(self, _info):
                pass

            def close(self):
                pass

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.properties = kwargs["properties"]

        fake_module = types.SimpleNamespace(
            ServiceInfo=FakeInfo, Zeroconf=FakeZeroconf
        )
        service = DiscoveryService()
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
            service.advertise("127.0.0.1", False, True)
            service.advertise("127.0.0.1", False, True)
        self.assertEqual(len(registered), 1)


class ValidationTest(unittest.TestCase):
    def test_empty_resolution_falls_back_without_crashing(self):
        self.assertEqual(sanitize_resolution(""), DEFAULT_PRIMARY_RESOLUTION)
        self.assertEqual(sanitize_resolution("   "), DEFAULT_PRIMARY_RESOLUTION)


class ReceiverControllerTest(unittest.TestCase):
    def test_pipeline_keeps_low_latency_queue_and_selected_decoder(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.sink = "glimagesink"
        process = process_mock()
        with patch("gui.receiver_controller.QProcess", return_value=process):
            controller._launch_pipeline("10.0.0.2", 7114)
        command, args = process.start.call_args.args
        self.assertEqual(command, "gst-launch-1.0")
        self.assertIn("vah264dec", args)
        self.assertIn("leaky=downstream", args)
        self.assertIn("sync=false", args)
        self.assertIn("port=7114", args)

    def test_stale_credentials_request_pairing_again(self):
        controller = ReceiverController("kde", Mock())
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.process = None
        emitted = []
        controller.pairingRequired.connect(lambda *args: emitted.append(args))
        controller.tls_process = Mock()
        controller.tls_process.readAllStandardOutput.return_value = (
            b"[TLS RECEIVER] AUTH_FAILED new-fingerprint\n"
        )
        with patch("gui.receiver_controller.clear_receiver_credentials") as clear:
            controller._read_tls()
        clear.assert_called_once_with("10.0.0.2")
        self.assertEqual(emitted, [("10.0.0.2", 7110, "new-fingerprint")])

    def test_immediate_eos_schedules_retry(self):
        controller = ReceiverController("kde", Mock())
        controller.host = "10.0.0.2"
        controller.port = 7114
        controller.attempt_started = __import__("time").monotonic()
        controller.process = process_mock()
        controller.tls_process = None
        controller._finished(0, None)
        self.assertTrue(controller.retry_pending)
        self.assertEqual(controller.retry_count, 1)
        self.assertTrue(controller.retry_timer.isActive())
        controller.retry_timer.stop()

    def test_stale_tls_ready_does_not_launch_pipeline(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 2
        controller.host = "10.0.0.2"
        controller.port = 7110
        old_tls = process_mock()
        old_tls.readAllStandardOutput.return_value = b"[TLS RECEIVER] READY\n"
        controller.tls_process = process_mock()
        with patch.object(controller, "_launch_pipeline") as launch:
            controller._read_tls(old_tls, generation=1)
        launch.assert_not_called()

    def test_stale_receiver_finish_does_not_retry(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 3
        controller.process = process_mock()
        old_process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        controller._finished(0, None, old_process, generation=2)
        self.assertFalse(controller.retry_pending)
        self.assertFalse(controller.retry_timer.isActive())

    def test_stale_retry_attempt_is_ignored(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 5
        controller.stopping = False
        controller.encrypted = False
        controller.host = "10.0.0.2"
        controller.port = 7110
        with patch.object(controller, "_launch_pipeline") as launch:
            controller._start_attempt(generation=4)
        launch.assert_not_called()

    def test_invalid_receiver_target_is_rejected(self):
        controller = ReceiverController("kde", Mock())
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("  ", 7110, False, "", "", "Software")
        start.assert_not_called()
        self.assertEqual(controller.status, "Invalid host or port")

    def test_receiver_credentials_use_normalized_host_key(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings.save_receiver_credentials(" Host.Local ", "fingerprint", "token")
                self.assertEqual(
                    settings.load_receiver_credentials("host.local"),
                    ("fingerprint", "token"),
                )
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_second_display_settings_load_sanitizes_numeric_values(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings._save_group("second_display", {
                    "resolution": "1920x1080 (16:9)",
                    "fps": "nope",
                    "bitrate": "-1",
                    "encoder": "Bogus",
                })
                loaded = settings.load_second_display_settings()
                self.assertEqual(loaded["fps"], "60")
                self.assertEqual(loaded["bitrate"], "500")
                self.assertEqual(loaded["encoder"], "Software (CPU / x264enc)")
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file


class StreamingControllerTest(unittest.TestCase):
    def kde_controller(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.width = 1920
        controller.height = 1200
        controller.fps = 60
        controller.bitrate = 8000
        controller.wifi = True
        controller.encrypted = False
        controller.streaming = True
        controller.display_type = "Extend"
        controller.env = Mock()
        controller.generation = 3
        return controller

    def test_streamer_command_preserves_wlroots_output(self):
        discovery = Mock()
        controller = StreamingController("sway", "10.0.0.1", discovery)
        controller.width = 1920
        controller.height = 1200
        controller.fps = 60
        controller.bitrate = 8000
        controller.wifi = True
        controller.streaming = True
        controller.display_type = "Extend"
        controller.display.created_output = "HEADLESS-2"
        controller.env = Mock()
        process = process_mock()
        with patch("gui.streaming_controller.QProcess", return_value=process):
            controller._launch_streamer()
        args = process.start.call_args.args[1]
        self.assertEqual(args[-1], "HEADLESS-2")
        self.assertIn("wifi", args)
        discovery.advertise.assert_called_once_with(
            "10.0.0.1", False, False
        )

    def test_stop_cleans_processes_and_advertisement(self):
        discovery = Mock()
        controller = StreamingController("sway", "10.0.0.1", discovery)
        controller.streaming = True
        controller.krfb = process_mock()
        controller.streamer = process_mock()
        with (
            patch("gui.streaming_controller.stop_processes") as stop,
            patch("gui.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        stop.assert_called_once()
        discovery.stop_advertising.assert_called_once()
        self.assertFalse(controller.streaming)

    def test_stale_delayed_input_start_is_ignored(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 4
        with patch("gui.streaming_controller.QProcess") as process:
            controller._launch_input(generation=3)
        process.assert_not_called()

    def test_stale_streamer_exit_does_not_restart_gnome(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        old_process = process_mock()
        controller.streamer = process_mock()
        with patch.object(controller, "_restart_gnome") as restart:
            controller._streamer_finished(1, None, 6, old_process)
        restart.assert_not_called()

    def test_stale_streamer_output_is_ignored(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.input_launched = False
        controller.streamer_buffer = ""
        old_process = process_mock()
        old_process.readAllStandardOutput.return_value = b"[GStreamer] PID: 999\n"
        controller.streamer = process_mock()
        controller._read_streamer(6, old_process)
        self.assertEqual(controller.gst_pids, set())
        self.assertEqual(controller.streamer_buffer, "")

    def test_invalid_stream_settings_are_sanitized_before_start(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        with patch.object(controller, "_prepare_display"):
            controller.start("1x99999", "bad", "nope", "Bogus", "Bogus", False)
        self.assertEqual((controller.width, controller.height), (320, 4320))
        self.assertEqual(controller.fps, 60)
        self.assertEqual(controller.bitrate, 8000)
        self.assertEqual(controller.display_type, "Extend")

    def test_invalid_third_stream_settings_are_sanitized_before_start(self):
        controller = ThirdStreamController()
        process = process_mock()
        with (
            patch("gui.third_stream_controller.kill_patterns"),
            patch("gui.third_stream_controller.QProcess", return_value=process),
            patch("gui.third_stream_controller.QTimer.singleShot"),
        ):
            controller.start("bad", "nope", "-1", "Bogus", False)
        self.assertEqual((controller.width, controller.height), (1920, 1080))
        self.assertEqual(controller.fps, 60)
        self.assertEqual(controller.bitrate, 500)

    def test_kde_streamer_waits_for_virtual_display_readiness(self):
        controller = self.kde_controller()
        krfb = process_mock()
        with (
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch.object(controller, "_kde_virtual_display_visible", side_effect=[False, True]),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller._prepare_display()
            launch.assert_not_called()
            controller._check_kde_virtual_display()
            launch.assert_not_called()
            controller._check_kde_virtual_display()
        launch.assert_called_once_with(3)
        controller.kde_ready_timer.stop()

    def test_kde_early_krfb_exit_fails_before_portal_picker(self):
        controller = self.kde_controller()
        krfb = process_mock()
        controller.krfb = krfb
        controller.kde_ready_generation = controller.generation
        controller.kde_ready_process = krfb
        with (
            patch.object(controller, "_launch_streamer") as launch,
            patch("gui.streaming_controller.stop_processes") as stop,
        ):
            controller._krfb_finished(1, None, controller.generation, krfb)
        launch.assert_not_called()
        stop.assert_called()
        self.assertFalse(controller.streaming)
        self.assertEqual(controller.status, "KDE virtual display did not stay active")

    def test_kde_stale_krfb_exit_is_ignored(self):
        controller = self.kde_controller()
        controller.krfb = process_mock()
        old_process = process_mock()
        controller.status = "unchanged"
        with patch("gui.streaming_controller.stop_processes") as stop:
            controller._krfb_finished(1, None, controller.generation - 1, old_process)
        stop.assert_not_called()
        self.assertTrue(controller.streaming)
        self.assertEqual(controller.status, "unchanged")

    def test_kde_prepare_display_does_not_killall_krfb(self):
        controller = self.kde_controller()
        krfb = process_mock()
        with (
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch("gui.streaming_controller.subprocess.run") as run,
        ):
            controller._prepare_display()
        self.assertFalse(any(
            call.args and call.args[0] == ["killall", "krfb-virtualmonitor"]
            for call in run.call_args_list
        ))
        controller.kde_ready_timer.stop()

    def test_kde_stop_terminates_tracked_krfb(self):
        controller = self.kde_controller()
        krfb = process_mock()
        streamer = process_mock()
        controller.krfb = krfb
        controller.streamer = streamer
        with (
            patch("gui.streaming_controller.stop_processes") as stop,
            patch("gui.streaming_controller.kill_patterns"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        self.assertIs(stop.call_args.args[0], krfb)
        self.assertIs(stop.call_args.args[1], streamer)


class ThirdStreamControllerTest(unittest.TestCase):
    def test_stale_delayed_launch_is_ignored(self):
        controller = ThirdStreamController()
        controller.active = True
        controller.generation = 3
        with patch("gui.third_stream_controller.QProcess") as process:
            controller._launch_streamer(generation=2)
        process.assert_not_called()

    def test_stale_streamer_output_and_finish_are_ignored(self):
        controller = ThirdStreamController()
        controller.active = True
        controller.ready = False
        controller.generation = 4
        old_process = process_mock()
        old_process.readAllStandardOutput.return_value = b"Setting pipeline to PLAYING\n"
        controller.streamer = process_mock()
        controller._read_streamer(3, old_process)
        self.assertFalse(controller.ready)
        controller._finished(0, None, generation=3, process=old_process)
        self.assertTrue(controller.active)


class ProcessUtilsTest(unittest.TestCase):
    def test_kill_patterns_does_not_call_broad_pkill(self):
        with patch("gui.process_utils.subprocess.run") as run:
            process_utils.kill_patterns("definitely-no-monitorize-process")
        run.assert_not_called()


class PipelineBuilderTest(unittest.TestCase):
    def test_launch_uses_argv_without_shell(self):
        proc = Mock()
        proc.pid = 123
        proc.wait.side_effect = TimeoutExpired("gst-launch-1.0", 0.25)
        with patch("pipeline_builder.subprocess.Popen", return_value=proc) as popen:
            pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110,
            )
        argv = popen.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertIn("gst-launch-1.0", argv)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_hardware_launch_falls_back_to_cpu_on_immediate_failure(self):
        failed = Mock()
        failed.pid = 1
        failed.returncode = 1
        failed.wait.return_value = 1
        cpu = Mock()
        cpu.pid = 2
        with patch(
            "pipeline_builder.subprocess.Popen",
            side_effect=[failed, cpu],
        ) as popen:
            result = pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, hw_encoder="vah264enc",
            )
        self.assertIs(result, cpu)
        first_argv = popen.call_args_list[0].args[0]
        second_argv = popen.call_args_list[1].args[0]
        self.assertIn("vah264enc", first_argv)
        self.assertIn("x264enc", second_argv)


class UsbControllerTest(unittest.TestCase):
    def test_adb_sequence_preserves_video_and_touch_forwarding(self):
        controller = UsbController()
        calls = []
        controller._run = lambda args, callback: calls.append((args, callback))
        controller.start()
        self.assertEqual(calls[0][0], ["devices"])
        controller._devices_done(0, None)
        self.assertEqual(
            calls[1][0], ["reverse", "tcp:7110", "tcp:7112"]
        )
        controller._video_done(0, None)
        self.assertEqual(
            calls[2][0], ["reverse", "tcp:7111", "tcp:7111"]
        )
        controller._touch_done(0, None)
        self.assertEqual(controller.status, "Device ready!")
        self.assertFalse(controller.busy)


class BackendFacadeTest(unittest.TestCase):
    def test_qml_api_remains_exposed(self):
        with patch("gui.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        properties = {
            backend.metaObject().property(index).name()
            for index in range(backend.metaObject().propertyCount())
        }
        methods = {
            backend.metaObject().method(index).name().data().decode()
            for index in range(backend.metaObject().methodCount())
        }
        self.assertTrue({
            "detectedDe", "localIp", "isStreaming", "isReceiving",
            "discoveredDevices", "pairingCode", "secondStreamActive",
        } <= properties)
        self.assertTrue({
            "startStreaming", "stopStreaming", "connectToHost",
            "startHostDiscovery", "startUsbScan", "startSecondStream",
        } <= methods)
        backend.network_timer.stop()

    def test_backend_rejects_invalid_manual_connect(self):
        with patch("gui.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        with patch.object(backend.receiver, "connect") as connect:
            backend.connectToHost("", 7110, False, "", "", "Software")
            backend.connectToHost("host", 70000, False, "", "", "Software")
        connect.assert_not_called()
        self.assertEqual(backend.receiver.status, "Invalid host or port")
        backend.network_timer.stop()


if __name__ == "__main__":
    unittest.main()
