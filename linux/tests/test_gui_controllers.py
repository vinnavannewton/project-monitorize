import json
import os
import signal
import sys
import socket
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from subprocess import TimeoutExpired

from PyQt6.QtCore import QCoreApplication, QProcess, QProcessEnvironment

from monitorize.streaming import Streamer_gnome, pipeline_builder
from monitorize.streaming import portal_streamer
from monitorize.config import app_log, autostart, settings
from monitorize.platform import gnome_virtual_monitor, kde_virtual_monitor, process_utils
from monitorize.desktop.discovery_service import DiscoveryService
from monitorize.desktop.backend import MonitorizeBackend
from monitorize.desktop.receiver_controller import ReceiverController
from monitorize.desktop.streaming_controller import StreamingController
from monitorize.desktop.third_stream_controller import ThirdStreamController
from monitorize.desktop.usb_controller import UsbController
from monitorize.config.validation import (
    DEFAULT_PRIMARY_RESOLUTION,
    sanitize_encoder_profile,
    sanitize_resolution,
)


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
        fake_tls_proxy = types.SimpleNamespace(certificate_fingerprint=lambda: "FP")
        service = DiscoveryService()
        with patch.dict(sys.modules, {
            "zeroconf": fake_module,
            "monitorize.security.tls_proxy": fake_tls_proxy,
        }):
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
            patch("monitorize.desktop.discovery_service.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
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
            patch("monitorize.desktop.discovery_service.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
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
                self.assertIn("Exec=/opt/monitorize/start --tray-agent", content)
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
        self.assertIn("-m monitorize", content)
        self.assertIn("--tray-agent", content)

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

    def test_encoder_profile_defaults_to_low_latency(self):
        self.assertEqual(sanitize_encoder_profile("Bogus"), "Low Latency")


class ReceiverControllerTest(unittest.TestCase):
    def test_pipeline_keeps_low_latency_queue_and_selected_decoder(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.sink = "glimagesink"
        process = process_mock()
        with patch("monitorize.desktop.receiver_controller.QProcess", return_value=process):
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
        with patch("monitorize.desktop.receiver_controller.clear_receiver_credentials") as clear:
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
                    "encoder_profile": "Bogus",
                })
                loaded = settings.load_second_display_settings()
                self.assertEqual(loaded["fps"], "60")
                self.assertEqual(loaded["bitrate"], "250")
                self.assertEqual(loaded["encoder"], "Software (CPU / x264enc)")
                self.assertEqual(loaded["encoder_profile"], "Low Latency")
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
                            "encoder_profile": "Balanced",
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
                self.assertEqual(loaded[0]["primary"]["encoder_profile"], "Balanced")
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

    def gnome_controller(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.width = 1920
        controller.height = 1200
        controller.fps = 60
        controller.bitrate = 8000
        controller.wifi = False
        controller.encrypted = False
        controller.streaming = True
        controller.display_type = "Extend"
        controller.env = Mock()
        controller.generation = 7
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
        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._launch_streamer()
        args = process.start.call_args.args[1]
        self.assertEqual(args[-1], "HEADLESS-2")
        self.assertIn("wifi", args)
        discovery.advertise.assert_called_once_with(
            "10.0.0.1", False, False
        )

    def test_gnome_streamer_command_uses_display_type_only(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.width = 1920
        controller.height = 1200
        controller.fps = 60
        controller.bitrate = 8000
        controller.wifi = False
        controller.streaming = True
        controller.display_type = "Extend"
        controller.env = Mock()
        process = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
            patch("monitorize.desktop.streaming_controller.QTimer.singleShot"),
        ):
            controller._launch_streamer()
        args = process.start.call_args.args[1]
        self.assertEqual(args[-1:], ["Extend"])
        self.assertTrue(controller.gnome_layout_timer.isActive())
        controller.gnome_layout_timer.stop()

    def test_gnome_extend_connects_display_config_signal(self):
        controller = self.gnome_controller()
        process = process_mock()
        bus = Mock()
        bus.connect.return_value = True
        qdbus = Mock()
        qdbus.sessionBus.return_value = bus
        with (
            patch("monitorize.desktop.streaming_controller.QDBusConnection", qdbus),
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
            patch("monitorize.desktop.streaming_controller.QTimer.singleShot"),
        ):
            controller._launch_streamer()
        bus.connect.assert_called_once()
        self.assertEqual(
            bus.connect.call_args.args[:4],
            (
                "org.gnome.Mutter.DisplayConfig",
                "/org/gnome/Mutter/DisplayConfig",
                "org.gnome.Mutter.DisplayConfig",
                "MonitorsChanged",
            ),
        )
        self.assertTrue(controller.gnome_display_config_connected)
        controller._stop_gnome_layout_tracking()

    def test_gnome_mirror_and_kde_do_not_connect_display_config_signal(self):
        bus = Mock()
        qdbus = Mock()
        qdbus.sessionBus.return_value = bus

        mirror = self.gnome_controller()
        mirror.display_type = "Mirror"
        kde = self.kde_controller()
        with patch("monitorize.desktop.streaming_controller.QDBusConnection", qdbus):
            mirror._start_gnome_layout_tracking()
            kde._start_gnome_layout_tracking()
        bus.connect.assert_not_called()

    def test_stop_disconnects_gnome_display_config_signal(self):
        controller = self.gnome_controller()
        controller.streamer = process_mock()
        controller.gnome_layout_timer.start()
        controller.gnome_layout_change_timer.start()
        bus = Mock()
        controller.gnome_display_config_bus = bus
        controller.gnome_display_config_connected = True
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                return_value=True,
            ),
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        bus.disconnect.assert_called_once()
        self.assertFalse(controller.gnome_layout_timer.isActive())
        self.assertFalse(controller.gnome_layout_change_timer.isActive())
        self.assertFalse(controller.gnome_display_config_connected)

    def test_gnome_monitors_changed_ignored_when_not_tracking(self):
        controller = self.gnome_controller()
        controller.display_type = "Mirror"
        with patch.object(controller, "_save_gnome_virtual_layout") as save:
            controller._on_gnome_monitors_changed()
        save.assert_not_called()
        self.assertFalse(controller.gnome_layout_change_timer.isActive())

    def test_gnome_monitors_changed_debounces_passive_save(self):
        controller = self.gnome_controller()
        controller._on_gnome_monitors_changed()
        self.assertTrue(controller.gnome_layout_change_timer.isActive())
        controller.gnome_layout_change_timer.stop()

    def test_gnome_layout_change_save_does_not_reconnect(self):
        controller = self.gnome_controller()
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                return_value=True,
            ) as save,
            patch.object(controller, "_restart_gnome") as restart,
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.gnome_layout_change_timer.timeout.emit()
        save.assert_called_once_with("primary")
        restart.assert_not_called()
        launch.assert_not_called()

    def test_stop_cleans_processes_and_advertisement(self):
        discovery = Mock()
        controller = StreamingController("sway", "10.0.0.1", discovery)
        controller.streaming = True
        controller.streamer = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes") as stop,
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        stop.assert_called_once()
        discovery.stop_advertising.assert_called_once()
        self.assertFalse(controller.streaming)

    def test_kde_portal_stop_waits_longer_and_skips_hard_kill_when_clean(self):
        discovery = Mock()
        controller = StreamingController("kde", "10.0.0.1", discovery)
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "4"
        controller.streamer = process_mock()
        controller.input_bridge = process_mock()
        controller.gst_pids = {12345}
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes", return_value=True) as stop,
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill_pids,
            patch("monitorize.desktop.streaming_controller.kill_patterns") as kill_patterns_mock,
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        self.assertEqual(stop.call_args_list[0].kwargs, {"timeout_ms": 8000})
        kill_pids.assert_not_called()
        kill_patterns_mock.assert_not_called()
        self.assertEqual(controller.gst_pids, set())
        discovery.stop_advertising.assert_called_once()

    def test_kde_portal_stop_hard_kills_when_graceful_stop_fails(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "4"
        controller.streamer = process_mock()
        controller.gst_pids = {12345}
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes", return_value=False),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill_pids,
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        kill_pids.assert_called_once_with({12345})

    def test_stale_delayed_input_start_is_ignored(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 4
        with patch("monitorize.desktop.streaming_controller.QProcess") as process:
            controller._launch_input(generation=3)
        process.assert_not_called()

    def test_encrypted_wifi_input_uses_local_udp(self):
        controller = self.kde_controller()
        controller.encrypted = True
        process = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
            patch(
                "monitorize.desktop.streaming_controller.load_general_settings",
                return_value={"enable_touch": True, "enable_stylus_features": False},
            ),
        ):
            controller._launch_input(generation=3)
        args = process.start.call_args.args[1]
        self.assertIn("--wifi", args)
        self.assertIn("--local-udp", args)

    def test_gnome_plain_wifi_input_uses_public_udp(self):
        controller = self.gnome_controller()
        controller.wifi = True
        controller.encrypted = False
        process = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
            patch(
                "monitorize.desktop.streaming_controller.load_general_settings",
                return_value={"enable_touch": True, "enable_stylus_features": False},
            ),
        ):
            controller._launch_input(generation=7)
        args = process.start.call_args.args[1]
        self.assertIn("--wifi", args)
        self.assertNotIn("--local-udp", args)

    def test_gnome_encrypted_wifi_input_uses_local_udp(self):
        controller = self.gnome_controller()
        controller.wifi = True
        controller.encrypted = True
        process = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
            patch(
                "monitorize.desktop.streaming_controller.load_general_settings",
                return_value={"enable_touch": True, "enable_stylus_features": False},
            ),
        ):
            controller._launch_input(generation=7)
        args = process.start.call_args.args[1]
        self.assertIn("--wifi", args)
        self.assertIn("--local-udp", args)

    def test_gnome_stylus_input_args_are_preserved(self):
        controller = self.gnome_controller()
        controller.runtime_general = {
            "enable_touch": True,
            "enable_stylus_features": True,
        }
        process = process_mock()
        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._launch_input(generation=7)
        args = process.start.call_args.args[1]
        self.assertIn("--stylus-features", args)
        self.assertNotIn("--stylus-only", args)

    def test_gnome_stylus_only_input_args_are_preserved(self):
        controller = self.gnome_controller()
        controller.runtime_general = {
            "enable_touch": False,
            "enable_stylus_features": True,
        }
        process = process_mock()
        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._launch_input(generation=7)
        args = process.start.call_args.args[1]
        self.assertIn("--stylus-features", args)
        self.assertIn("--stylus-only", args)

    def test_runtime_general_settings_override_saved_defaults(self):
        controller = self.kde_controller()
        controller.runtime_general = {
            "enable_touch": False,
            "enable_stylus_features": False,
            "minimize_to_tray": True,
        }
        with (
            patch("monitorize.desktop.streaming_controller.load_general_settings") as load,
            patch("monitorize.desktop.streaming_controller.QProcess") as process,
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
            "1920x1080", "60", "8000", "Software (CPU / x264enc)",
            "Low Latency",
        )
        self.assertIsNone(controller.pending_third)

    def test_active_configuration_includes_running_third_display(self):
        controller = self.kde_controller()
        controller.encoder = "Intel/AMD VA-API (vah264enc)"
        controller.encoder_profile = "Balanced"
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
        controller.third.encoder_profile = "Quality"
        config = controller.active_configuration()
        self.assertEqual(config["primary"]["resolution"], "1920x1200")
        self.assertEqual(config["primary"]["encoder_profile"], "Balanced")
        self.assertEqual(config["wifi"]["stream_type"], "Speed")
        self.assertEqual(config["third"]["resolution"], "1280x800")
        self.assertEqual(config["third"]["encoder_profile"], "Quality")
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

    def test_restart_gnome_saves_virtual_layout_before_relaunch(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.display_type = "Extend"
        events = []
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                side_effect=lambda *_args: events.append("save") or True,
            ) as save,
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(
                controller,
                "_launch_streamer",
                side_effect=lambda *_args: events.append("launch"),
            ) as launch,
        ):
            controller._restart_gnome(7)
        save.assert_called_once_with("primary")
        launch.assert_called_once_with(7)
        self.assertEqual(events, ["save", "launch"])

    def test_restart_gnome_logs_failed_layout_save_but_relaunches(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.display_type = "Extend"
        logs = []
        controller.logAppended.connect(
            lambda label, message: logs.append((label, message))
        )

        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                return_value=False,
            ),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller._restart_gnome(7)

        launch.assert_called_once_with(7)
        self.assertIn(
            (
                "STREAMER",
                "GNOME virtual layout save failed before restart; using last saved layout.",
            ),
            logs,
        )

    def test_gnome_layout_timer_saves_while_streaming(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Extend"
        with patch(
            "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout"
        ) as save:
            controller._save_gnome_virtual_layout()
        save.assert_called_once_with("primary")

    def test_gnome_layout_timer_ignores_mirror_mode(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Mirror"
        with patch(
            "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout"
        ) as save:
            controller._save_gnome_virtual_layout()
        save.assert_not_called()

    def test_stop_saves_gnome_layout_before_stopping(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Extend"
        controller.streamer = process_mock()
        controller.gnome_layout_timer.start()
        events = []
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                side_effect=lambda *_args: events.append("save"),
            ) as save,
            patch(
                "monitorize.desktop.streaming_controller.stop_processes",
                side_effect=lambda *_args, **_kwargs: events.append("stop") or True,
            ),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        save.assert_called_once_with("primary")
        self.assertEqual(events[:2], ["save", "stop"])

    def test_stop_logs_failed_gnome_layout_save_but_stops(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Extend"
        controller.streamer = process_mock()
        logs = []
        controller.logAppended.connect(
            lambda label, message: logs.append((label, message))
        )

        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                return_value=False,
            ),
            patch("monitorize.desktop.streaming_controller.stop_processes") as stop,
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()

        stop.assert_called()
        self.assertIn(
            (
                "STREAMER",
                "GNOME virtual layout save failed before stop; using last saved layout.",
            ),
            logs,
        )
        self.assertFalse(controller.gnome_layout_timer.isActive())

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
        with patch("monitorize.desktop.streaming_controller.QTimer.singleShot") as single_shot:
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
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
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
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
        ):
            controller._streamer_finished(
                1, None, controller.generation, controller.streamer
            )
        self.assertEqual(
            controller.status, "KDE portal selection was cancelled or denied"
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
            controller.start(
                "1x99999", "bad", "nope", "Bogus", "Bogus",
                "Bogus", False,
            )
        self.assertEqual((controller.width, controller.height), (320, 4320))
        self.assertEqual(controller.fps, 60)
        self.assertEqual(controller.bitrate, 8000)
        self.assertEqual(controller.display_type, "Extend")
        self.assertEqual(controller.encoder_profile, "Low Latency")

    def test_start_does_not_emit_false_when_already_stopped(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Low Latency", False,
            )
        self.assertEqual(events, [True])

    def test_stream_start_sets_encoder_profile_environment(self):
        controller = StreamingController("sway", "10.0.0.1", Mock())
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Balanced", False,
            )
        self.assertEqual(controller.env.value("MONITORIZE_ENCODER_PROFILE"), "Balanced")

    def test_kde_extend_start_uses_portal_virtual_source(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.third, "stop"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Low Latency", False,
            )
        self.assertEqual(events, [True])
        self.assertTrue(controller.streaming)
        controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE")
        self.assertEqual(controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "4")
        launch.assert_called_once_with()

    def test_invalid_third_stream_settings_are_sanitized_before_start(self):
        controller = ThirdStreamController()
        process = process_mock()
        with (
            patch("monitorize.desktop.third_stream_controller.kill_patterns"),
            patch("monitorize.desktop.third_stream_controller.QProcess", return_value=process),
            patch("monitorize.desktop.third_stream_controller.QTimer.singleShot"),
        ):
            controller.start("bad", "nope", "-1", "Bogus", "Bogus", False)
        self.assertEqual((controller.width, controller.height), (1920, 1080))
        self.assertEqual(controller.fps, 60)
        self.assertEqual(controller.bitrate, 250)
        self.assertEqual(controller.encoder_profile, "Low Latency")


class ThirdStreamControllerTest(unittest.TestCase):
    def test_start_uses_portal_virtual_source(self):
        controller = ThirdStreamController()
        with (
            patch("monitorize.desktop.third_stream_controller.kill_patterns"),
            patch("monitorize.desktop.third_stream_controller.QTimer.singleShot") as single_shot,
        ):
            controller.start("1920x1080", "60", "8000", "Software", "Low Latency", False)
        self.assertEqual(controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "4")
        self.assertEqual(controller.env.value("MONITORIZE_VIRTUAL_SLOT"), "third")
        single_shot.assert_called_once()

    def test_virtual_output_name_is_captured_for_stop(self):
        controller = ThirdStreamController()
        controller.active = True
        controller.generation = 1
        controller.env = QProcessEnvironment.systemEnvironment()
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[Portal] Virtual output ready name=Virtual-3 mode=1920x1080@60\n"
        )
        controller.streamer = process
        controller._read_streamer(1, process)
        self.assertEqual(controller.env.value("MONITORIZE_OUTPUT"), "Virtual-3")

    def test_stop_saves_third_virtual_layout(self):
        controller = ThirdStreamController()
        controller.active = True
        controller.env = QProcessEnvironment.systemEnvironment()
        controller.env.insert("MONITORIZE_OUTPUT", "Virtual-3")
        with (
            patch("monitorize.desktop.third_stream_controller.save_current_virtual_layout") as save,
            patch("monitorize.desktop.third_stream_controller.stop_processes"),
            patch("monitorize.desktop.third_stream_controller.kill_tracked_pids"),
            patch("monitorize.desktop.third_stream_controller.kill_patterns"),
        ):
            controller.stop()
        save.assert_called_once_with("third", "Virtual-3")

    def test_stale_delayed_launch_is_ignored(self):
        controller = ThirdStreamController()
        controller.active = True
        controller.generation = 3
        with patch("monitorize.desktop.third_stream_controller.QProcess") as process:
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
        with patch("monitorize.platform.process_utils.subprocess.run") as run:
            process_utils.kill_patterns("definitely-no-monitorize-process")
        run.assert_not_called()


class GnomeVirtualMonitorCompatTest(unittest.TestCase):
    class FakeDbus:
        Int32 = int
        UInt32 = int
        Double = float
        Boolean = bool
        String = str

        @staticmethod
        def Array(values, signature=None):
            return list(values)

        @staticmethod
        def Dictionary(values, signature=None):
            return dict(values)

        @staticmethod
        def Struct(values, signature=None):
            return tuple(values)

    @staticmethod
    def display_state():
        return (
            7,
            [
                (
                    ("eDP-1", "Vendor", "Panel", "1"),
                    [("edp-mode", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})],
                    {
                        "color-mode": 1,
                        "display-name": "Built-in Display",
                        "rgb-range": 2,
                    },
                ),
                (
                    ("Meta-0", "Meta", "Virtual Monitor", "2"),
                    [("meta-mode", 1920, 1200, 60.0, 1.0, [1.0], {"is-current": True})],
                    {
                        "color-mode": 3,
                        "is-underscanning": True,
                        "rgb-range": 1,
                    },
                ),
            ],
            [
                (0, 0, 1.0, 0, True, [("eDP-1", "Vendor", "Panel", "1")]),
                (1920, 0, 1.0, 0, False, [("Meta-0", "Meta", "Virtual Monitor", "2")]),
            ],
            {
                "layout-mode": 2,
                "supports-changing-layout-mode": True,
            },
        )

    @staticmethod
    def saved_right_layout():
        return [
            {
                "connectors": ["eDP-1"],
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "virtual": False,
            },
            {
                "connectors": ["Meta-0"],
                "x": 1920,
                "y": 0,
                "scale": 1.0,
                "virtual": True,
            },
        ]

    def test_current_virtual_layout_is_saved(self):
        state = (
            1,
            [
                (("eDP-1", "Vendor", "Panel", "1"), []),
                (("Meta-0", "Meta", "Virtual Monitor", "2"), []),
            ],
            [
                (0, 0, 1.0, 0, True, [("eDP-1", "Vendor", "Panel", "1")]),
                (77, -20, 1.0, 0, False, [("Meta-0", "Meta", "Virtual Monitor", "2")]),
            ],
            {},
        )
        with (
            patch("monitorize.platform.gnome_virtual_monitor._mutter_state", return_value=state),
            patch("monitorize.platform.gnome_virtual_monitor.save_gnome_virtual_layout") as save,
        ):
            self.assertTrue(gnome_virtual_monitor.save_current_virtual_layout("primary"))
        save.assert_called_once_with(
            "primary",
            [
                {
                    "connectors": ["eDP-1"],
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "virtual": False,
                },
                {
                    "connectors": ["Meta-0"],
                    "x": 77,
                    "y": -20,
                    "scale": 1.0,
                    "virtual": True,
                },
            ],
        )

    def test_missing_gnome_virtual_monitor_does_not_save(self):
        state = (
            1,
            [(("eDP-1", "Vendor", "Panel", "1"), [])],
            [(0, 0, 1.0, 0, True, [("eDP-1", "Vendor", "Panel", "1")])],
            {},
        )
        with (
            patch("monitorize.platform.gnome_virtual_monitor._mutter_state", return_value=state),
            patch("monitorize.platform.gnome_virtual_monitor.save_gnome_virtual_layout") as save,
        ):
            self.assertFalse(gnome_virtual_monitor.save_current_virtual_layout("primary"))
        save.assert_not_called()

    def test_apply_payload_requires_saved_full_layout(self):
        configs = gnome_virtual_monitor.build_monitors_config(
            self.display_state(), self.FakeDbus
        )
        self.assertIsNone(configs)

    def test_apply_payload_preserves_monitor_fields_with_full_layout(self):
        configs = gnome_virtual_monitor.build_monitors_config(
            self.display_state(),
            self.FakeDbus,
            logical_monitors=self.saved_right_layout(),
        )
        self.assertEqual(configs[0][0:2], (0, 0))
        self.assertEqual(configs[0][5][0][0:2], ("eDP-1", "edp-mode"))
        self.assertEqual(
            configs[0][5][0][2],
            {"color-mode": 1, "rgb-range": 2},
        )
        self.assertEqual(configs[1][0:2], (1920, 0))
        self.assertEqual(configs[1][2:5], (1.0, 0, False))
        self.assertEqual(configs[1][5][0][0:2], ("Meta-0", "meta-mode"))
        self.assertEqual(
            configs[1][5][0][2],
            {"color-mode": 3, "underscanning": True, "rgb-range": 1},
        )

    def test_apply_payload_restores_full_left_side_layout(self):
        saved_layout = [
            {
                "connectors": ["eDP-1"],
                "x": 1920,
                "y": 0,
                "scale": 1.0,
                "virtual": False,
            },
            {
                "connectors": ["Meta-0"],
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "virtual": True,
            },
        ]
        state = (
            7,
            [
                (
                    ("eDP-1", "Vendor", "Panel", "1"),
                    [("edp-mode", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})],
                    {"color-mode": 1, "rgb-range": 2},
                ),
                (
                    ("Meta-1", "Meta", "Virtual Monitor", "3"),
                    [("meta-mode", 1920, 1200, 60.0, 1.0, [1.0], {"is-current": True})],
                    {"color-mode": 3, "rgb-range": 1},
                ),
            ],
            [
                (0, 0, 1.0, 0, True, [("eDP-1", "Vendor", "Panel", "1")]),
                (1920, 0, 1.0, 0, False, [("Meta-1", "Meta", "Virtual Monitor", "3")]),
            ],
            {"layout-mode": 2},
        )
        configs = gnome_virtual_monitor.build_monitors_config(
            state,
            self.FakeDbus,
            logical_monitors=saved_layout,
        )
        self.assertEqual(configs[0][0:2], (1920, 0))
        self.assertEqual(configs[0][5][0][0:2], ("eDP-1", "edp-mode"))
        self.assertEqual(configs[1][0:2], (0, 0))
        self.assertEqual(configs[1][5][0][0:2], ("Meta-1", "meta-mode"))

    def test_apply_payload_restores_saved_scale(self):
        saved_layout = self.saved_right_layout()
        saved_layout[1]["scale"] = 1.25
        state = self.display_state()
        state[1][1][1][0][5].append(1.25)
        configs = gnome_virtual_monitor.build_monitors_config(
            state,
            self.FakeDbus,
            logical_monitors=saved_layout,
        )
        self.assertEqual(configs[0][2], 1.0)
        self.assertEqual(configs[1][2], 1.25)

    def test_apply_payload_rejects_unsupported_saved_scale(self):
        saved_layout = self.saved_right_layout()
        saved_layout[1]["scale"] = 1.25
        configs = gnome_virtual_monitor.build_monitors_config(
            self.display_state(),
            self.FakeDbus,
            logical_monitors=saved_layout,
        )
        self.assertIsNone(configs)

    def test_read_only_underscan_aliases_are_not_applied(self):
        state = self.display_state()
        state[1][1][2].update({
            "enable_underscanning": True,
            "underscan": True,
        })
        configs = gnome_virtual_monitor.build_monitors_config(
            state,
            self.FakeDbus,
            logical_monitors=self.saved_right_layout(),
        )
        self.assertEqual(
            configs[1][5][0][2],
            {"color-mode": 3, "underscanning": True, "rgb-range": 1},
        )
        self.assertNotIn("is-underscanning", configs[1][5][0][2])
        self.assertNotIn("enable_underscanning", configs[1][5][0][2])
        self.assertNotIn("underscan", configs[1][5][0][2])

    def test_writable_underscanning_property_wins_over_state_alias(self):
        state = self.display_state()
        state[1][1][2]["underscanning"] = False
        configs = gnome_virtual_monitor.build_monitors_config(
            state,
            self.FakeDbus,
            logical_monitors=self.saved_right_layout(),
        )
        self.assertFalse(configs[1][5][0][2]["underscanning"])

    def test_restore_virtual_layout_applies_temporary_config(self):
        display_config = Mock()
        display_config.GetCurrentState.return_value = self.display_state()
        with patch(
            "monitorize.platform.gnome_virtual_monitor.load_gnome_virtual_layout",
            return_value={
                "logical_monitors": self.saved_right_layout(),
            },
        ):
            ok = gnome_virtual_monitor.restore_virtual_layout(
                display_config=display_config,
                dbus=self.FakeDbus,
                attempts=1,
                delay=0,
            )
        self.assertTrue(ok)
        serial, method, configs, props = display_config.ApplyMonitorsConfig.call_args.args
        self.assertEqual(serial, 7)
        self.assertEqual(method, gnome_virtual_monitor.APPLY_METHOD_TEMPORARY)
        self.assertEqual(configs[0][0:2], (0, 0))
        self.assertEqual(configs[1][0:2], (1920, 0))
        self.assertEqual(props, {"layout-mode": 2})

    def test_gnome_display_config_failure_does_not_save(self):
        with (
            patch(
                "monitorize.platform.gnome_virtual_monitor._mutter_state",
                side_effect=RuntimeError("no display config"),
            ),
            patch("monitorize.platform.gnome_virtual_monitor.save_gnome_virtual_layout") as save,
        ):
            self.assertFalse(gnome_virtual_monitor.save_current_virtual_layout("primary"))
        save.assert_not_called()


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
                "pos": {"x": 0, "y": 0},
                "size": {"width": 1920, "height": 1080},
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
            if ".position." in args[1] or ".rotation." in args[1]:
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {args}")

        with (
            patch(
                "monitorize.platform.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ) as run,
            patch("monitorize.platform.kde_virtual_monitor.load_kde_virtual_layout",
                  return_value={"position": None, "rotation": ""}),
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
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

    def test_portal_layout_uses_saved_slot_position_and_rotation(self):
        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                return Mock(
                    returncode=0,
                    stdout=json.dumps({"outputs": self.portal_outputs(True, True)}),
                    stderr="",
                )
            return Mock(returncode=0, stdout="", stderr="")

        with (
            patch("monitorize.platform.kde_virtual_monitor.subprocess.run", side_effect=fake_run) as run,
            patch(
                "monitorize.platform.kde_virtual_monitor.load_kde_virtual_layout",
                return_value={"position": (77, 88), "rotation": "left"},
            ) as load,
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
        ):
            ok, _output_name, message = kde_virtual_monitor.configure_portal_virtual_output(
                {"eDP-1"}, 1920, 1200, 60, "third", attempts=1, delay=0,
            )
        self.assertTrue(ok, message)
        load.assert_called_once_with("third")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "kscreen-doctor",
                "output.Virtual-virtual-xdp-kde-monitorize.position.77,88",
            ],
            commands,
        )
        self.assertIn(
            [
                "kscreen-doctor",
                "output.Virtual-virtual-xdp-kde-monitorize.rotation.left",
            ],
            commands,
        )

    def test_current_virtual_layout_is_saved(self):
        with (
            patch("monitorize.platform.kde_virtual_monitor.kde_outputs", return_value=[
                {"name": "Virtual-1", "connected": True, "enabled": True,
                 "pos": {"x": 123, "y": 45}, "rotation": "right"},
            ]),
            patch("monitorize.platform.kde_virtual_monitor.save_kde_virtual_layout") as save,
        ):
            kde_virtual_monitor.save_current_virtual_layout("primary", "Virtual-1")
        save.assert_called_once_with("primary", 123, 45, "right")

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
                "monitorize.platform.kde_virtual_monitor.kde_outputs",
                return_value=outputs,
            ),
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
            patch("monitorize.platform.kde_virtual_monitor.subprocess.run") as run,
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


class StreamerGnomeTest(unittest.TestCase):
    class FakeStruct(tuple):
        def __new__(cls, values, signature=None):
            item = super().__new__(cls, values)
            item.signature = signature
            return item

    class FakeDbus:
        Int32 = int
        UInt32 = int
        Double = float
        Boolean = bool

        @staticmethod
        def Array(values, signature=None):
            return list(values)

        @staticmethod
        def Dictionary(values, signature=None):
            return dict(values)

        @staticmethod
        def Struct(values, signature=None):
            return StreamerGnomeTest.FakeStruct(values, signature)

    def test_parse_args_accepts_display_type_without_scale_arg(self):
        config = Streamer_gnome.parse_args([
            "1920", "1200", "60", "8000", "usb", "Extend",
        ])
        self.assertEqual(config.display_type, "Extend")

    def test_parse_args_empty_argv_uses_defaults(self):
        config = Streamer_gnome.parse_args([])
        self.assertEqual(config.width, 2560)
        self.assertEqual(config.height, 1600)
        self.assertEqual(config.fps, 60)
        self.assertEqual(config.bitrate, 8000)
        self.assertEqual(config.mode, "usb")
        self.assertEqual(config.display_type, "Extend")

    def test_record_virtual_does_not_pass_position(self):
        session = Mock()
        config = Streamer_gnome.StreamerConfig(
            width=1920,
            height=1200,
            fps=60,
        )
        Streamer_gnome._record_virtual(session, self.FakeDbus, config)
        options = session.RecordVirtual.call_args.args[0]
        self.assertNotIn("position", options)

    def test_record_virtual_includes_preferred_scale_when_saved(self):
        session = Mock()
        config = Streamer_gnome.StreamerConfig(
            width=1920,
            height=1200,
            fps=60,
            preferred_scale=1.25,
        )
        Streamer_gnome._record_virtual(session, self.FakeDbus, config)
        options = session.RecordVirtual.call_args.args[0]
        self.assertEqual(options["modes"][0]["preferred-scale"], 1.25)

    def test_record_virtual_includes_preferred_mode(self):
        session = Mock()
        config = Streamer_gnome.StreamerConfig(width=1920, height=1200, fps=60)
        Streamer_gnome._record_virtual(session, self.FakeDbus, config)
        options = session.RecordVirtual.call_args.args[0]
        self.assertEqual(options["modes"][0]["size"], (1920, 1200))
        self.assertEqual(options["modes"][0]["size"].signature, "uu")
        self.assertEqual(options["modes"][0]["refresh-rate"], 60.0)
        self.assertTrue(options["modes"][0]["is-preferred"])

    def test_restore_happens_before_gstreamer_launch(self):
        events = []

        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        config = Streamer_gnome.StreamerConfig(display_type="Extend")
        with (
            patch(
                "monitorize.streaming.Streamer_gnome._restore_virtual_layout",
                side_effect=lambda *_args: events.append("restore"),
            ),
            patch("monitorize.streaming.Streamer_gnome.threading.Thread", FakeThread),
        ):
            Streamer_gnome._restore_and_launch(
                Mock(), self.FakeDbus, config,
                lambda node_id: events.append(f"launch:{node_id}"),
                42,
            )
        self.assertEqual(events, ["restore", "launch:42"])

    def test_restore_failure_still_launches_gstreamer(self):
        events = []

        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        config = Streamer_gnome.StreamerConfig(display_type="Extend")
        with (
            patch(
                "monitorize.streaming.Streamer_gnome._restore_virtual_layout",
                side_effect=RuntimeError("restore failed"),
            ),
            patch("monitorize.streaming.Streamer_gnome.threading.Thread", FakeThread),
        ):
            Streamer_gnome._restore_and_launch(
                Mock(), self.FakeDbus, config,
                lambda node_id: events.append(f"launch:{node_id}"),
                42,
            )
        self.assertEqual(events, ["launch:42"])


class PipelineBuilderTest(unittest.TestCase):
    def _pipeline_text(self, **kwargs):
        argv = pipeline_builder.build_pipeline(
            pw_fd=None,
            node_id=42,
            width=1280,
            height=800,
            fps=60,
            bitrate=8000,
            port=7110,
            **kwargs,
        )
        return " ".join(argv)

    def test_low_latency_encoder_profile_keeps_current_nvenc_settings(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", encoder_profile="Low Latency"
        )
        self.assertIn("preset=p1", text)
        self.assertIn("tune=ultra-low-latency", text)
        self.assertIn("rc-lookahead=0", text)
        self.assertIn("bframes=0", text)

    def test_balanced_and_quality_cpu_profiles_change_speed_preset(self):
        balanced = self._pipeline_text(encoder_profile="Balanced")
        quality = self._pipeline_text(encoder_profile="Quality")
        self.assertIn("speed-preset=superfast", balanced)
        self.assertIn("ref=1", balanced)
        self.assertIn("speed-preset=veryfast", quality)
        self.assertIn("ref=2", quality)
        self.assertIn("bframes=0", quality)

    def test_balanced_and_quality_nvenc_profiles_change_preset_only(self):
        balanced = self._pipeline_text(
            hw_encoder="nvh264enc", encoder_profile="Balanced"
        )
        quality = self._pipeline_text(
            hw_encoder="nvh264enc", encoder_profile="Quality"
        )
        self.assertIn("preset=p3", balanced)
        self.assertIn("preset=p5", quality)
        self.assertIn("rc-lookahead=0", quality)
        self.assertIn("bframes=0", quality)

    def test_balanced_and_quality_vaapi_profiles_change_usage(self):
        balanced = self._pipeline_text(
            hw_encoder="vah264enc", wifi_mode=True, encoder_profile="Balanced"
        )
        quality = self._pipeline_text(
            hw_encoder="vah264enc", wifi_mode=True, encoder_profile="Quality"
        )
        self.assertIn("target-usage=5", balanced)
        self.assertIn("cabac=true", balanced)
        self.assertIn("target-usage=3", quality)
        self.assertIn("ref-frames=2", quality)
        self.assertIn("b-frames=0", quality)

    def test_launch_uses_argv_without_shell(self):
        proc = Mock()
        proc.pid = 123
        proc.wait.side_effect = TimeoutExpired("gst-launch-1.0", 0.25)
        with patch("monitorize.streaming.pipeline_builder.subprocess.Popen", return_value=proc) as popen:
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
            "monitorize.streaming.pipeline_builder.subprocess.Popen",
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
            patch("monitorize.streaming.portal_streamer.DBusGMainLoop"),
            patch("monitorize.streaming.portal_streamer.dbus.SessionBus", return_value=bus),
            patch("monitorize.streaming.portal_streamer.dbus.Interface", side_effect=fake_interface),
            patch("monitorize.streaming.portal_streamer.GLib.MainLoop", return_value=FakeLoop()),
            patch("monitorize.streaming.portal_streamer.threading.Thread", FakeThread),
            patch("monitorize.streaming.portal_streamer.signal.signal"),
            patch(
                "monitorize.streaming.portal_streamer.secrets.token_hex",
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

    def test_cleanup_closes_portal_session_before_stopping_gstreamer(self):
        events = []
        handlers = {}
        screen_cast = Mock()
        screen_cast.CreateSession.return_value = "/request/create"
        screen_cast.SelectSources.return_value = "/request/select"
        screen_cast.Start.return_value = "/request/start"
        screen_cast.OpenPipeWireRemote.return_value = Mock(take=Mock(return_value=9))

        class FakeBus:
            callback = None

            def get_object(self, _service, _path):
                return Mock()

            def add_signal_receiver(self, callback, **_kwargs):
                self.callback = callback

        bus = FakeBus()

        class FakeLoop:
            def run(self):
                bus.callback(0, {"session_handle": "/session"}, path="/request/create")
                bus.callback(0, {}, path="/request/select")
                bus.callback(0, {"streams": [(42, {})]}, path="/request/start")

            def is_running(self):
                return False

            def quit(self):
                pass

        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        class FakeGst:
            returncode = 0

            def poll(self):
                return None

            def terminate(self):
                events.append("gst-terminate")

            def wait(self, *args, **kwargs):
                if "timeout" not in kwargs:
                    events.append("stream-wait")
                    handlers[signal.SIGTERM]()
                else:
                    events.append("gst-wait-timeout")
                return 0

            def kill(self):
                events.append("gst-kill")

        session_interface = Mock()
        session_interface.Close.side_effect = lambda: events.append("session-close")

        def fake_interface(_object, interface_name):
            if interface_name == "org.freedesktop.portal.ScreenCast":
                return screen_cast
            return session_interface

        with (
            patch("monitorize.streaming.portal_streamer.DBusGMainLoop"),
            patch("monitorize.streaming.portal_streamer.dbus.SessionBus", return_value=bus),
            patch("monitorize.streaming.portal_streamer.dbus.Interface", side_effect=fake_interface),
            patch("monitorize.streaming.portal_streamer.GLib.MainLoop", return_value=FakeLoop()),
            patch("monitorize.streaming.portal_streamer.GLib.idle_add"),
            patch("monitorize.streaming.portal_streamer.threading.Thread", FakeThread),
            patch("monitorize.streaming.portal_streamer.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)),
            patch("monitorize.streaming.portal_streamer.launch_with_fallback", return_value=FakeGst()),
        ):
            portal_streamer.run_portal_streamer(
                "KDE", "Create virtual screen", 1920, 1200, 60, 8000,
                "wifi", 7110, "vah264enc", "127.0.0.1", source_type=4,
            )

        self.assertLess(events.index("session-close"), events.index("gst-terminate"))
        self.assertNotIn("gst-kill", events)


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
    def test_main_menu_desktop_badge_uses_backend_detected_de(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "MainMenuPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertNotIn("page.detectedDe", qml)
        self.assertIn("backend.detectedDe", qml)

    def test_qml_api_remains_exposed(self):
        with patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"):
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
        with patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"):
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
                "encoder_profile": "Quality",
            },
            "general": {
                "minimize_to_tray": False,
                "enable_touch": True,
                "enable_stylus_features": False,
            },
            "third": {"enabled": False},
        }
        with (
            patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"),
            patch("monitorize.desktop.backend.load_presets", return_value=[preset]),
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
            self.assertEqual(start.call_args.args[5], "Quality")
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
                "encoder_profile": "Balanced",
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
            patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"),
            patch("monitorize.desktop.backend.load_presets", return_value=[preset]),
        ):
            backend = MonitorizeBackend("kde")
        with (
            patch.object(backend.streaming, "start") as start,
            patch(
                "monitorize.desktop.backend.load_general_settings",
                return_value={"minimize_to_tray": True},
            ),
        ):
            backend.launchPreset(0)
            start.assert_called_once()
            self.assertEqual(start.call_args.args[5], "Balanced")
            self.assertTrue(backend.should_minimize_to_tray())
        backend.network_timer.stop()

    def test_backend_autostart_slots_delegate_to_helper(self):
        with patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        with (
            patch("monitorize.desktop.backend.autostart.is_enabled", return_value=True) as enabled,
            patch("monitorize.desktop.backend.autostart.set_enabled", return_value="") as set_enabled,
        ):
            self.assertTrue(backend.isAutostartEnabled())
            self.assertEqual(backend.setAutostartEnabled(False), "")
        enabled.assert_called_once()
        set_enabled.assert_called_once_with(False)
        backend.network_timer.stop()

    def test_start_in_tray_hides_initial_window_when_tray_is_available(self):
        from monitorize.desktop.main_window import _show_initial_window

        window = Mock()
        window.tray = Mock()
        with (
            patch("monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True),
            patch("monitorize.desktop.main_window.QApplication.setQuitOnLastWindowClosed") as set_quit,
        ):
            shown = _show_initial_window(window, True)
        self.assertFalse(shown)
        window.tray.show.assert_called_once()
        window.show.assert_not_called()
        set_quit.assert_called_once_with(False)

    def test_start_in_tray_falls_back_when_tray_is_unavailable(self):
        from monitorize.desktop.main_window import _show_initial_window

        window = Mock()
        window.tray = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        ):
            shown = _show_initial_window(window, True)
        self.assertTrue(shown)
        window.show.assert_called_once()
        window.tray.show.assert_not_called()

    def test_close_event_returns_idle_full_app_to_light_tray(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = False
        window.backend.isReceiving = False
        window.tray = Mock()
        window._quit_to_tray_agent.return_value = True
        event = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.accept.assert_called_once()
        event.ignore.assert_not_called()
        window._quit_to_tray_agent.assert_called_once()
        window.hide.assert_not_called()
        window.tray.show.assert_not_called()

    def test_close_event_minimizes_to_full_tray_when_agent_start_fails(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = False
        window.backend.isReceiving = False
        window.tray = Mock()
        window._quit_to_tray_agent.return_value = False
        event = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        window.tray.show.assert_called_once()

    def test_close_event_minimizes_to_tray_while_streaming(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = True
        window.backend.isReceiving = False
        window.tray = Mock()
        event = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        window.tray.show.assert_called_once()
        window._quit_app.assert_not_called()

    def test_close_event_minimizes_to_tray_while_receiving(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        window.backend.isStreaming = False
        window.backend.isReceiving = True
        window.tray = Mock()
        event = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            MonitorizeWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        window.tray.show.assert_called_once()
        window._quit_to_tray_agent.assert_not_called()

    def test_close_event_quits_when_minimize_to_tray_is_disabled(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = False
        event = Mock()
        MonitorizeWindow.closeEvent(window, event)
        window._quit_app.assert_called_once()
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_close_event_quits_when_tray_is_unavailable(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.should_minimize_to_tray.return_value = True
        event = Mock()
        with patch(
            "monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        ):
            MonitorizeWindow.closeEvent(window, event)
        window._quit_app.assert_called_once()
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_launch_preset_arg_is_parsed_for_full_app(self):
        from monitorize.desktop.main_window import _instance_command, _launch_preset_index

        argv = ["-m", "monitorize", "--start-in-tray", "--launch-preset", "2"]
        self.assertEqual(_launch_preset_index(argv), 2)
        self.assertEqual(_instance_command(True, 2), b"preset:2")

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
            patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"),
            patch("monitorize.desktop.backend.load_presets", return_value=[existing]),
        ):
            backend = MonitorizeBackend("kde")
        backend.streaming.streaming = True
        backend.streaming.active_configuration = Mock(return_value=snapshot)
        with (
            patch("monitorize.desktop.backend.save_presets") as save,
            patch("monitorize.desktop.backend.load_presets", return_value=[
                {**snapshot, "name": "New"}
            ]),
        ):
            result = backend.saveCurrentPreset("New", 0)
        self.assertEqual(result, "")
        self.assertEqual(save.call_args.args[0][0]["name"], "New")
        backend.network_timer.stop()


if __name__ == "__main__":
    unittest.main()
