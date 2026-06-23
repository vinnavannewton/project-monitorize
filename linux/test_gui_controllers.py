import json
import os
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
import portal_streamer
from gui import app_log, autostart, kde_virtual_monitor, process_utils, settings
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


    def test_encrypted_advertisement_declares_udp_input_transport(self):
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
        with (
            patch.dict(sys.modules, {"zeroconf": fake_module}),
            patch("tls_proxy.certificate_fingerprint", return_value="FP"),
        ):
            service.advertise("127.0.0.1", True, False)
        self.assertEqual(registered[0].properties["input_transport"], "udp-aesgcm-v1")

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


class AppLogTest(unittest.TestCase):
    def test_log_is_persisted_immediately_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitorize.log"
            app_log.configure(path)
            app_log.write("STREAMER", "first line\nsecond line")
            content = path.read_text(encoding="utf-8")
            self.assertIn("[STREAMER] first line", content)
            self.assertIn("[STREAMER] second line", content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            app_log.close()


class AutostartTest(unittest.TestCase):
    def test_autostart_uses_installed_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            data_home = Path(directory) / "data"
            app_dir = data_home / "applications"
            app_dir.mkdir(parents=True)
            (app_dir / "monitorize.desktop").write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Monitorize\n"
                "Exec=/opt/monitorize/start\n"
                "StartupNotify=true\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_DATA_HOME": str(data_home),
            }):
                self.assertEqual(autostart.set_enabled(True), "")
                content = autostart.autostart_path().read_text(encoding="utf-8")
                self.assertIn("Exec=/opt/monitorize/start --start-in-tray", content)
                self.assertIn("StartupNotify=false", content)
                self.assertIn("X-GNOME-Autostart-enabled=true", content)
                self.assertTrue(autostart.is_enabled())
                self.assertEqual(autostart.set_enabled(False), "")
                self.assertFalse(autostart.autostart_path().exists())

    def test_autostart_falls_back_when_installed_entry_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "XDG_DATA_HOME": str(Path(directory) / "data"),
            }):
                self.assertEqual(autostart.set_enabled(True), "")
                content = autostart.autostart_path().read_text(encoding="utf-8")
        self.assertIn("venv/bin/python3", content)
        self.assertIn("monitorize_gui.py", content)
        self.assertIn("--start-in-tray", content)

    def test_autostart_disabled_entries_are_not_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(directory)}):
                path = autostart.autostart_path()
                path.parent.mkdir(parents=True)
                path.write_text(
                    "[Desktop Entry]\nExec=/bin/true\nHidden=true\n",
                    encoding="utf-8",
                )
                self.assertFalse(autostart.is_enabled())
                path.write_text(
                    "[Desktop Entry]\n"
                    "Exec=/bin/true\n"
                    "X-GNOME-Autostart-enabled=false\n",
                    encoding="utf-8",
                )
                self.assertFalse(autostart.is_enabled())


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
                self.assertEqual(loaded["bitrate"], "250")
                self.assertEqual(loaded["encoder"], "Software (CPU / x264enc)")
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_presets_round_trip_and_limit_to_four(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                presets = []
                for index in range(5):
                    presets.append({
                        "version": 1,
                        "name": f"Preset {index}",
                        "mode": "wifi",
                        "primary": {
                            "resolution": "2560x1600",
                            "fps": "60",
                            "bitrate": "14000",
                            "display_type": "Extend",
                            "encoder": "Intel/AMD VA-API (vah264enc)",
                        },
                        "wifi": {
                            "stream_type": "Speed",
                            "use_encryption": True,
                        },
                        "general": {
                            "minimize_to_tray": True,
                            "enable_touch": True,
                            "enable_stylus_features": False,
                        },
                        "third": {"enabled": False},
                    })
                settings.save_presets(presets)
                loaded = settings.load_presets()
                self.assertEqual(len(loaded), 4)
                self.assertEqual(loaded[0]["name"], "Preset 0")
                self.assertTrue(loaded[0]["wifi"]["use_encryption"])
                self.assertTrue(loaded[0]["general"]["minimize_to_tray"])
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_corrupt_presets_are_ignored(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings._save_group("presets", {
                    "items": json.dumps([
                        {"version": 99, "name": "Old"},
                        "not-a-preset",
                    ])
                })
                self.assertEqual(settings.load_presets(), [])
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

    def test_encrypted_wifi_input_uses_local_udp(self):
        controller = self.kde_controller()
        controller.encrypted = True
        process = process_mock()
        with (
            patch("gui.streaming_controller.QProcess", return_value=process),
            patch(
                "gui.streaming_controller.load_general_settings",
                return_value={"enable_touch": True, "enable_stylus_features": False},
            ),
        ):
            controller._launch_input(generation=3)
        args = process.start.call_args.args[1]
        self.assertIn("--wifi", args)
        self.assertIn("--local-udp", args)

    def test_runtime_general_settings_override_saved_defaults(self):
        controller = self.kde_controller()
        controller.runtime_general = {
            "enable_touch": False,
            "enable_stylus_features": False,
            "minimize_to_tray": True,
        }
        with (
            patch("gui.streaming_controller.load_general_settings") as load,
            patch("gui.streaming_controller.QProcess") as process,
        ):
            controller._launch_input(generation=3)
        load.assert_not_called()
        process.assert_not_called()

    def test_primary_ready_launches_saved_third_display(self):
        controller = self.kde_controller()
        controller.pending_third = {
            "resolution": "1920x1080",
            "fps": "60",
            "bitrate": "8000",
            "encoder": "Software (CPU / x264enc)",
        }
        with patch.object(controller, "start_third") as start:
            controller._set_primary_ready(True)
        start.assert_called_once_with(
            "1920x1080", "60", "8000", "Software (CPU / x264enc)"
        )
        self.assertIsNone(controller.pending_third)

    def test_active_configuration_includes_running_third_display(self):
        controller = self.kde_controller()
        controller.encoder = "Intel/AMD VA-API (vah264enc)"
        controller.env.value.return_value = "Speed"
        controller.runtime_general = {
            "minimize_to_tray": True,
            "enable_touch": True,
            "enable_stylus_features": True,
        }
        controller.third.active = True
        controller.third.width = 1280
        controller.third.height = 800
        controller.third.fps = 60
        controller.third.bitrate = 6000
        controller.third.encoder = "Software (CPU / x264enc)"
        config = controller.active_configuration()
        self.assertEqual(config["primary"]["resolution"], "1920x1200")
        self.assertEqual(config["wifi"]["stream_type"], "Speed")
        self.assertEqual(config["third"]["resolution"], "1280x800")
        self.assertTrue(config["general"]["enable_stylus_features"])

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

    def test_kde_virtual_portal_starts_input_after_pipewire_node(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.input_launched = False
        controller.env = Mock()
        controller.env.value.return_value = "4"
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[Portal] Virtual output ready name=Virtual-1 mode=1920x1200@60\n"
            b"[Portal] Got PipeWire node=42 fd=9\n"
        )
        controller.streamer = process
        with patch("gui.streaming_controller.QTimer.singleShot") as single_shot:
            controller._read_streamer(7, process)
            self.assertTrue(controller.input_launched)
            single_shot.assert_called_once()
        controller.env.insert.assert_called_with("MONITORIZE_OUTPUT", "Virtual-1")

    def test_kde_portal_crash_before_pipewire_node_reports_retryable_error(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "4"
        controller.streamer = process_mock()
        with (
            patch("gui.streaming_controller.stop_processes"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch("gui.streaming_controller.kill_patterns"),
        ):
            controller._streamer_finished(
                1, None, controller.generation, controller.streamer
            )
        self.assertFalse(controller.streaming)
        self.assertEqual(
            controller.status,
            "KDE portal crashed while starting screen capture. Try again.",
        )

    def test_kde_portal_explicit_error_is_not_overwritten_on_exit(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "4"
        controller.streamer = process_mock()
        controller.kde_portal_terminal_error = True
        controller.status = "KDE portal selection was cancelled or denied"
        with (
            patch("gui.streaming_controller.stop_processes"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch("gui.streaming_controller.kill_patterns"),
        ):
            controller._streamer_finished(
                1, None, controller.generation, controller.streamer
            )
        self.assertEqual(
            controller.status, "KDE portal selection was cancelled or denied"
        )

    def test_kde_legacy_virtual_mode_apply_runs_even_if_custom_mode_exists(self):
        controller = self.kde_controller()
        controller.env.value.return_value = ""

        def fake_run(args, **_kwargs):
            if "addCustomMode" in args[1]:
                return Mock(returncode=1, stdout="", stderr="mode already exists")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("gui.streaming_controller.subprocess.run", side_effect=fake_run) as run:
            controller._configure_legacy_kde_display(controller.generation)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            ["kscreen-doctor", "output.Virtual-monitorize.mode.1920x1200@60"],
            commands,
        )
        self.assertIn(
            ["kscreen-doctor", "output.Virtual-monitorize.scale.1.0"],
            commands,
        )

    def test_kde_portal_output_ready_rejects_non_virtual_name(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.env = Mock()
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[Portal] Virtual output ready name=eDP-1 mode=1920x1200@60\n"
        )
        controller.streamer = process
        controller._read_streamer(7, process)
        controller.env.insert.assert_not_called()

    def test_invalid_stream_settings_are_sanitized_before_start(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        with patch.object(controller, "_prepare_display"):
            controller.start("1x99999", "bad", "nope", "Bogus", "Bogus", False)
        self.assertEqual((controller.width, controller.height), (320, 4320))
        self.assertEqual(controller.fps, 60)
        self.assertEqual(controller.bitrate, 8000)
        self.assertEqual(controller.display_type, "Extend")

    def test_start_does_not_emit_false_when_already_stopped(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("gui.streaming_controller.stop_processes"),
            patch("gui.streaming_controller.kill_patterns"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start("1280x800", "60", "8000", "Extend", "Software", False)
        self.assertEqual(events, [True])

    def test_kde_extend_start_uses_portal_virtual_source(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("gui.streaming_controller.stop_processes"),
            patch("gui.streaming_controller.kill_patterns"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_use_legacy_kde_krfb", return_value=False),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.start("1280x800", "60", "8000", "Extend", "Software", False)
        self.assertEqual(events, [True])
        self.assertTrue(controller.streaming)
        controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE")
        self.assertEqual(controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "4")
        launch.assert_called_once_with()

    def test_kde_extend_start_uses_krfb_before_kde_67(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        krfb = process_mock()
        with (
            patch("gui.streaming_controller.stop_processes"),
            patch("gui.streaming_controller.kill_patterns"),
            patch("gui.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch("gui.kde_virtual_monitor.detect_kde_version", return_value=(6, 6, 5)),
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.start("1280x800", "60", "8000", "Extend", "Software", False)
        launch.assert_not_called()
        args = krfb.start.call_args.args[1]
        self.assertEqual(args[args.index("--name") + 1], "monitorize")

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
        self.assertEqual(controller.bitrate, 250)

    def test_kde_streamer_waits_for_virtual_display_readiness(self):
        controller = self.kde_controller()
        krfb = process_mock()
        with (
            patch.dict(os.environ, {"MONITORIZE_KDE_USE_KRFB": "1"}),
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch.object(controller, "_kde_virtual_display_visible", side_effect=[False, True]),
            patch.object(controller, "_configure_legacy_kde_display") as configure,
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller._prepare_display()
            launch.assert_not_called()
            controller._check_kde_virtual_display()
            launch.assert_not_called()
            controller._check_kde_virtual_display()
            launch.assert_not_called()
            configure.assert_called_once()
            configure.call_args.kwargs["on_done"]()
            launch.assert_called_once_with(3)
        controller.kde_ready_timer.stop()

    def test_kde_readiness_accepts_new_output_with_different_name(self):
        controller = self.kde_controller()
        controller.kde_output_baseline = {"eDP-1"}
        with patch(
            "gui.streaming_controller.active_kde_output_names",
            return_value={"eDP-1", "Virtual-1"},
        ):
            self.assertTrue(controller._kde_virtual_display_visible())

    def test_kde_readiness_falls_back_when_kscreen_is_unusable(self):
        controller = self.kde_controller()
        controller.kde_ready_fallback_at = 0
        with (
            patch("gui.streaming_controller.active_kde_output_names", return_value=set()),
            patch("gui.streaming_controller.QGuiApplication.instance", return_value=None),
        ):
            self.assertTrue(controller._kde_virtual_display_visible())

    def test_kde_readiness_falls_back_when_output_probe_fails(self):
        controller = self.kde_controller()
        controller.kde_ready_fallback_at = 0
        with (
            patch(
                "gui.streaming_controller.active_kde_output_names",
                side_effect=OSError("no display"),
            ),
            patch("gui.streaming_controller.QGuiApplication.instance", return_value=None),
        ):
            self.assertTrue(controller._kde_virtual_display_visible())

    def test_kde_startup_failure_before_streaming_does_not_emit_false(self):
        controller = self.kde_controller()
        controller.streaming = False
        events = []
        controller.streamingChanged.connect(events.append)
        krfb = process_mock()
        controller.krfb = krfb
        controller.kde_ready_generation = controller.generation
        controller.kde_ready_process = krfb
        with patch("gui.streaming_controller.stop_processes") as stop:
            controller._krfb_finished(1, None, controller.generation, krfb)
        stop.assert_called()
        self.assertEqual(events, [])
        self.assertFalse(controller.streaming)
        self.assertEqual(controller.status, "KDE virtual display did not stay active")

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
            patch.dict(os.environ, {"MONITORIZE_KDE_USE_KRFB": "1"}),
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch("gui.streaming_controller.subprocess.run") as run,
        ):
            controller._prepare_display()
        self.assertFalse(any(
            call.args and call.args[0] == ["killall", "krfb-virtualmonitor"]
            for call in run.call_args_list
        ))
        controller.kde_ready_timer.stop()

    def test_kde_prepare_display_uses_available_virtual_monitor_port(self):
        controller = self.kde_controller()
        krfb = process_mock()
        with (
            patch.dict(os.environ, {"MONITORIZE_KDE_USE_KRFB": "1"}),
            patch("gui.streaming_controller.QProcess", return_value=krfb),
            patch.object(controller, "_allocate_kde_virtual_monitor_port", return_value="5999"),
        ):
            controller._prepare_display()
        args = krfb.start.call_args.args[1]
        self.assertEqual(args[args.index("--port") + 1], "5999")
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


class KdeVirtualMonitorCompatTest(unittest.TestCase):
    @staticmethod
    def portal_outputs(mode_registered=False, mode_active=False):
        modes = [
            {
                "id": "1",
                "name": "1920x1080@60",
                "refreshRate": 60.0,
                "size": {"width": 1920, "height": 1080},
            }
        ]
        if mode_registered:
            modes.append({
                "id": "2",
                "name": "1920x1200@60",
                "refreshRate": 59.885,
                "size": {"width": 1920, "height": 1200},
            })
        return [
            {
                "id": 1,
                "name": "Virtual-virtual-xdp-kde-monitorize",
                "connected": True,
                "enabled": True,
                "priority": 2,
                "currentModeId": "2" if mode_active else "1",
                "scale": 1.5,
                "rotation": 8,
                "modes": modes,
            },
            {
                "id": 2,
                "name": "eDP-1",
                "connected": True,
                "enabled": True,
                "priority": 1,
                "scale": 1.5,
                "modes": [],
            },
        ]

    def test_portal_output_detection_uses_name_when_ids_are_reused(self):
        output = kde_virtual_monitor._new_portal_virtual_output(
            {"eDP-1"},
            self.portal_outputs(),
        )
        self.assertEqual(output["name"], "Virtual-virtual-xdp-kde-monitorize")
        self.assertEqual(output["id"], 1)

    def test_portal_mode_registration_selects_discovered_mode_id(self):
        state = {"registered": False, "active": False}

        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                outputs = self.portal_outputs(
                    mode_registered=state["registered"],
                    mode_active=state["active"],
                )
                return Mock(
                    returncode=0,
                    stdout=json.dumps({"outputs": outputs}),
                    stderr="",
                )
            if "addCustomMode.1920.1200.60000.full" in args[1]:
                state["registered"] = True
                return Mock(returncode=0, stdout="", stderr="")
            if args[1].endswith(".mode.2"):
                state["active"] = True
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {args}")

        with (
            patch(
                "gui.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ) as run,
            patch("gui.kde_virtual_monitor.time.sleep"),
        ):
            ok, output_name, message = (
                kde_virtual_monitor.configure_portal_virtual_output(
                    {"eDP-1"},
                    1920,
                    1200,
                    60,
                    attempts=2,
                    delay=0,
                )
            )

        self.assertTrue(ok, message)
        self.assertEqual(output_name, "Virtual-virtual-xdp-kde-monitorize")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "kscreen-doctor",
                (
                    "output.Virtual-virtual-xdp-kde-monitorize."
                    "addCustomMode.1920.1200.60000.full"
                ),
            ],
            commands,
        )
        self.assertIn(
            [
                "kscreen-doctor",
                "output.Virtual-virtual-xdp-kde-monitorize.mode.2",
            ],
            commands,
        )
        self.assertFalse(any(".scale." in " ".join(command) for command in commands))
        self.assertFalse(any("output.eDP-1" in " ".join(command) for command in commands))

    def test_portal_mode_configuration_rejects_ambiguous_virtual_outputs(self):
        outputs = self.portal_outputs()
        outputs.insert(1, {
            "id": 3,
            "name": "Virtual-other",
            "connected": True,
            "enabled": True,
            "priority": 3,
            "modes": [],
        })
        with (
            patch(
                "gui.kde_virtual_monitor.kde_outputs",
                return_value=outputs,
            ),
            patch("gui.kde_virtual_monitor.time.sleep"),
            patch("gui.kde_virtual_monitor.subprocess.run") as run,
        ):
            ok, output_name, _message = (
                kde_virtual_monitor.configure_portal_virtual_output(
                    {"eDP-1"},
                    1920,
                    1200,
                    60,
                    attempts=1,
                    delay=0,
                )
            )
        self.assertFalse(ok)
        self.assertEqual(output_name, "")
        run.assert_not_called()

    def test_kde_version_parser_handles_common_outputs(self):
        self.assertEqual(
            kde_virtual_monitor.parse_kde_version("kwin 6.6.5"),
            (6, 6, 5),
        )
        self.assertEqual(
            kde_virtual_monitor.parse_kde_version("plasmashell 6.7.0"),
            (6, 7, 0),
        )
        self.assertIsNone(kde_virtual_monitor.parse_kde_version("not a version"))

    def test_kde_version_gate_uses_krfb_before_67(self):
        with (
            patch.dict(os.environ, {
                "MONITORIZE_KDE_FORCE_PORTAL": "",
                "MONITORIZE_KDE_USE_KRFB": "",
            }),
            patch("gui.kde_virtual_monitor.detect_kde_version", return_value=(6, 6, 5)),
        ):
            self.assertTrue(kde_virtual_monitor.should_use_legacy_krfb())

    def test_kde_version_gate_uses_portal_at_67(self):
        with (
            patch.dict(os.environ, {
                "MONITORIZE_KDE_FORCE_PORTAL": "",
                "MONITORIZE_KDE_USE_KRFB": "",
            }),
            patch("gui.kde_virtual_monitor.detect_kde_version", return_value=(6, 7, 0)),
        ):
            self.assertFalse(kde_virtual_monitor.should_use_legacy_krfb())

    def test_kde_unknown_version_prefers_available_portal_virtual_source(self):
        with (
            patch.dict(os.environ, {
                "MONITORIZE_KDE_FORCE_PORTAL": "",
                "MONITORIZE_KDE_USE_KRFB": "",
            }),
            patch("gui.kde_virtual_monitor.detect_kde_version", return_value=None),
            patch("gui.kde_virtual_monitor.portal_virtual_source_available", return_value=True),
        ):
            self.assertFalse(kde_virtual_monitor.should_use_legacy_krfb())

    def test_forced_krfb_warns_on_kde_67(self):
        warnings = []
        with (
            patch.dict(os.environ, {"MONITORIZE_KDE_USE_KRFB": "1"}),
            patch("gui.kde_virtual_monitor.detect_kde_version", return_value=(6, 7, 0)),
        ):
            self.assertTrue(kde_virtual_monitor.should_use_legacy_krfb(warnings.append))
        self.assertTrue(any("KDE 6.7+" in item for item in warnings))

    def test_creates_alias_when_krfb_desktop_id_mismatches_binary_app_id(self):
        with tempfile.TemporaryDirectory() as data_home, tempfile.TemporaryDirectory() as data_dir:
            applications = Path(data_dir) / "applications"
            applications.mkdir()
            source = applications / "org.kde.krfb.virtualmonitor.desktop"
            source.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Exec=/usr/bin/krfb-virtualmonitor\n"
                "NoDisplay=true\n",
                encoding="utf-8",
            )
            env = {"XDG_DATA_HOME": data_home, "XDG_DATA_DIRS": data_dir}
            with (
                patch.dict(os.environ, env),
                patch("gui.kde_virtual_monitor.shutil.which", return_value="/usr/bin/kbuildsycoca6"),
                patch("gui.kde_virtual_monitor.subprocess.run") as run,
            ):
                message = kde_virtual_monitor.ensure_krfb_virtualmonitor_desktop_entry()
            alias = (
                Path(data_home)
                / "applications"
                / "org.kde.krfb-virtualmonitor.desktop"
            )
            self.assertTrue(alias.exists())
            content = alias.read_text(encoding="utf-8")
            self.assertIn("Exec=/usr/bin/krfb-virtualmonitor", content)
            self.assertIn("X-Monitorize-CompatibilityAlias=true", content)
            self.assertIn(str(alias), message)
            run.assert_called()

    def test_alias_creation_is_noop_when_exact_desktop_id_exists(self):
        with tempfile.TemporaryDirectory() as data_home:
            applications = Path(data_home) / "applications"
            applications.mkdir()
            alias = applications / "org.kde.krfb-virtualmonitor.desktop"
            alias.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": data_home, "XDG_DATA_DIRS": ""}),
                patch("gui.kde_virtual_monitor.subprocess.run") as run,
            ):
                self.assertIsNone(
                    kde_virtual_monitor.ensure_krfb_virtualmonitor_desktop_entry()
                )
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
        config_interval_args = [
            arg for arg in argv if arg.startswith("config-interval=")
        ]
        self.assertEqual(["config-interval=1"], config_interval_args)
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


class PortalStreamerTest(unittest.TestCase):
    def test_prepares_virtual_output_before_opening_pipewire(self):
        events = []
        screen_cast = Mock()
        screen_cast.CreateSession.return_value = "/request/create"
        screen_cast.SelectSources.return_value = "/request/select"
        screen_cast.Start.return_value = "/request/start"
        screen_cast.OpenPipeWireRemote.side_effect = lambda *_args: (
            events.append("open-pipewire")
            or Mock(take=Mock(return_value=9))
        )
        session_interface = Mock()

        class FakeBus:
            callback = None

            def get_object(self, _service, _path):
                return Mock()

            def add_signal_receiver(self, callback, **_kwargs):
                self.callback = callback

        bus = FakeBus()

        class FakeLoop:
            def run(self):
                bus.callback(0, {"session_handle": "/ignored"}, path="/request/other")
                bus.callback(0, {"session_handle": "/session"}, path="/request/create")
                bus.callback(0, {"streams": [(99, {})]}, path="/request/other")
                bus.callback(0, {}, path="/request/select")
                bus.callback(0, {"streams": [(42, {})]}, path="/request/start")

            def is_running(self):
                return False

            def quit(self):
                pass

        class FakeThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                events.append("gstreamer-thread")

        def fake_interface(_object, interface_name):
            if interface_name == "org.freedesktop.portal.ScreenCast":
                return screen_cast
            return session_interface

        def prepare():
            events.append("prepare-mode")
            return True, "Virtual-1", "configured"

        with (
            patch("portal_streamer.DBusGMainLoop"),
            patch("portal_streamer.dbus.SessionBus", return_value=bus),
            patch("portal_streamer.dbus.Interface", side_effect=fake_interface),
            patch("portal_streamer.GLib.MainLoop", return_value=FakeLoop()),
            patch("portal_streamer.threading.Thread", FakeThread),
            patch("portal_streamer.signal.signal"),
            patch(
                "portal_streamer.secrets.token_hex",
                side_effect=["a1", "b2", "c3", "d4"],
            ),
        ):
            portal_streamer.run_portal_streamer(
                "KDE",
                "Create virtual screen",
                1920,
                1200,
                60,
                8000,
                "wifi",
                7110,
                "vah264enc",
                "127.0.0.1",
                source_type=4,
                prepare_stream=prepare,
            )

        self.assertLess(events.index("prepare-mode"), events.index("open-pipewire"))
        self.assertLess(
            events.index("open-pipewire"),
            events.index("gstreamer-thread"),
        )
        self.assertEqual(screen_cast.SelectSources.call_count, 1)
        self.assertEqual(screen_cast.SelectSources.call_args.args[0], "/session")
        self.assertEqual(screen_cast.Start.call_count, 1)
        self.assertEqual(screen_cast.OpenPipeWireRemote.call_count, 1)
        create_options = screen_cast.CreateSession.call_args.args[0]
        select_options = screen_cast.SelectSources.call_args.args[1]
        start_options = screen_cast.Start.call_args.args[2]
        self.assertEqual(str(create_options["handle_token"]), "create_a1")
        self.assertEqual(str(create_options["session_handle_token"]), "session_b2")
        self.assertEqual(str(select_options["handle_token"]), "select_c3")
        self.assertEqual(str(start_options["handle_token"]), "start_d4")


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
            "presets", "presetLaunchStatus",
        } <= properties)
        self.assertTrue({
            "startStreaming", "stopStreaming", "connectToHost",
            "startHostDiscovery", "startUsbScan", "startSecondStream",
            "saveCurrentPreset", "launchPreset", "renamePreset", "deletePreset",
            "isAutostartEnabled", "setAutostartEnabled",
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

    def test_usb_preset_scans_before_launching(self):
        preset = {
            "version": 1,
            "name": "Desk",
            "mode": "usb",
            "primary": {
                "resolution": "1920x1200",
                "fps": "60",
                "bitrate": "8000",
                "display_type": "Extend",
                "encoder": "Software (CPU / x264enc)",
            },
            "general": {
                "minimize_to_tray": False,
                "enable_touch": True,
                "enable_stylus_features": False,
            },
            "third": {"enabled": False},
        }
        with (
            patch("gui.backend.get_local_ip", return_value="127.0.0.1"),
            patch("gui.backend.load_presets", return_value=[preset]),
        ):
            backend = MonitorizeBackend("kde")
        with (
            patch.object(backend.usb, "start") as scan,
            patch.object(backend.streaming, "start") as start,
        ):
            backend.launchPreset(0)
            scan.assert_called_once()
            start.assert_not_called()
            backend._finish_usb_preset_launch(True)
            start.assert_called_once()
        backend.network_timer.stop()

    def test_preset_launch_does_not_override_global_tray_setting(self):
        preset = {
            "version": 1,
            "name": "Desk",
            "mode": "wifi",
            "primary": {
                "resolution": "1920x1200",
                "fps": "60",
                "bitrate": "8000",
                "display_type": "Extend",
                "encoder": "Software (CPU / x264enc)",
            },
            "wifi": {"stream_type": "Speed", "use_encryption": True},
            "general": {
                "minimize_to_tray": False,
                "enable_touch": True,
                "enable_stylus_features": False,
            },
            "third": {"enabled": False},
        }
        with (
            patch("gui.backend.get_local_ip", return_value="127.0.0.1"),
            patch("gui.backend.load_presets", return_value=[preset]),
        ):
            backend = MonitorizeBackend("kde")
        with (
            patch.object(backend.streaming, "start") as start,
            patch(
                "gui.backend.load_general_settings",
                return_value={"minimize_to_tray": True},
            ),
        ):
            backend.launchPreset(0)
            start.assert_called_once()
            self.assertTrue(backend.should_minimize_to_tray())
        backend.network_timer.stop()

    def test_backend_autostart_slots_delegate_to_helper(self):
        with patch("gui.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        with (
            patch("gui.backend.autostart.is_enabled", return_value=True) as enabled,
            patch("gui.backend.autostart.set_enabled", return_value="") as set_enabled,
        ):
            self.assertTrue(backend.isAutostartEnabled())
            self.assertEqual(backend.setAutostartEnabled(False), "")
        enabled.assert_called_once()
        set_enabled.assert_called_once_with(False)
        backend.network_timer.stop()

    def test_start_in_tray_hides_initial_window_when_tray_is_available(self):
        from gui.main_window import _show_initial_window

        window = Mock()
        window.tray = Mock()
        with (
            patch("gui.main_window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True),
            patch("gui.main_window.QApplication.setQuitOnLastWindowClosed") as set_quit,
        ):
            shown = _show_initial_window(window, True)
        self.assertFalse(shown)
        window.tray.show.assert_called_once()
        window.show.assert_not_called()
        set_quit.assert_called_once_with(False)

    def test_start_in_tray_falls_back_when_tray_is_unavailable(self):
        from gui.main_window import _show_initial_window

        window = Mock()
        window.tray = Mock()
        with patch(
            "gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        ):
            shown = _show_initial_window(window, True)
        self.assertTrue(shown)
        window.show.assert_called_once()
        window.tray.show.assert_not_called()

    def test_close_event_minimizes_to_tray_even_when_not_streaming(self):
        from gui.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = False
        window.tray = Mock()
        event = Mock()
        with patch(
            "gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        window.hide.assert_called_once()
        window.tray.show.assert_called_once()
        window._quit_app.assert_not_called()

    def test_close_event_minimizes_to_tray_while_streaming(self):
        from gui.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = True
        window.tray = Mock()
        event = Mock()
        with patch(
            "gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        window.tray.show.assert_called_once()
        window._quit_app.assert_not_called()

    def test_close_event_quits_when_minimize_to_tray_is_disabled(self):
        from gui.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = False
        event = Mock()
        MonitorizeWindow.closeEvent(window, event)
        window._quit_app.assert_called_once()
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_close_event_quits_when_tray_is_unavailable(self):
        from gui.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        event = Mock()
        with patch(
            "gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        ):
            MonitorizeWindow.closeEvent(window, event)
        window._quit_app.assert_called_once()
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_save_current_preset_replaces_selected_slot(self):
        existing = {
            "version": 1,
            "name": "Old",
            "mode": "usb",
            "primary": {},
            "general": {},
            "third": {"enabled": False},
        }
        snapshot = {
            "version": 1,
            "mode": "wifi",
            "primary": {
                "resolution": "2560x1600",
                "fps": "60",
                "bitrate": "14000",
                "display_type": "Extend",
                "encoder": "Intel/AMD VA-API (vah264enc)",
            },
            "wifi": {"stream_type": "Speed", "use_encryption": True},
            "general": {
                "minimize_to_tray": True,
                "enable_touch": True,
                "enable_stylus_features": False,
            },
            "third": {"enabled": False},
        }
        with (
            patch("gui.backend.get_local_ip", return_value="127.0.0.1"),
            patch("gui.backend.load_presets", return_value=[existing]),
        ):
            backend = MonitorizeBackend("kde")
        backend.streaming.streaming = True
        backend.streaming.active_configuration = Mock(return_value=snapshot)
        with (
            patch("gui.backend.save_presets") as save,
            patch("gui.backend.load_presets", return_value=[
                {**snapshot, "name": "New"}
            ]),
        ):
            result = backend.saveCurrentPreset("New", 0)
        self.assertEqual(result, "")
        self.assertEqual(save.call_args.args[0][0]["name"], "New")
        backend.network_timer.stop()


if __name__ == "__main__":
    unittest.main()
