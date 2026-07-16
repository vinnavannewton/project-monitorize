import json
import os
import signal
import sys
import socket
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from subprocess import TimeoutExpired

from PyQt6.QtCore import QCoreApplication, QProcess

from monitorize.streaming import Streamer_gnome, pipeline_builder
from monitorize.streaming import kde_native_streamer, portal_streamer
from monitorize.config import app_log, autostart, settings
from monitorize.platform import (
    gnome_virtual_monitor,
    kde_virtual_monitor,
    process_utils,
)
from monitorize.platform.display_controller import DisplayController
from monitorize.desktop.discovery_service import DiscoveryService
from monitorize.desktop.backend import MonitorizeBackend
from monitorize.desktop.receiver_controller import ReceiverController
from monitorize.desktop.streaming_controller import StreamingController
from monitorize.desktop.usb_controller import UsbController, authorized_adb_serials
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
    def test_worker_thread_resolution_reaches_qt_owner_thread(self):
        service = DiscoveryService()
        values = ("Host", "10.0.0.2", 7110, False, "", False, 7114, "svc")
        worker = threading.Thread(target=lambda: service._deviceResolved.emit(values))
        worker.start()
        worker.join()
        deadline = time.monotonic() + 1
        while not service.devices and time.monotonic() < deadline:
            app.processEvents()

        self.assertEqual(service.devices[0]["ip"], "10.0.0.2")

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
                self.name = args[1]
                self.port = kwargs["port"]

        fake_module = types.SimpleNamespace(
            ServiceInfo=FakeInfo, Zeroconf=FakeZeroconf
        )
        service = DiscoveryService()
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
            service.advertise("127.0.0.1", False, True)
        self.assertEqual(len(registered), 2)
        self.assertEqual(registered[0].port, 7110)
        self.assertEqual(registered[0].properties["encrypted"], "0")
        self.assertEqual(registered[0].properties["fps"], "60")
        self.assertEqual(registered[0].properties["third_available"], "1")
        self.assertEqual(registered[1].port, 7114)
        self.assertIn("Second Virtual Monitor", registered[1].name)
        self.assertIn("Second Virtual Monitor", registered[1].properties["name"])

    def test_advertisement_declares_selected_fps(self):
        registered = []

        class FakeZeroconf:
            def register_service(self, info):
                registered.append(info)

            def close(self):
                pass

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.properties = kwargs["properties"]
                self.port = kwargs["port"]

        fake_module = types.SimpleNamespace(
            ServiceInfo=FakeInfo, Zeroconf=FakeZeroconf
        )
        service = DiscoveryService()
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
            service.advertise("127.0.0.1", False, True, 90, 75)
        self.assertEqual(registered[0].properties["fps"], "90")
        self.assertEqual(registered[1].properties["fps"], "75")


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
            service.advertise("127.0.0.1", True, True)
        self.assertEqual(len(registered), 2)
        for advertisement in registered:
            self.assertEqual(
                advertisement.properties["input_transport"], "udp-aesgcm-v1"
            )
            self.assertEqual(advertisement.properties["fingerprint"], "FP")

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
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
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
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
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
        self.assertEqual(len(registered), 2)

    def test_removing_third_advertisement_keeps_primary(self):
        registered = []
        unregistered = []

        class FakeZeroconf:
            def register_service(self, info):
                registered.append(info)

            def unregister_service(self, info):
                unregistered.append(info)

            def close(self):
                pass

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.port = kwargs["port"]

        fake_module = types.SimpleNamespace(
            ServiceInfo=FakeInfo, Zeroconf=FakeZeroconf
        )
        service = DiscoveryService()
        with patch.dict(sys.modules, {"zeroconf": fake_module}):
            service.advertise("127.0.0.1", False, True)
            service.advertise("127.0.0.1", False, False)

        self.assertEqual([item.port for item in unregistered], [7110, 7114])
        self.assertEqual([item.port for item in service.advertisements], [7110])


class HyprlandDisplayControllerTest(unittest.TestCase):
    def test_additional_output_has_independent_creation_and_removal(self):
        display = DisplayController("hyprland")
        with (
            patch.object(
                display,
                "headless_monitors",
                side_effect=[["HEADLESS-1"], ["HEADLESS-1", "HEADLESS-2"]],
            ),
            patch("monitorize.platform.display_controller.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            output, error = display.prepare_hyprland(1280, 720, 60, "additional")
            display.remove_hyprland_output("additional")
        self.assertEqual((output, error), ("HEADLESS-2", ""))
        self.assertIsNone(display.additional_output)
        self.assertIsNone(display.created_output)
        self.assertIn(
            (["hyprctl", "output", "remove", "HEADLESS-2"],),
            [call.args for call in run.call_args_list],
        )


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
    def test_tray_agent_constructs_tray_icon_path(self):
        from monitorize.desktop import tray_agent

        action = Mock()
        action.triggered.connect = Mock()
        disabled_action = Mock()
        menu = Mock()
        presets_menu = Mock()
        presets_menu.aboutToShow.connect = Mock()
        presets_menu.addAction.return_value = disabled_action
        menu.addAction.return_value = action
        menu.addMenu.return_value = presets_menu
        tray = Mock()
        tray.activated.connect = Mock()

        with (
            patch("monitorize.desktop.tray_agent.QSystemTrayIcon", return_value=tray),
            patch("monitorize.desktop.tray_agent.QMenu", return_value=menu),
            patch("monitorize.desktop.tray_agent.QIcon") as icon,
            patch("monitorize.desktop.tray_agent.load_presets", return_value=[]),
        ):
            tray_agent.TrayAgent()

        icon.assert_called_once_with(
            os.path.join(
                tray_agent.ASSETS_DIR,
                "tray",
                "icon_tray_white.svg",
            )
        )

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

    def test_autostart_uses_system_desktop_entry_for_rpm_install(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            data_home = Path(directory) / "data"
            system_app_dir = Path(directory) / "system-applications"
            system_app_dir.mkdir()
            (system_app_dir / "monitorize.desktop").write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Monitorize\n"
                "Exec=monitorize\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_DATA_HOME": str(data_home),
                }),
                patch.object(autostart, "SYSTEM_APPLICATIONS_DIR", system_app_dir),
            ):
                self.assertEqual(autostart.set_enabled(True), "")
                content = autostart.autostart_path().read_text(encoding="utf-8")
        self.assertIn("Exec=monitorize --tray-agent", content)

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


class PlatformDetectionTest(unittest.TestCase):
    def test_windows_detects_windows_desktop(self):
        with patch("monitorize.platform.utils.sys.platform", "win32"):
            self.assertEqual(platform_utils.detect_desktop_environment(), "windows")

    def test_windows_detection_does_not_prompt_for_linux_desktop(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window._ask_desktop_environment = Mock()
        with patch(
            "monitorize.desktop.main_window.detect_desktop_environment",
            return_value="windows",
        ):
            self.assertEqual(
                MonitorizeWindow._select_desktop_environment(window), "windows"
            )
        window._ask_desktop_environment.assert_not_called()


class ReceiverControllerTest(unittest.TestCase):
    def test_pipeline_preserves_compressed_frames_before_decode(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.sink = "xvimagesink"
        process = process_mock()
        with (
            patch("monitorize.desktop.receiver_controller.QProcess", return_value=process),
            patch(
                "monitorize.desktop.receiver_controller._gst_has_property",
                return_value=True,
            ),
        ):
            controller._launch_pipeline("10.0.0.2", 7114)
        command, args = process.start.call_args.args
        self.assertEqual(command, "gst-launch-1.0")
        self.assertIn("vah264dec", args)
        self.assertIn("disable-passthrough=true", args)
        self.assertIn("config-interval=-1", args)
        self.assertIn("video/x-h264,stream-format=byte-stream,alignment=au", args)
        decoder_index = args.index("vah264dec")
        first_queue_index = args.index("queue")
        self.assertLess(first_queue_index, decoder_index)
        self.assertIn("max-size-buffers=3", args[first_queue_index:decoder_index])
        self.assertNotIn("leaky=downstream", args[first_queue_index:decoder_index])
        self.assertIn("leaky=downstream", args[decoder_index:])
        self.assertIn("sync=false", args)
        self.assertIn("async=false", args)
        self.assertIn("force-aspect-ratio=false", args)
        self.assertIn("port=7114", args)

    def test_embedded_pipeline_uses_video_overlay_sink(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.receiver_surface_width = 1920
        controller.receiver_surface_height = 1080
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            return_value=True,
        ):
            description = controller._embedded_pipeline_description(
                "10.0.0.2", 7110, "glimagesink"
            )
        self.assertIn("glimagesink", description)
        self.assertIn("name=receiver_sink", description)
        self.assertIn("videoconvert", description)
        self.assertIn("videoscale add-borders=false", description)
        self.assertIn(
            "video/x-raw,width=1920,height=1080,pixel-aspect-ratio=1/1",
            description,
        )
        self.assertIn("force-aspect-ratio=false", description)
        parts = description.split()
        decoder_index = parts.index("avdec_h264")
        first_queue_index = parts.index("queue")
        self.assertLess(first_queue_index, decoder_index)
        self.assertIn("max-size-buffers=3", parts[first_queue_index:decoder_index])
        self.assertNotIn("leaky=downstream", parts[first_queue_index:decoder_index])
        self.assertIn("leaky=downstream", parts[decoder_index:])

    def test_embedded_sink_prefers_wayland_on_wayland(self):
        controller = ReceiverController("kde", Mock())
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}, clear=True),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"waylandsink", "glimagesink"},
            ),
        ):
            self.assertEqual(controller._embedded_sink_name(), "waylandsink")

    def test_wayland_receivers_default_to_external_sink(self):
        for de in ("kde", "gnome", "hyprland"):
            controller = ReceiverController(de, Mock())
            with (
                patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}, clear=True),
                patch(
                    "monitorize.desktop.receiver_controller.gst_has_element",
                    side_effect=lambda name: name in {"waylandsink", "glimagesink"},
                ),
            ):
                self.assertFalse(controller.should_use_embedded_window(), de)

    def test_wayland_receiver_can_force_embedded_for_debugging(self):
        controller = ReceiverController("gnome", Mock())
        with (
            patch.dict(os.environ, {
                "XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": "wayland-0",
                "MONITORIZE_RECEIVER_EMBEDDED": "1",
            }, clear=True),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"waylandsink", "glimagesink"},
            ),
        ):
            self.assertTrue(controller.should_use_embedded_window())

    def test_wayland_receiver_launches_external_even_with_video_item(self):
        controller = ReceiverController("gnome", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.decoder_label = "Software"
        controller.video_item = Mock()
        controller.video_item.width.return_value = 1920
        controller.video_item.height.return_value = 1080
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}, clear=True),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"waylandsink", "glimagesink"},
            ),
            patch.object(controller, "_launch_external_pipeline") as external,
            patch.object(controller, "_launch_embedded_pipeline") as embedded,
        ):
            controller._launch_pipeline("10.0.0.2", 7110, generation=0)
        embedded.assert_not_called()
        external.assert_called_once_with("10.0.0.2", 7110, 0)

    def test_embedded_wayland_sink_does_not_request_standalone_fullscreen(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.receiver_surface_width = 1920
        controller.receiver_surface_height = 1080
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            return_value=True,
        ):
            description = controller._embedded_pipeline_description(
                "10.0.0.2", 7110, "waylandsink"
            )
        self.assertIn("waylandsink", description)
        self.assertNotIn("fullscreen=true", description)
        self.assertIn("force-aspect-ratio=false", description)

    def test_receiver_waits_for_embedded_video_surface(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.decoder_label = "Software"
        with (
            patch.object(controller, "_should_wait_for_embedded_surface", return_value=True),
            patch.object(controller, "_launch_external_pipeline") as external,
        ):
            controller._launch_pipeline("10.0.0.2", 7110, generation=0)
        external.assert_not_called()
        self.assertEqual(controller.pending_launch, ("10.0.0.2", 7110, 0))
        self.assertTrue(controller.surface_timer.isActive())
        controller.surface_timer.stop()

    def test_receiver_video_item_starts_pending_pipeline(self):
        controller = ReceiverController("kde", Mock())
        controller.pending_launch = ("10.0.0.2", 7110, 3)
        controller.surface_timer.start(1000)
        item = Mock()
        item.width.return_value = 1920
        item.height.return_value = 1080
        with patch.object(controller, "_launch_pipeline") as launch:
            controller.set_video_item(item)
        launch.assert_called_once_with("10.0.0.2", 7110, 3)
        self.assertEqual(controller.receiver_surface_width, 1920)
        self.assertEqual(controller.receiver_surface_height, 1080)
        self.assertIsNone(controller.pending_launch)
        self.assertFalse(controller.surface_timer.isActive())

    def test_receiver_video_item_waits_when_surface_is_tiny(self):
        controller = ReceiverController("kde", Mock())
        controller.pending_launch = ("10.0.0.2", 7110, 3)
        item = Mock()
        item.width.return_value = 1
        item.height.return_value = 1080
        with patch.object(controller, "_launch_pipeline") as launch:
            controller.set_video_item(item)
        launch.assert_not_called()
        self.assertEqual(controller.pending_launch, ("10.0.0.2", 7110, 3))
        self.assertTrue(controller.surface_timer.isActive())
        controller.surface_timer.stop()

    def test_embedded_pipeline_uses_same_scaling_for_primary_and_third_ports(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.receiver_surface_width = 1366
        controller.receiver_surface_height = 768
        primary = controller._embedded_pipeline_description(
            "10.0.0.2", 7110, "waylandsink"
        )
        third = controller._embedded_pipeline_description(
            "10.0.0.2", 7114, "waylandsink"
        )
        scaled_caps = "video/x-raw,width=1366,height=768,pixel-aspect-ratio=1/1"
        self.assertIn("port=7110", primary)
        self.assertIn("port=7114", third)
        self.assertIn(scaled_caps, primary)
        self.assertIn(scaled_caps, third)

    def test_embedded_video_geometry_syncs_render_rectangle(self):
        controller = ReceiverController("kde", Mock())
        video_item = Mock()
        video_item.width.return_value = 1920
        video_item.height.return_value = 1080
        sink = Mock()
        controller.video_item = video_item
        controller.gst_video_sink = sink
        controller.sync_video_geometry()
        sink.set_render_rectangle.assert_called_once_with(0, 0, 1920, 1080)
        sink.expose.assert_called_once()

    def test_embedded_video_geometry_clamps_zero_size(self):
        controller = ReceiverController("kde", Mock())
        video_item = Mock()
        video_item.width.return_value = 0
        video_item.height.return_value = 0
        sink = Mock()
        controller.video_item = video_item
        controller._sync_embedded_sink_geometry(sink)
        sink.set_render_rectangle.assert_called_once_with(0, 0, 1, 1)

    def test_embedded_video_geometry_logs_tiny_surface_once(self):
        controller = ReceiverController("kde", Mock())
        video_item = Mock()
        video_item.width.return_value = 32
        video_item.height.return_value = 24
        sink = Mock()
        emitted = []
        controller.video_item = video_item
        controller.logAppended.connect(emitted.append)
        controller._sync_embedded_sink_geometry(sink)
        controller._sync_embedded_sink_geometry(sink)
        self.assertEqual(len(emitted), 1)
        self.assertIn("32x24", emitted[0])

    def test_receiver_resize_schedules_one_embedded_pipeline_restart(self):
        controller = ReceiverController("kde", Mock())
        video_item = Mock()
        video_item.width.return_value = 1920
        video_item.height.return_value = 1080
        sink = Mock()
        controller.video_item = video_item
        controller.gst_video_sink = sink
        controller.gst_pipeline = object()
        controller.embedded_pipeline_size = (1280, 720)
        controller.resize_restart_timer.start = Mock()
        controller.sync_video_geometry()
        controller.resize_restart_timer.start.assert_called_once_with(150)

    def test_receiver_resize_restart_relaunches_embedded_pipeline_once(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 5
        controller.receiver_host = "10.0.0.2"
        controller.receiver_port = 7110
        controller.video_item = Mock()
        controller.receiver_surface_width = 1920
        controller.receiver_surface_height = 1080
        controller.gst_pipeline = object()
        with patch.object(controller, "_launch_embedded_pipeline") as launch:
            controller._restart_embedded_for_resize(5)
            controller._restart_embedded_for_resize(5)
        launch.assert_called_once_with("10.0.0.2", 7110, 5)
        self.assertTrue(controller.resize_restart_used)

    def test_embedded_pipeline_can_mark_receiver_stable(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 6
        controller.host = "10.0.0.2"
        controller.port = 7110
        pipeline = object()
        controller.gst_pipeline = pipeline
        with patch.object(controller, "_inhibit_sleep"):
            controller._mark_stable(6, pipeline)
        self.assertTrue(controller.stable)
        self.assertTrue(controller.receiving)
        self.assertEqual(controller.status, "Receiving from 10.0.0.2:7110")

    def test_receiver_connect_marks_session_active_before_stable(self):
        controller = ReceiverController("kde", Mock())
        emitted = []
        controller.receivingChanged.connect(lambda value: emitted.append(value))
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("10.0.0.2", 7110, False, "", "", "Software")
        start.assert_called_once()
        self.assertTrue(controller.receiving)
        self.assertFalse(controller.stable)
        self.assertIn(True, emitted)

    def test_encrypted_receiver_waits_for_tls_ready_before_session_active(self):
        controller = ReceiverController("kde", Mock())
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("10.0.0.2", 7110, True, "fingerprint", "", "Software")
        start.assert_called_once()
        self.assertFalse(controller.receiving)

    def test_software_decoder_discards_corrupt_output_when_supported(self):
        controller = ReceiverController("kde", Mock())
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            return_value=True,
        ):
            args = controller._software_decoder_args()
        self.assertEqual(args[0], "avdec_h264")
        self.assertIn("output-corrupt=false", args)
        self.assertIn("discard-corrupted-frames=true", args)
        self.assertIn("automatic-request-sync-points=true", args)
        self.assertIn("max-threads=2", args)

    def test_sink_selection_prefers_gl_before_wayland_fallback(self):
        controller = ReceiverController("kde", Mock())
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}, clear=True),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"waylandsink", "glimagesink"},
            ),
        ):
            self.assertEqual(
                controller._sink_candidates(),
                ["glimagesink", "waylandsink", "autovideosink"],
            )

    def test_sink_selection_prefers_gl_before_x11_fallbacks(self):
        controller = ReceiverController("kde", Mock())
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, clear=True),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"xvimagesink", "ximagesink", "glimagesink"},
            ),
        ):
            self.assertEqual(
                controller._sink_candidates(),
                ["glimagesink", "xvimagesink", "ximagesink", "autovideosink"],
            )

    def test_windows_receiver_profiles_prefer_d3d11_hardware_then_in_app_fallbacks(self):
        controller = ReceiverController("windows", Mock())
        with (
            patch("monitorize.desktop.receiver_controller.sys.platform", "win32"),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {
                    "d3d11h264dec", "d3d11videosink",
                },
            ),
            patch(
                "monitorize.desktop.receiver_controller._gst_has_property",
                return_value=False,
            ),
        ):
            profiles = controller._windows_receiver_profiles("Hardware")
        self.assertEqual(
            profiles,
            [
                (["d3d11h264dec"], "D3D11 d3d11h264dec", "d3d11videosink"),
                (["avdec_h264"], "Software avdec_h264", "d3d11videosink"),
                (["avdec_h264"], "Software avdec_h264", "autovideosink"),
            ],
        )

    def test_windows_receiver_launches_embedded_not_external_gstreamer(self):
        controller = ReceiverController("windows", Mock())
        controller.decoder_args = ["avdec_h264"]
        controller.decoder_label = "Software avdec_h264"
        controller.sink = "d3d11videosink"
        item = Mock()
        item.width.return_value = 1920
        item.height.return_value = 1080
        controller.video_item = item
        with (
            patch("monitorize.desktop.receiver_controller.sys.platform", "win32"),
            patch.object(controller, "_embedded_sink_available", return_value=True),
            patch.object(controller, "_launch_embedded_pipeline") as embedded,
            patch.object(controller, "_launch_external_pipeline") as external,
        ):
            controller._launch_pipeline("10.0.0.2", 7110, generation=0)
        embedded.assert_called_once_with("10.0.0.2", 7110, 0)
        external.assert_not_called()

    def test_windows_embedded_launch_uses_active_profile_sink(self):
        controller = ReceiverController("windows", Mock())
        controller.sink = "autovideosink"
        with patch("monitorize.desktop.receiver_controller.sys.platform", "win32"):
            self.assertEqual(controller._active_embedded_sink_name(), "autovideosink")

    def test_windows_receiver_failure_does_not_launch_external_gstreamer(self):
        controller = ReceiverController("windows", Mock())
        controller.windows_profiles = [(["avdec_h264"], "Software avdec_h264", "d3d11videosink")]
        controller.decoder_args = ["avdec_h264"]
        controller.decoder_label = "Software avdec_h264"
        controller.sink = "d3d11videosink"
        item = Mock()
        item.width.return_value = 1920
        item.height.return_value = 1080
        controller.video_item = item
        with (
            patch("monitorize.desktop.receiver_controller.sys.platform", "win32"),
            patch.object(controller, "_embedded_sink_available", return_value=True),
            patch.object(
                controller, "_launch_embedded_pipeline",
                side_effect=RuntimeError("missing sink"),
            ),
            patch.object(controller, "_launch_external_pipeline") as external,
        ):
            controller._launch_pipeline("10.0.0.2", 7110, generation=0)
        external.assert_not_called()
        self.assertFalse(controller.receiving)
        self.assertEqual(controller.status, "Windows in-app receiver unavailable")

    def test_sink_args_only_include_supported_properties_and_stretch(self):
        controller = ReceiverController("kde", Mock())
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            side_effect=lambda _element, prop: prop in {"sync", "force-aspect-ratio"},
        ):
            args = controller._sink_args("glimagesink")
        self.assertEqual(args, ["glimagesink", "sync=false", "force-aspect-ratio=false"])

    def test_wayland_fallback_sink_requests_fullscreen_when_supported(self):
        controller = ReceiverController("kde", Mock())
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            side_effect=lambda _element, prop: prop in {"fullscreen", "force-aspect-ratio"},
        ):
            args = controller._sink_args("waylandsink")
        self.assertIn("fullscreen=true", args)
        self.assertIn("force-aspect-ratio=false", args)

    def test_hardware_failure_retries_next_sink_without_software_decoder(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 4
        controller.decoder = "Hardware"
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.receiver_host = "10.0.0.2"
        controller.receiver_port = 7110
        controller.sink_candidates = ["glimagesink", "autovideosink"]
        controller.sink_index = 0
        controller.sink = "glimagesink"
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        with patch.object(controller, "_launch_external_pipeline") as launch:
            controller._finished(1, None, controller.process, generation=4)
        launch.assert_called_once_with("10.0.0.2", 7110, 4)
        self.assertEqual(controller.sink, "autovideosink")
        self.assertEqual(controller.decoder_args, ["vah264dec"])
        self.assertEqual(controller.decoder_label, "VA-API")

    def test_hardware_failure_with_no_remaining_sink_stops_without_software(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 4
        controller.decoder = "Hardware"
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.receiver_host = "10.0.0.2"
        controller.receiver_port = 7110
        controller.sink_candidates = ["glimagesink"]
        controller.sink = "glimagesink"
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        controller._finished(1, None, controller.process, generation=4)
        self.assertEqual(controller.status, "Hardware receiver pipeline failed — see logs")
        self.assertFalse(controller.receiving)
        self.assertEqual(controller.decoder_args, ["vah264dec"])

    def test_hardware_mode_without_vaapi_decoder_does_not_start_software(self):
        controller = ReceiverController("kde", Mock())
        with (
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                return_value=False,
            ),
            patch.object(controller, "_start_attempt") as start,
        ):
            controller.connect("10.0.0.2", 7110, False, "", "", "Hardware")
        start.assert_not_called()
        self.assertEqual(
            controller.status,
            "Hardware decoder unavailable — install the GStreamer VA-API decoder",
        )


class ReceiverVideoWindowTest(unittest.TestCase):
    def test_receiver_video_window_fills_native_surface_from_window_size(self):
        from monitorize.desktop.main_window import ReceiverVideoWindow

        backend = Mock()
        window = Mock()
        window.backend = backend
        window.width.return_value = 1920
        window.height.return_value = 1080
        window.video_surface = Mock()
        ReceiverVideoWindow.sync_video_geometry(window)
        window.video_surface.setGeometry.assert_called_once_with(0, 0, 1920, 1080)
        backend.receiver.sync_video_geometry.assert_called_once()

    def test_receiver_video_window_waits_for_valid_surface_before_binding(self):
        from monitorize.desktop.main_window import ReceiverVideoWindow

        backend = Mock()
        backend.isReceiving = True
        window = Mock()
        window.backend = backend
        window.SYNC_DELAYS_MS = ReceiverVideoWindow.SYNC_DELAYS_MS
        window.isVisible.return_value = True
        window.video_surface = Mock()
        window.video_surface.width.return_value = 1
        window.video_surface.height.return_value = 1080
        window.sync_video_geometry = Mock()
        with patch("monitorize.desktop.main_window.QTimer.singleShot") as single_shot:
            ReceiverVideoWindow._bind_receiver_video_surface(window)
        backend.setReceiverVideoItem.assert_not_called()
        single_shot.assert_called_once()

    def test_receiver_video_window_binds_and_schedules_geometry_resyncs(self):
        from monitorize.desktop.main_window import ReceiverVideoWindow

        backend = Mock()
        backend.isReceiving = True
        window = Mock()
        window.backend = backend
        window.SYNC_DELAYS_MS = ReceiverVideoWindow.SYNC_DELAYS_MS
        window.isVisible.return_value = True
        window.video_surface = Mock()
        window.video_surface.width.return_value = 1920
        window.video_surface.height.return_value = 1080
        window.sync_video_geometry = Mock()
        with patch("monitorize.desktop.main_window.QTimer.singleShot") as single_shot:
            ReceiverVideoWindow._bind_receiver_video_surface(window)
        backend.setReceiverVideoItem.assert_called_once_with(window.video_surface)
        self.assertEqual(single_shot.call_count, len(ReceiverVideoWindow.SYNC_DELAYS_MS))

    def test_monitorize_window_uses_dedicated_receiver_window(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.receiver.should_use_embedded_window.return_value = True
        MonitorizeWindow._sync_receiver_fullscreen(window, True)
        window.receiver_video_window.show_receiver.assert_called_once()
        window.showFullScreen.assert_not_called()
        window.content_stack.setCurrentWidget.assert_not_called()

    def test_monitorize_window_does_not_cover_external_receiver_sink(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        window.backend.receiver.should_use_embedded_window.return_value = False
        MonitorizeWindow._sync_receiver_fullscreen(window, True)
        window.receiver_video_window.show_receiver.assert_not_called()

    def test_monitorize_window_hides_dedicated_receiver_window_on_stop(self):
        from monitorize.desktop.main_window import MonitorizeWindow

        window = Mock()
        MonitorizeWindow._sync_receiver_fullscreen(window, False)
        window.receiver_video_window.hide_receiver.assert_called_once()

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

    def test_tls_ready_enters_receiver_session_and_launches_pipeline(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 2
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.tls_process = process_mock()
        controller.tls_process.readAllStandardOutput.return_value = b"[TLS RECEIVER] READY\n"
        with patch.object(controller, "_launch_pipeline") as launch:
            controller._read_tls(controller.tls_process, generation=2)
        launch.assert_called_once_with("127.0.0.1", 17110, 2)
        self.assertTrue(controller.receiving)

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
                self.assertTrue(loaded["enable_touch"])
                self.assertFalse(loaded["enable_stylus_features"])
                settings.save_second_display_settings(
                    resolution="1920x1080 (16:9)", fps="60", bitrate="8000",
                    encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency", enable_touch=False,
                    enable_stylus_features=True,
                )
                self.assertFalse(settings.load_second_display_settings()["enable_touch"])
                self.assertTrue(
                    settings.load_second_display_settings()["enable_stylus_features"]
                )
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_second_display_custom_mode_round_trips_sanitized_values(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings.save_second_display_settings(
                    resolution="Custom...", custom_w="3441", custom_h="1441",
                    fps="Custom...", custom_fps="999", bitrate="8000",
                    encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency",
                )

                loaded = settings.load_second_display_settings()

                self.assertEqual(loaded["resolution"], "Custom...")
                self.assertEqual(loaded["custom_w"], "3440")
                self.assertEqual(loaded["custom_h"], "1440")
                self.assertEqual(loaded["fps"], "Custom...")
                self.assertEqual(loaded["custom_fps"], "240")
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_first_run_wifi_defaults_are_plain_cpu_1080p_16mbps(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                loaded = settings.load_wifi_settings()
                self.assertEqual(loaded["resolution"], "1920x1080")
                self.assertEqual(loaded["bitrate"], "16000")
                self.assertEqual(loaded["encoder"], "Software (CPU / x264enc)")
                self.assertFalse(loaded["use_encryption"])
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_first_run_usb_defaults_are_cpu_1080p_16mbps(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                loaded = settings.load_usb_settings()
                self.assertEqual(loaded["resolution"], "1920x1080")
                self.assertEqual(loaded["bitrate"], "16000")
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
                        "third": {
                            "enabled": True,
                            "resolution": "1280x720",
                            "fps": "60",
                            "bitrate": "8000",
                            "encoder": "Software (CPU / x264enc)",
                            "encoder_profile": "Low Latency",
                            "enable_touch": False,
                        },
                    })
                settings.save_presets(presets)
                loaded = settings.load_presets()
                self.assertEqual(len(loaded), 4)
                self.assertEqual(loaded[0]["name"], "Preset 0")
                self.assertEqual(loaded[0]["primary"]["encoder_profile"], "Balanced")
                self.assertTrue(loaded[0]["wifi"]["use_encryption"])
                self.assertTrue(loaded[0]["general"]["minimize_to_tray"])
                self.assertFalse(loaded[0]["third"]["enable_touch"])
                self.assertFalse(loaded[0]["third"]["enable_stylus_features"])
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
        controller = StreamingController("hyprland", "10.0.0.1", discovery)
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
            "10.0.0.1", False, False, 60, 60
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
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        bus.disconnect.assert_called_once()
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
        controller.gnome_outputs = {"primary": "Meta-0"}
        controller._on_gnome_monitors_changed()
        self.assertTrue(controller.gnome_layout_change_timer.isActive())
        controller.gnome_layout_change_timer.stop()

    def test_gnome_layout_change_save_does_not_reconnect(self):
        controller = self.gnome_controller()
        controller.gnome_outputs = {"primary": "Meta-0"}
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                return_value=True,
            ) as save,
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.gnome_layout_change_timer.timeout.emit()
        save.assert_called_once_with("primary", role_connectors={"primary": "Meta-0"})
        launch.assert_not_called()

    def test_stop_cleans_processes_and_advertisement(self):
        discovery = Mock()
        controller = StreamingController("hyprland", "10.0.0.1", discovery)
        controller.streaming = True
        controller.streamer = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes") as stop,
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        stop.assert_called_once()
        discovery.stop_advertising.assert_called_once()
        self.assertFalse(controller.streaming)

    def test_kde_native_stop_cleans_tracked_pipeline_and_helper(self):
        discovery = Mock()
        controller = StreamingController("kde", "10.0.0.1", discovery)
        controller.streaming = True
        controller.env = Mock()
        controller.streamer = process_mock()
        controller.input_bridge = process_mock()
        controller.gst_pids = {12345}
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes", return_value=True) as stop,
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill_pids,
            patch("monitorize.desktop.streaming_controller.kill_patterns") as kill_patterns_mock,
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        stop.assert_called_once()
        kill_pids.assert_called_once_with({12345})
        patterns = kill_patterns_mock.call_args.args
        self.assertIn("monitorize-kde-virtual-output", patterns)
        discovery.stop_advertising.assert_called_once()

    def test_kde_stop_still_cleans_tracked_pipeline_after_terminate_failure(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.streamer = process_mock()
        controller.gst_pids = {12345}
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes", return_value=False),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill_pids,
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
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
        self.assertNotIn("--gnome-primary", args)

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

    def test_gnome_mirror_input_targets_primary_monitor(self):
        controller = self.gnome_controller()
        controller.display_type = "Mirror"
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
        self.assertIn("--gnome-primary", args)

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

    def test_input_permission_marker_updates_status(self):
        controller = self.kde_controller()
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[TouchDaemon] ERROR MONITORIZE_UINPUT_PERMISSION: "
            b"Monitorize needs uinput permission.\n"
        )
        controller.input_bridge = process
        controller._read_input(generation=3, process=process)
        self.assertIn("Monitorize udev rule", controller.status)

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

    def test_primary_ready_ignores_saved_third_display(self):
        controller = self.kde_controller()
        with patch.object(controller, "start_third") as start:
            controller._set_primary_ready(True)
        start.assert_not_called()

    def test_active_configuration_keeps_third_display_disabled(self):
        controller = self.kde_controller()
        controller.encoder = "Intel/AMD VA-API (vah264enc)"
        controller.encoder_profile = "Balanced"
        controller.env.value.return_value = "Speed"
        controller.runtime_general = {
            "minimize_to_tray": True,
            "enable_touch": True,
            "enable_stylus_features": True,
        }
        config = controller.active_configuration()
        self.assertEqual(config["primary"]["resolution"], "1920x1200")
        self.assertEqual(config["primary"]["encoder_profile"], "Balanced")
        self.assertEqual(config["wifi"]["stream_type"], "Speed")
        self.assertEqual(config["third"], {"enabled": False})
        self.assertTrue(config["general"]["enable_stylus_features"])

    def test_active_configuration_includes_active_third_display_settings(self):
        controller = self.kde_controller()
        controller.encoder = "Intel/AMD VA-API (vah264enc)"
        controller.encoder_profile = "Balanced"
        controller.env.value.return_value = "Speed"
        controller.third_streaming = True
        controller.third_width = 1920
        controller.third_height = 1080
        controller.third_fps = 60
        controller.third_bitrate = 12000
        controller.third_encoder = "Software (CPU / x264enc)"
        controller.third_encoder_profile = "Quality"

        config = controller.active_configuration()

        self.assertEqual(config["third"], {
            "enabled": True,
            "resolution": "1920x1080",
            "fps": "60",
            "bitrate": "12000",
            "encoder": "Software (CPU / x264enc)",
            "encoder_profile": "Quality",
            "enable_touch": True,
            "enable_stylus_features": False,
        })

    def test_kde_third_display_uses_distinct_native_virtual_slot(self):
        discovery = Mock()
        controller = StreamingController("kde", "10.0.0.1", discovery)
        controller.streaming = True
        controller.wifi = True
        controller.encrypted = False
        controller.primary_ready = True
        controller.fps = 60
        events = []
        controller.secondStreamChanged.connect(events.append)
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "1920x1080", "60", "8000",
                "Software (CPU / x264enc)", "Low Latency",
            )

        args = process.start.call_args.args[1]
        env = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(args[:2], ["-m", "monitorize.streaming.Streamer_kde"])
        self.assertEqual(args[-1], "wifi")
        self.assertEqual(
            env.value("MONITORIZE_KDE_VIRTUAL_SLOT"), "additional"
        )
        self.assertEqual(env.value("MONITORIZE_PORT"), "7114")
        self.assertEqual(env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "")
        self.assertEqual(events, [True])
        discovery.advertise.assert_called_once_with(
            "10.0.0.1", False, False, 60, 60
        )

    def test_third_custom_mode_uses_shared_resolution_and_fps_sanitizers(self):
        controller = self.kde_controller()
        controller.streaming = True
        controller.wifi = True
        controller.primary_ready = True
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "3441x1441", "75", "8000",
                "Software (CPU / x264enc)", "Low Latency",
            )

        args = process.start.call_args.args[1]
        self.assertEqual(args[2:6], ["3440", "1440", "75", "8000"])

    def test_hyprland_third_display_creates_headless_output_before_picker(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.primary_ready = True
        process = process_mock()

        with (
            patch.object(
                controller.display, "prepare_hyprland", return_value=("HEADLESS-2", "")
            ) as prepare,
            patch.object(controller.display, "wait_for_headless_ready", return_value=True) as ready,
            patch("monitorize.desktop.streaming_controller.QProcess", return_value=process),
        ):
            controller.start_third(
                "1280x720", "30", "4000",
                "Software (CPU / x264enc)", "Balanced",
            )

        args = process.start.call_args.args[1]
        env = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(args[:2], ["-m", "monitorize.streaming.Streamer_hyprland"])
        self.assertEqual(args[2:6], ["1280", "720", "30", "4000"])
        self.assertEqual(args[-1], "HEADLESS-2")
        prepare.assert_called_once_with(1280, 720, 30, "additional")
        ready.assert_called_once_with("HEADLESS-2", 1280, 720)
        self.assertEqual(env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "1")
        self.assertIn("HEADLESS-2", env.value("MONITORIZE_PORTAL_SELECTOR_HINT"))

    def test_hyprland_third_output_failure_keeps_primary_streaming(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        controller.streaming = True
        controller.primary_ready = True
        events = []
        controller.secondStreamChanged.connect(events.append)
        with (
            patch.object(
                controller.display, "prepare_hyprland", return_value=("", "headless failed")
            ),
            patch("monitorize.desktop.streaming_controller.QProcess") as process,
        ):
            controller.start_third(
                "1280x720", "30", "4000",
                "Software (CPU / x264enc)", "Balanced",
            )
        process.assert_not_called()
        self.assertTrue(controller.streaming)
        self.assertFalse(controller.third_streaming)
        self.assertEqual(events, [False])

    def test_hyprland_third_stop_removes_only_additional_output(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        controller.streaming = True
        controller.third_streaming = True
        controller.third_streamer = process_mock()
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.display, "remove_hyprland_output") as remove,
        ):
            controller.stop_third()
        remove.assert_called_once_with("additional")

    def test_encrypted_third_display_uses_tls_backend_port(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.encrypted = True
        controller.primary_ready = True
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "1920x1080", "60", "8000",
                "Software (CPU / x264enc)", "Low Latency",
            )

        env = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(env.value("MONITORIZE_PORT"), "7115")
        self.assertEqual(env.value("MONITORIZE_HOST"), "127.0.0.1")

    def test_third_touch_uses_its_wifi_port_only_when_enabled(self):
        controller = self.kde_controller()
        controller.third_streaming = True
        controller.third_ready = True
        controller.third_generation = 5
        controller.third_output = "Virtual-Monitorize-2"
        controller.third_width, controller.third_height = 1280, 720
        controller.third_touch_enabled = True
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._maybe_launch_third_input(5)

        args = process.start.call_args.args[1]
        env = process.setProcessEnvironment.call_args.args[0]
        self.assertIn("--additional", args)
        self.assertEqual(args[args.index("--port") + 1], "7117")
        self.assertIn("--wifi", args)
        self.assertEqual(env.value("MONITORIZE_OUTPUT"), "Virtual-Monitorize-2")

        controller.third_input_bridge = None
        controller.third_input_launched = False
        controller.third_touch_enabled = False
        with patch("monitorize.desktop.streaming_controller.QProcess") as disabled:
            controller._maybe_launch_third_input(5)
        disabled.assert_not_called()

    def test_encrypted_third_touch_uses_tls_udp_backend_port(self):
        controller = self.kde_controller()
        controller.encrypted = True
        controller.third_streaming = True
        controller.third_ready = True
        controller.third_generation = 5
        controller.third_output = "Virtual-Monitorize-2"
        controller.third_width, controller.third_height = 1280, 720
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._maybe_launch_third_input(5)

        args = process.start.call_args.args[1]
        self.assertEqual(args[args.index("--port") + 1], "7118")
        self.assertIn("--wifi", args)
        self.assertIn("--local-udp", args)

    def test_third_stylus_can_run_without_additional_touch(self):
        controller = self.kde_controller()
        controller.third_streaming = True
        controller.third_ready = True
        controller.third_generation = 5
        controller.third_output = "Virtual-Monitorize-2"
        controller.third_width, controller.third_height = 1280, 720
        controller.third_touch_enabled = False
        controller.third_stylus_enabled = True
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._maybe_launch_third_input(5)

        args = process.start.call_args.args[1]
        self.assertIn("--stylus-features", args)
        self.assertIn("--stylus-only", args)
        self.assertIn("--additional", args)

    def test_third_usb_touch_uses_tcp_7115_and_matching_reverse_rules(self):
        controller = self.kde_controller()
        controller.wifi = False
        controller.third_streaming = True
        controller.third_ready = True
        controller.third_generation = 5
        controller.third_output = "Virtual-Monitorize-2"
        controller.third_width, controller.third_height = 1280, 720
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._maybe_launch_third_input(5)

        args = process.start.call_args.args[1]
        self.assertEqual(args[args.index("--port") + 1], "7115")
        self.assertNotIn("--wifi", args)

        with patch.object(controller, "_run_adb_reverse") as reverse:
            controller._configure_third_usb_reverse(True)
        self.assertEqual(reverse.call_args_list[-1].args, ("tcp:7115", "tcp:7115"))

    def test_gnome_third_display_uses_native_virtual_streamer(self):
        discovery = Mock()
        controller = StreamingController("gnome", "10.0.0.1", discovery)
        controller.streaming = True
        controller.primary_ready = True
        events = []
        logs = []
        controller.secondStreamChanged.connect(events.append)
        controller.logAppended.connect(lambda label, message: logs.append((label, message)))

        process = process_mock()
        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "1920x1080", "60", "8000",
                "Software (CPU / x264enc)", "Low Latency",
            )

        args = process.start.call_args.args[1]
        self.assertEqual(args[1], "monitorize.streaming.Streamer_gnome")
        self.assertEqual(events, [True])
        self.assertTrue(any("Creating GNOME virtual display" in message for _, message in logs))

    def test_third_availability_waits_for_pipeline_ready(self):
        discovery = Mock()
        controller = StreamingController("kde", "10.0.0.1", discovery)
        controller.streaming = True
        controller.wifi = True
        controller.third_generation = 2
        controller.third_streaming = True
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[Portal] Got PipeWire node=42 fd=9\n"
            b"New clock: GstSystemClock\n"
        )
        controller.third_streamer = process

        controller._read_third_streamer(2, process)

        self.assertTrue(controller.third_ready)
        self.assertEqual(
            discovery.advertise.call_args_list[-1].args,
            ("10.0.0.1", False, True, 60, 60),
        )

    def test_stale_third_streamer_output_is_ignored(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.third_generation = 2
        controller.third_streaming = True
        old_process = process_mock()
        old_process.readAllStandardOutput.return_value = b"New clock: GstSystemClock\n"
        controller.third_streamer = process_mock()

        controller._read_third_streamer(1, old_process)

        self.assertFalse(controller.third_ready)

    def test_stale_third_streamer_exit_is_ignored(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.third_generation = 2
        controller.third_streaming = True
        old_process = process_mock()
        controller.third_streamer = process_mock()

        controller._third_streamer_finished(1, None, 1, old_process)

        self.assertTrue(controller.third_streaming)

    def test_stop_third_leaves_primary_streaming(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.third_streaming = True
        controller.third_ready = True
        third_process = process_mock()
        controller.third_streamer = third_process
        controller.third_gst_pids = {123}

        with (
            patch("monitorize.desktop.streaming_controller.stop_processes") as stop,
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill_pids,
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
        ):
            controller.stop_third()

        stop.assert_called_once_with(third_process)
        kill_pids.assert_called_once_with({123})
        self.assertTrue(controller.streaming)
        self.assertFalse(controller.third_streaming)

    def test_stale_streamer_exit_does_not_restart_gnome(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        old_process = process_mock()
        controller.streamer = process_mock()
        controller._streamer_finished(1, None, 6, old_process)
        self.assertTrue(controller.streaming)

    def legacy_restart_gnome_saves_virtual_layout_before_relaunch(self):
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

    def legacy_restart_gnome_logs_failed_layout_save_but_relaunches(self):
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

    def test_gnome_layout_save_uses_identified_primary(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Extend"
        controller.gnome_outputs = {"primary": "Meta-0"}
        with patch(
            "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout"
        ) as save:
            controller._save_gnome_virtual_layout()
        save.assert_called_once_with("primary", role_connectors={"primary": "Meta-0"})

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
        controller.gnome_outputs = {"primary": "Meta-0"}
        events = []
        with (
            patch(
                "monitorize.desktop.streaming_controller.save_current_gnome_virtual_layout",
                side_effect=lambda *_args, **_kwargs: events.append("save"),
            ) as save,
            patch(
                "monitorize.desktop.streaming_controller.stop_processes",
                side_effect=lambda *_args, **_kwargs: events.append("stop") or True,
            ),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch.object(controller.display, "cleanup"),
        ):
            controller.stop()
        save.assert_called_once_with("primary", role_connectors={"primary": "Meta-0"})
        self.assertEqual(events[:2], ["save", "stop"])

    def test_stop_logs_failed_gnome_layout_save_but_stops(self):
        controller = StreamingController("gnome", "10.0.0.1", Mock())
        controller.streaming = True
        controller.display_type = "Extend"
        controller.streamer = process_mock()
        controller.gnome_outputs = {"primary": "Meta-0"}
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

    def test_stale_streamer_output_is_ignored(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
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

    def test_kde_native_capture_starts_input_after_exact_output_ready(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.input_launched = False
        controller.env = Mock()
        controller.env.value.return_value = "primary"
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b'MONITORIZE_EVENT {"type":"kde_output_ready","slot":"primary",'
            b'"name":"Virtual-Monitorize-1","width":1920,"height":1200,'
            b'"refresh_rate":60}\n'
            b'MONITORIZE_EVENT {"type":"kde_capture_ready","slot":"primary",'
            b'"node_id":42,"target_object":"88"}\n'
        )
        controller.streamer = process
        with patch("monitorize.desktop.streaming_controller.QTimer.singleShot") as single_shot:
            controller._read_streamer(7, process)
            self.assertTrue(controller.input_launched)
            single_shot.assert_called_once()
        controller.env.insert.assert_called_with(
            "MONITORIZE_OUTPUT", "Virtual-Monitorize-1"
        )

    def test_kde_native_start_failure_stops_streaming(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "primary"
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
            "KDE streaming setup failed — see logs",
        )

    def test_kde_native_explicit_error_is_not_overwritten_on_exit(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.env = Mock()
        controller.env.value.return_value = "primary"
        controller.streamer = process_mock()
        controller.status = "KDE native helper is missing"
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
        ):
            controller._streamer_finished(
                1, None, controller.generation, controller.streamer
            )
        self.assertEqual(
            controller.status, "KDE native helper is missing"
        )

    def test_kde_native_output_ready_rejects_unexpected_name(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.generation = 7
        controller.env = Mock()
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b'MONITORIZE_EVENT {"type":"kde_output_ready","slot":"primary",'
            b'"name":"eDP-1","width":1920,"height":1200,"refresh_rate":60}\n'
        )
        controller.streamer = process
        controller._read_streamer(7, process)
        controller.env.insert.assert_not_called()

    def test_invalid_stream_settings_are_sanitized_before_start(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
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
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Low Latency", False,
            )
        self.assertEqual(events, [True])

    def test_stream_start_sets_encoder_profile_environment(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Balanced", False,
            )
        self.assertEqual(controller.env.value("MONITORIZE_ENCODER_PROFILE"), "Balanced")

    def test_kde_extend_start_uses_native_primary_slot(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        events = []
        controller.streamingChanged.connect(events.append)
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_launch_streamer") as launch,
        ):
            controller.start(
                "1280x800", "60", "8000", "Extend", "Software",
                "Low Latency", False,
            )
        self.assertEqual(events, [True])
        self.assertTrue(controller.streaming)
        self.assertEqual(
            controller.env.value("MONITORIZE_KDE_VIRTUAL_SLOT"), "primary"
        )
        self.assertEqual(controller.env.value("MONITORIZE_PORTAL_SOURCE_TYPE"), "")
        launch.assert_called_once_with()

class ProcessUtilsTest(unittest.TestCase):
    def test_kill_patterns_does_not_call_broad_pkill(self):
        with patch("monitorize.platform.process_utils.subprocess.run") as run:
            process_utils.kill_patterns("definitely-no-monitorize-process")
        run.assert_not_called()

    def test_kill_patterns_noops_when_proc_is_unavailable(self):
        with (
            patch("monitorize.platform.process_utils.Path.exists", return_value=False),
            patch("monitorize.platform.process_utils.Path.iterdir") as iterdir,
        ):
            process_utils.kill_patterns("gst-launch-1.0")
        iterdir.assert_not_called()


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
    def native_outputs(mode_registered=False, mode_active=False):
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
                "uuid": "uuid-primary",
                "name": "Virtual-Monitorize-1",
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
                "uuid": "uuid-edp",
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

    def test_virtual_slots_have_distinct_stable_names(self):
        primary = kde_virtual_monitor.virtual_slot("primary")
        additional = kde_virtual_monitor.virtual_slot("additional")
        self.assertEqual(primary["output_name"], "Virtual-Monitorize-1")
        self.assertEqual(
            additional["output_name"], "Virtual-Monitorize-2"
        )
        self.assertNotEqual(primary["base_name"], additional["base_name"])

    def test_native_mode_registration_targets_exact_output_id(self):
        state = {"registered": False, "active": False}

        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                outputs = self.native_outputs(
                    mode_registered=state["registered"],
                    mode_active=state["active"],
                )
                return Mock(
                    returncode=0,
                    stdout=json.dumps({"outputs": outputs}),
                    stderr="",
                )
            if "addCustomMode.1920.1200.60000.reduced" in args[1]:
                state["registered"] = True
                return Mock(returncode=0, stdout="", stderr="")
            if args[1].endswith(".mode.2"):
                state["active"] = True
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {args}")

        with (
            patch(
                "monitorize.platform.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ) as run,
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
        ):
            ok, details, message = (
                kde_virtual_monitor.configure_native_virtual_output(
                    "Virtual-Monitorize-1",
                    1920,
                    1200,
                    60,
                    attempts=2,
                    delay=0,
                )
            )

        self.assertTrue(ok, message)
        self.assertEqual(details["uuid"], "uuid-primary")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "kscreen-doctor",
                (
                    "output.1."
                    "addCustomMode.1920.1200.60000.reduced"
                ),
            ],
            commands,
        )
        self.assertIn(
            [
                "kscreen-doctor",
                "output.1.mode.2",
            ],
            commands,
        )
        self.assertFalse(any(".scale." in " ".join(command) for command in commands))
        self.assertFalse(any("output.2." in " ".join(command) for command in commands))

    def test_native_mode_registration_falls_back_to_full_blanking_once(self):
        state = {"registered": False, "active": False}

        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                outputs = self.native_outputs(
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
            if "addCustomMode.1920.1200.60000.reduced" in args[1]:
                return Mock(returncode=0, stdout="", stderr="")
            if args[1].endswith(".mode.2"):
                state["active"] = True
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {args}")

        with (
            patch(
                "monitorize.platform.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ) as run,
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
        ):
            ok, _details, message = (
                kde_virtual_monitor.configure_native_virtual_output(
                    "Virtual-Monitorize-1",
                    1920,
                    1200,
                    60,
                    attempts=1,
                    delay=0,
                )
            )

        self.assertTrue(ok, message)
        commands = [call.args[0][1] for call in run.call_args_list if len(call.args[0]) > 1]
        self.assertIn(
            "output.1.addCustomMode.1920.1200.60000.full",
            commands,
        )
        self.assertIn(
            "output.1.addCustomMode.1920.1200.60000.reduced",
            commands,
        )

    def test_native_mode_registration_accepts_cvt_rounded_width(self):
        state = {"registered": False, "active": False}

        def rounded_outputs():
            outputs = self.native_outputs()
            if state["registered"]:
                outputs[0]["modes"].append({
                    "id": "2",
                    "name": "2336x1080@60",
                    "refreshRate": 59.952,
                    "size": {"width": 2336, "height": 1080},
                })
            outputs[0]["currentModeId"] = "2" if state["active"] else "1"
            return outputs

        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                return Mock(
                    returncode=0,
                    stdout=json.dumps({"outputs": rounded_outputs()}),
                    stderr="",
                )
            if "addCustomMode.2340.1080.60000.reduced" in args[1]:
                state["registered"] = True
                return Mock(returncode=0, stdout="", stderr="")
            if args[1].endswith(".mode.2"):
                state["active"] = True
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {args}")

        with (
            patch(
                "monitorize.platform.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ),
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
        ):
            ok, details, message = (
                kde_virtual_monitor.configure_native_virtual_output(
                    "Virtual-Monitorize-1",
                    2340,
                    1080,
                    60,
                    attempts=2,
                    delay=0,
                )
            )

        self.assertTrue(ok, message)
        self.assertEqual(details["width"], 2336)
        self.assertTrue(details["rounded"])
        self.assertIn("2336x1080", message)
        self.assertIn("requested 2340x1080@60", message)

    def test_native_configuration_leaves_layout_and_scale_to_kwin(self):
        def fake_run(args, **_kwargs):
            if args == ["kscreen-doctor", "-j"]:
                return Mock(
                    returncode=0,
                    stdout=json.dumps({"outputs": self.native_outputs(True, True)}),
                    stderr="",
                )
            return Mock(returncode=0, stdout="", stderr="")

        with (
            patch(
                "monitorize.platform.kde_virtual_monitor.subprocess.run",
                side_effect=fake_run,
            ) as run,
            patch("monitorize.platform.kde_virtual_monitor.time.sleep"),
        ):
            ok, _details, message = (
                kde_virtual_monitor.configure_native_virtual_output(
                    "Virtual-Monitorize-1",
                    1920,
                    1200,
                    60,
                    attempts=1,
                    delay=0,
                )
            )
        self.assertTrue(ok, message)
        commands = [call.args[0] for call in run.call_args_list]
        mutations = [command[1] for command in commands if len(command) > 1]
        self.assertFalse(any(".position." in command for command in mutations))
        self.assertFalse(any(".rotation." in command for command in mutations))
        self.assertFalse(any(".scale." in command for command in mutations))

    def test_output_presence_uses_exact_stable_name(self):
        outputs = self.native_outputs()
        outputs.insert(1, {
            "id": 3,
            "uuid": "uuid-other",
            "name": "Virtual-other",
            "connected": True,
            "enabled": True,
            "priority": 3,
            "modes": [],
        })
        with patch(
            "monitorize.platform.kde_virtual_monitor.kde_outputs",
            return_value=outputs,
        ):
            output = kde_virtual_monitor.find_kde_output(
                "Virtual-Monitorize-1"
            )
            self.assertEqual(output["uuid"], "uuid-primary")
            self.assertFalse(
                kde_virtual_monitor.output_is_active(
                    "Virtual-Monitorize-2"
                )
            )

    def test_native_configuration_uses_output_id_when_uuid_is_absent(self):
        outputs = self.native_outputs(True, True)
        outputs[0].pop("uuid")
        with patch(
            "monitorize.platform.kde_virtual_monitor.kde_outputs",
            return_value=outputs,
        ):
            ok, details, message = (
                kde_virtual_monitor.configure_native_virtual_output(
                    "Virtual-Monitorize-1",
                    1920,
                    1200,
                    60,
                    attempts=1,
                    delay=0,
                )
            )
        self.assertTrue(ok, message)
        self.assertEqual(details["uuid"], "")
        self.assertEqual(details["selector"], "1")


class KdeNativeStreamerTest(unittest.TestCase):
    def test_native_stream_uses_second_stream_serial_after_mode_configuration(self):
        helper = Mock()
        helper.stdin = Mock()
        helper.stdout = Mock()
        helper.poll.return_value = 0
        gst = Mock()
        gst.poll.return_value = 0
        gst.returncode = 0
        actual = {
            "name": "Virtual-Monitorize-1",
            "uuid": "uuid-primary",
            "width": 1920,
            "height": 1200,
            "refresh_rate": 59.95,
            "mode_id": "2",
            "rounded": True,
        }
        with (
            patch.object(kde_native_streamer, "wait_for_output_absent", return_value=True),
            patch.object(kde_native_streamer, "find_helper", return_value="/helper"),
            patch.object(kde_native_streamer.subprocess, "Popen", return_value=helper),
            patch.object(
                kde_native_streamer,
                "_read_helper_event",
                side_effect=[
                    {
                        "event": "owner_ready",
                        "name": "Virtual-Monitorize-1",
                        "node_id": 10,
                        "target_object": "100",
                    },
                    {
                        "event": "capture_ready",
                        "name": "Virtual-Monitorize-1",
                        "node_id": 11,
                        "target_object": "101",
                    },
                ],
            ),
            patch.object(
                kde_native_streamer,
                "configure_native_virtual_output",
                return_value=(True, actual, "configured"),
            ),
            patch.object(
                kde_native_streamer,
                "launch_with_fallback",
                return_value=gst,
            ) as launch,
            patch.object(kde_native_streamer.signal, "signal"),
        ):
            result = kde_native_streamer.run_native_streamer(
                "primary", 1920, 1200, 60, 8000, "wifi", 7110, None,
                "0.0.0.0",
            )

        self.assertEqual(result, 0)
        helper.stdin.write.assert_called_once_with("capture\n")
        self.assertEqual(launch.call_args.kwargs["target_object"], "101")
        self.assertTrue(launch.call_args.kwargs["preserve_source_size"])
        self.assertTrue(launch.call_args.kwargs["preserve_source_rate"])

    def test_native_stream_refuses_duplicate_slot_before_spawning_helper(self):
        with (
            patch.object(kde_native_streamer, "wait_for_output_absent", return_value=False),
            patch.object(kde_native_streamer.subprocess, "Popen") as popen,
        ):
            result = kde_native_streamer.run_native_streamer(
                "additional", 1920, 1080, 60, 8000, "wifi", 7114,
                None, "0.0.0.0",
            )
        self.assertEqual(result, 1)
        popen.assert_not_called()


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
        self.assertTrue(options["is-platform"])

    def test_record_virtual_marks_stock_mutter_output_as_platform(self):
        session = Mock()
        Streamer_gnome._record_virtual(session, self.FakeDbus, Streamer_gnome.StreamerConfig())
        self.assertTrue(session.RecordVirtual.call_args.args[0]["is-platform"])

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

    def test_native_pipewire_target_preserves_kwin_source_rate(self):
        text = self._pipeline_text(
            target_object="101", preserve_source_rate=True
        )
        self.assertIn("target-object=101", text)
        self.assertNotIn("videorate", text)
        self.assertNotIn("framerate=60/1", text)

    def test_low_latency_encoder_profile_keeps_current_nvenc_settings(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", encoder_profile="Low Latency"
        )
        self.assertIn("preset=p1", text)
        self.assertIn("tune=ultra-low-latency", text)
        self.assertIn("rc-lookahead=0", text)
        self.assertIn("bframes=0", text)
        self.assertIn("vbv-buffer-size=134", text)
        self.assertIn("strict-gop=true", text)
        self.assertIn("repeat-sequence-header=true", text)

    def test_stability_nvenc_uses_short_gop_without_unsupported_intra_refresh(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", stream_type="Stability"
        )
        self.assertIn("gop-size=15", text)
        self.assertIn("repeat-sequence-header=true", text)
        self.assertNotIn("intra-refresh", text)

    def test_nvenc_gl_path_preserves_dmabuf_and_uses_gl_memory(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", nvidia_memory="gl"
        )
        self.assertIn("always-copy=false", text)
        self.assertIn("glupload", text)
        self.assertIn("glcolorconvert", text)
        self.assertIn("glcolorscale", text)
        self.assertIn("memory:GLMemory", text)
        self.assertIn("format=RGBA", text)
        self.assertNotIn("format=NV12", text)
        self.assertNotIn("cudaupload", text)

    def test_nvenc_system_fallback_keeps_hardware_encoder(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", nvidia_memory="system"
        )
        self.assertIn("always-copy=true", text)
        self.assertIn("videoconvert", text)
        self.assertIn("format=NV12", text)
        self.assertIn("nvh264enc", text)
        self.assertNotIn("cudaupload", text)

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
        proc.wait.side_effect = TimeoutExpired("gst-launch-1.0", 1.0)
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

    def test_nvenc_launch_falls_back_from_gl_to_cuda(self):
        failed = Mock(pid=1, returncode=1)
        failed.wait.return_value = 1
        cuda = Mock(pid=2, returncode=None)
        cuda.wait.side_effect = TimeoutExpired("gst-launch-1.0", 1.0)
        with (
            patch(
                "monitorize.streaming.pipeline_builder._nvidia_memory_candidates",
                return_value=["gl", "cuda", "system"],
            ),
            patch(
                "monitorize.streaming.pipeline_builder.subprocess.Popen",
                side_effect=[failed, cuda],
            ) as popen,
        ):
            result = pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, hw_encoder="nvh264enc",
            )
        self.assertIs(result, cuda)
        gl_argv = popen.call_args_list[0].args[0]
        cuda_argv = popen.call_args_list[1].args[0]
        self.assertIn("glupload", gl_argv)
        self.assertIn("cudaupload", cuda_argv)
        self.assertEqual(2, popen.call_count)


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
        controller._authorized_serials = Mock(return_value=["android-1"])
        controller.start()
        self.assertEqual(calls[0][0], ["devices"])
        controller._devices_done(0, None)
        self.assertEqual(controller.serial, "android-1")
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

    def test_only_authorized_adb_devices_are_accepted(self):
        output = "List of devices attached\nready\tdevice\nlocked\tunauthorized\ngone\toffline\n"

        self.assertEqual(["ready"], authorized_adb_serials(output))

    def test_unavailable_selected_device_does_not_configure_reverse_ports(self):
        controller = UsbController()
        calls = []
        controller._run = lambda args, callback: calls.append((args, callback))
        controller._authorized_serials = Mock(return_value=[])

        controller.start("missing-device")
        controller._devices_done(0, None)

        self.assertEqual([["devices"]], [args for args, _ in calls])
        self.assertIn("not authorized", controller.status)
        self.assertFalse(controller.busy)

    def test_usb_page_auto_scans_only_one_authorized_recent_device(self):
        qml_path = Path(__file__).resolve().parents[1] / "monitorize" / "qml" / "UsbStep1Page.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn("function startAutomaticScanIfReady()", qml)
        self.assertIn("if (onlineSerials.length !== 1)", qml)
        self.assertIn("backend.startUsbScan(onlineSerials[0])", qml)
        self.assertNotIn('text: modelData.online ? "Connect"', qml)
        self.assertNotIn("MouseArea {", qml)


class BackendFacadeTest(unittest.TestCase):
    def test_kde_helper_is_built_and_authorized_by_all_packages(self):
        root = Path(__file__).resolve().parents[2]
        installer = (root / "linux" / "scripts" / "install.sh").read_text(
            encoding="utf-8"
        )
        nix_package = (root / "nix" / "package.nix").read_text(encoding="utf-8")
        rpm_spec = (root / "monitorize.spec").read_text(encoding="utf-8")
        rpm_permission = (
            root / "packaging" / "fedora"
            / "monitorize-kde-virtual-output.desktop"
        ).read_text(encoding="utf-8")
        permission = (
            "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1"
        )

        for packaging in (installer, nix_package, rpm_spec):
            self.assertIn("native/kde_virtual_output/build.sh", packaging)
        for packaging in (installer, nix_package, rpm_permission):
            self.assertIn(permission, packaging)
        self.assertIn('HELPER_DESKTOP_FILE="${HELPER_NAME}.desktop"', installer)
        self.assertIn("monitorize-kde-virtual-output.desktop", nix_package)
        self.assertIn("monitorize-kde-virtual-output.desktop", rpm_spec)
        self.assertIn("Exec=/usr/bin/monitorize-kde-virtual-output", rpm_permission)
        self.assertNotIn("BuildArch:      noarch", rpm_spec)
        self.assertIn("kbuildsycoca6", installer)

    def test_fedora_rpm_covers_runtime_permissions_and_firewall(self):
        root = Path(__file__).resolve().parents[2]
        spec = (root / "monitorize.spec").read_text(encoding="utf-8")
        rules = (
            root / "packaging" / "fedora" / "70-monitorize-uinput.rules"
        ).read_text(encoding="utf-8")
        firewall = (
            root / "packaging" / "fedora" / "monitorize.xml"
        ).read_text(encoding="utf-8")
        workflow = (
            root / ".github" / "workflows" / "desktop.yml"
        ).read_text(encoding="utf-8")

        for dependency in (
            "gstreamer1-plugin-libav",
            "gstreamer1-plugins-ugly",
            "pipewire-gstreamer",
            "android-tools",
            "openssl",
            "qt6-qtwayland",
        ):
            self.assertIn(f"Requires:       {dependency}", spec)
        self.assertNotIn("gstreamer1-plugin-openh264", spec)
        self.assertNotIn("Requires:       gstreamer1-plugins-ugly-free", spec)
        self.assertNotIn("groupadd", spec)
        self.assertIn('TAG+="uaccess"', rules)
        self.assertIn("Monitorize-Touch-2", rules)
        self.assertIn("Monitorize-Stylus-2", rules)
        self.assertIn('<include service="mdns"/>', firewall)
        for protocol, port in (("tcp", "7110"), ("tcp", "7114"),
                               ("udp", "7113"), ("udp", "7117")):
            self.assertIn(f'<port protocol="{protocol}" port="{port}"/>', firewall)
        self.assertIn("firewall-zones", spec)
        self.assertIn("--remove-service=monitorize", spec)
        self.assertIn("rpmfusion-free-release", workflow)
        self.assertIn("Clean Fedora 44 RPM install", workflow)

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

    def test_main_menu_presets_align_to_mode_cards(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "MainMenuPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn("readonly property int modeCardWidth: 220", qml)
        self.assertIn("readonly property int modeCardSpacing: 30", qml)
        self.assertIn("readonly property int modeCardCount: backend.canHostStream ? 3 : 1", qml)
        self.assertIn("readonly property int modeCardsWidth", qml)
        self.assertIn("id: modeCardsRow", qml)
        self.assertEqual(qml.count("implicitWidth: page.modeCardWidth"), 3)
        self.assertEqual(qml.count("visible: backend.canHostStream"), 2)
        self.assertEqual(qml.count("Layout.preferredWidth: modeCardsRow.implicitWidth"), 2)
        self.assertIn("width: modeCardsRow.implicitWidth", qml)
        self.assertIn("horizontalAlignment: Text.AlignLeft", qml)
        self.assertIn("id: presetMenu", qml)
        self.assertIn("width: 132", qml)
        self.assertIn("padding: 6", qml)
        self.assertIn("radius: theme.controlRadius", qml)
        self.assertIn('if (backend.detectedDe === "windows") return "Windows"', qml)
        desktop_index = qml.index('text: "Desktop: "')
        desktop_block = qml[desktop_index: qml.index("Layout.alignment: Qt.AlignVCenter", desktop_index)]
        saved_index = qml.index('text: "Saved Presets"')
        saved_block = qml[saved_index: qml.index("horizontalAlignment: Text.AlignLeft", saved_index)]
        preset_name_index = qml.index('text: presetCard.modelData["name"]')
        preset_name_block = qml[preset_name_index: qml.index("elide: Text.ElideRight", preset_name_index)]
        self.assertIn("font.weight: Font.DemiBold", desktop_block)
        self.assertIn("font.weight: Font.DemiBold", saved_block)
        self.assertIn("font.weight: Font.DemiBold", preset_name_block)
        self.assertNotIn("font.weight: Font.Bold", desktop_block)
        self.assertNotIn("font.weight: Font.Bold", saved_block)
        self.assertNotIn("font.weight: Font.Bold", preset_name_block)
        rename_index = qml.index("id: renameMenuItem")
        delete_index = qml.index("id: deleteMenuItem")
        rename_block = qml[rename_index:delete_index]
        delete_block = qml[delete_index: qml.index("leftPadding: 12", delete_index)]
        self.assertIn("color: renameMenuItem.highlighted ? theme.surfaceAlt : theme.surface", rename_block)
        self.assertIn("color: deleteMenuItem.highlighted ? theme.surfaceAlt : theme.surface", delete_block)
        self.assertIn("Behavior on color", rename_block)
        self.assertIn("Behavior on color", delete_block)
        self.assertNotIn("\"transparent\"", rename_block)
        self.assertNotIn("\"transparent\"", delete_block)
        self.assertNotIn("border.color", rename_block)
        self.assertNotIn("border.color", delete_block)

    def test_stream_stop_returns_to_launching_config_page(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "main.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn(
            'property string lastStreamingSetupPage: "MainMenuPage.qml"',
            qml,
        )
        self.assertIn("stack.currentItem.returnPageSource", qml)
        self.assertIn("stack.lastStreamingSetupPage = returnPage.length > 0", qml)
        self.assertIn(
            "stack.replace(stack.lastStreamingSetupPage, StackView.PopTransition)",
            qml,
        )

    def test_receiver_disconnect_returns_to_receiver_setup_page(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "main.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn(
            'property string lastReceiverSetupPage: "ReceiverSetupPage.qml"',
            qml,
        )
        self.assertIn('stack.lastReceiverSetupPage = "ReceiverSetupPage.qml"', qml)
        self.assertIn(
            "stack.replace(stack.lastReceiverSetupPage, StackView.PopTransition)",
            qml,
        )
        self.assertNotIn(
            'stack.replace("MainMenuPage.qml", StackView.PopTransition)',
            qml,
        )

    def test_settings_button_uses_svg_icon(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        qml = (qml_dir / "main.qml").read_text(encoding="utf-8")
        icon = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "assets"
            / "svg"
            / "settings.svg"
        ).read_text(encoding="utf-8")
        settings_index = qml.index('objectName: "settingsIconButton"')
        popup_index = qml.index("Popup {", settings_index)
        settings_block = qml[settings_index:popup_index]
        self.assertIn('source: "../assets/svg/settings.svg"', settings_block)
        self.assertIn("contentItem: Item", settings_block)
        self.assertIn("anchors.centerIn: parent", settings_block)
        self.assertIn("width: 17", settings_block)
        self.assertIn("height: 17", settings_block)
        self.assertIn("sourceSize.width: 17", settings_block)
        self.assertIn("sourceSize.height: 17", settings_block)
        self.assertIn("visible: parent.hovered || parent.down", settings_block)
        self.assertIn("radius: theme.controlRadius", settings_block)
        self.assertNotIn("radius: 18", settings_block)
        self.assertNotIn("border.color: theme.border", settings_block)
        self.assertNotIn('text: "⚙"', settings_block)
        self.assertIn('stroke="#ffffff"', icon)
        self.assertNotIn('stroke="#000000"', icon)
        self.assertIn("visible: backend.canAutostart", qml)

    def test_streaming_config_pages_expose_return_source(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        wifi_qml = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        usb_qml = (qml_dir / "UsbStep2Page.qml").read_text(encoding="utf-8")
        self.assertIn(
            'readonly property string returnPageSource: page.isWifi ? "WifiPage.qml" : "UsbStep2Page.qml"',
            wifi_qml,
        )
        self.assertIn('text: "Use encryption"', wifi_qml)
        self.assertNotIn("Use encryption (recommended)", wifi_qml)
        self.assertIn("WifiPage {", usb_qml)
        self.assertIn("isWifi: false", usb_qml)

    def test_wifi_settings_page_omits_header_and_ip_guidance(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "WifiPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertNotIn("Wi-Fi Mode Settings", qml)
        self.assertNotIn("Your Local IP Address is:", qml)
        self.assertNotIn("Enter this IP in the Monitorize Android app", qml)
        self.assertNotIn("USB Mode  ·  Step 2 of 2", qml)
        self.assertNotIn("Please open the Monitorize app on your tablet.", qml)
        self.assertNotIn("backend.streamingStatus", qml)

    def test_recent_wifi_devices_are_status_only(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "WifiPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertNotIn("Start Server", qml)
        self.assertNotIn("id: wifiItemMouse", qml)
        self.assertIn('text: modelData.online ? "Online" : "Offline"', qml)

    def test_wifi_settings_page_uses_choice_chips_for_option_sets(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        qml_path = qml_dir / "WifiPage.qml"
        qml = qml_path.read_text(encoding="utf-8")
        streaming_qml = (qml_dir / "StreamingPage.qml").read_text(encoding="utf-8")
        chips_qml = (qml_dir / "ChoiceChips.qml").read_text(encoding="utf-8")
        self.assertNotIn("Encrypted mode requires the 6-digit pairing code", qml)
        self.assertNotIn("Encryption is off", qml)
        self.assertNotIn("MUST EXACTLY MATCH", qml)
        self.assertNotIn("WarningCard", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 4)
        self.assertEqual(qml.count("CustomComboBox {"), 2)
        self.assertIn("RowLayout {", chips_qml)
        self.assertIn("property int chipWidth: 112", chips_qml)
        self.assertIn("Layout.preferredWidth: chips.chipWidth", chips_qml)
        self.assertIn("theme.buttonBackgroundHover", chips_qml)
        self.assertIn("theme.buttonBackground", chips_qml)
        self.assertIn("function find(val)", chips_qml)
        self.assertIn('return "NVIDIA NVENC"', chips_qml)
        self.assertNotIn("chipText.implicitWidth + 24", chips_qml)
        self.assertNotIn("rowSpacing", chips_qml)
        self.assertIn("contentItem: Text", chips_qml)
        self.assertNotIn("nvidia.svg", chips_qml)
        self.assertNotIn("amd.svg", chips_qml)
        self.assertNotIn("intel.svg", chips_qml)
        for source in (qml, streaming_qml):
            self.assertIn('"NVIDIA NVENC (nvh264enc)"', source)
            self.assertIn('"Intel/AMD VA-API (vah264enc)"', source)
            self.assertIn('"Software (CPU / x264enc)"', source)
        for control_id in (
            "displayTypeCombo",
            "encoderCombo",
            "encoderProfileCombo",
            "streamTypeCombo",
        ):
            self.assertIn(f"id: {control_id}", qml)

    def test_wifi_usb_settings_page_uses_toggles(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        qml = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        toggle_qml = (qml_dir / "CustomToggle.qml").read_text(encoding="utf-8")
        checkbox_qml = (qml_dir / "CustomCheckBox.qml").read_text(encoding="utf-8")
        self.assertEqual(qml.count("CustomToggle {"), 3)
        self.assertNotIn("CustomCheckBox {", qml)
        self.assertIn('text: "Use encryption"', qml)
        self.assertNotIn('text: "Use encryption (recommended)"', qml)
        self.assertIn("Switch {", toggle_qml)
        self.assertIn("theme.buttonBackgroundHover", toggle_qml)
        self.assertIn("theme.buttonBackground", toggle_qml)
        self.assertIn("toggle.hovered || toggle.down ? theme.surfaceAlt : theme.surface", toggle_qml)
        self.assertIn("toggle.hovered || toggle.down ? theme.borderHover : theme.border", toggle_qml)
        self.assertIn("theme.buttonBackgroundHover", checkbox_qml)
        self.assertIn("theme.buttonBackground", checkbox_qml)
        self.assertIn("chk.hovered || chk.down ? theme.surfaceAlt : theme.surface", checkbox_qml)
        self.assertIn("chk.hovered || chk.down ? theme.borderHover : theme.border", checkbox_qml)
        self.assertIn('text: "✓"', checkbox_qml)
        self.assertNotIn("width: 8", checkbox_qml)
        self.assertNotIn("height: 8", checkbox_qml)
        for control_id in ("encryptionCheck", "touchCheck", "stylusCheck"):
            self.assertIn(f"id: {control_id}", qml)

    def test_settings_popup_close_button_is_dark_card_style(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "main.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn('text: "Close"', qml)
        self.assertIn("implicitWidth: 92", qml)
        self.assertIn("implicitHeight: 36", qml)
        self.assertIn("parent.hovered ? theme.borderHover : theme.surface", qml)
        self.assertIn("border.color: parent.hovered ? theme.borderHover : theme.border", qml)
        self.assertIn("radius: theme.controlRadius", qml)

    def test_qml_icon_buttons_do_not_use_tooltips(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        for qml_path in qml_dir.glob("*.qml"):
            with self.subTest(qml=qml_path.name):
                qml = qml_path.read_text(encoding="utf-8")
                self.assertNotIn("ToolTip.", qml)

    def test_hover_styles_avoid_blue_outlines(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        combo_qml = (qml_dir / "CustomComboBox.qml").read_text(encoding="utf-8")
        field_qml = (qml_dir / "CustomTextField.qml").read_text(encoding="utf-8")
        button_qml = (qml_dir / "CustomButton.qml").read_text(encoding="utf-8")
        streaming_qml = (qml_dir / "StreamingPage.qml").read_text(encoding="utf-8")
        main_menu_qml = (qml_dir / "MainMenuPage.qml").read_text(encoding="utf-8")
        self.assertIn("color: highlighted ? theme.surfaceAlt : theme.surface", combo_qml)
        self.assertIn("border.color: cb.hovered ? theme.borderHover : theme.border", combo_qml)
        self.assertNotIn("theme.buttonBackgroundHover", combo_qml)
        self.assertIn("border.color: tf.hovered ? theme.borderHover : theme.border", field_qml)
        self.assertNotIn("border.color: tf.hovered ? theme.buttonBackgroundHover", field_qml)
        self.assertIn("scale: btn.hovered ? theme.hoverScale : 1.0", button_qml)
        self.assertIn("Behavior on scale", button_qml)
        self.assertIn("scale: hovered ? theme.hoverScale : 1.0", streaming_qml)
        self.assertIn("border.color: parent.hovered ? theme.borderHover : theme.border", streaming_qml)
        self.assertIn(": (parent.hovered ? theme.borderHover : theme.border)", streaming_qml)
        self.assertNotIn("parent.hovered ? theme.buttonBackgroundHover : theme.accent", streaming_qml)
        self.assertIn("border.color: presetMouse.containsMouse ? theme.borderHover : theme.border", main_menu_qml)

    def test_bitrate_sliders_use_round_button_blue_style(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        wifi_qml = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        streaming_qml = (qml_dir / "StreamingPage.qml").read_text(encoding="utf-8")
        slider_qml = (qml_dir / "CustomSlider.qml").read_text(encoding="utf-8")
        self.assertIn("id: bitrateSlider", wifi_qml)
        self.assertIn("id: s2BitrateSlider", streaming_qml)
        self.assertIn("CustomSlider {", wifi_qml)
        self.assertIn("CustomSlider {", streaming_qml)
        self.assertIn("radius: width / 2", slider_qml)
        self.assertIn("theme.buttonBackgroundHover", slider_qml)
        self.assertIn("theme.buttonBackground", slider_qml)

    def test_save_preset_cancel_button_is_dark_card_style(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "StreamingPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        popup_index = qml.index("id: savePresetPopup")
        cancel_index = qml.index('text: "Cancel"', popup_index)
        save_index = qml.index("id: savePresetButton", cancel_index)
        cancel_block = qml[cancel_index:save_index]
        self.assertIn("onClicked: savePresetPopup.close()", cancel_block)
        self.assertIn("parent.hovered ? theme.borderHover : theme.surface", cancel_block)
        self.assertIn("border.color: parent.hovered ? theme.borderHover : theme.border", cancel_block)
        self.assertIn("radius: theme.controlRadius", cancel_block)
        self.assertIn("Behavior on border.color", cancel_block)

    def test_streaming_page_shows_add_display_for_supported_wayland_desktops(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "StreamingPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn(
            'backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"',
            qml,
        )
        stop_index = qml.index('text: "⏹ Stop Streaming"')
        save_index = qml.index('text: "Save Preset"')
        add_index = qml.index('backend.secondStreamActive ? "Remove Third Display" : "Add Another Display"')
        self.assertLess(stop_index, save_index)
        self.assertLess(save_index, add_index)
        self.assertNotIn("Add Third Display", qml)
        self.assertIn("Add Another Display", qml)
        self.assertIn("Creates a second Hyprland HEADLESS display.", qml)
        display_config = qml[qml.index('text: "⚙ Display Config"'):]
        self.assertIn('visible: backend.detectedDe === "hyprland"', display_config)
        self.assertIn("readonly property int actionButtonWidth: 160", qml)
        self.assertIn("readonly property int actionButtonHeight: 38", qml)
        self.assertEqual(qml.count("Layout.preferredWidth: page.actionButtonWidth"), 3)
        self.assertEqual(qml.count("Layout.preferredHeight: page.actionButtonHeight"), 3)
        self.assertNotIn("activeIndicator", qml)
        self.assertNotIn("OpacityAnimator", qml)
        self.assertNotIn("backend.streamingStatus", qml)
        self.assertNotIn("Active Ports Card", qml)
        self.assertIn("Top status and stream details card", qml)
        self.assertIn("id: streamInfoGrid", qml)
        self.assertIn("readonly property int streamInfoColumns: 3", qml)
        self.assertIn("readonly property int streamInfoCardHeight: 28", qml)
        self.assertIn("readonly property int streamInfoSpacing: 10", qml)
        self.assertNotIn("streamInfoMinCardWidth", qml)
        self.assertIn("readonly property var streamInfoBaseItems", qml)
        self.assertIn("readonly property int streamInfoVisibleColumns: Math.max(", qml)
        self.assertIn("Flow {", qml)
        self.assertIn("spacing: page.streamInfoSpacing", qml)
        self.assertIn("Layout.preferredHeight: page.streamInfoRows * page.streamInfoCardHeight", qml)
        self.assertIn("model: page.streamInfoItems", qml)
        self.assertIn('"Second Display  Port 7110"', qml)
        self.assertIn('"Host  " + backend.localIp', qml)
        self.assertIn('"Third Display  Port 7114"', qml)
        self.assertIn('page.streamInfoBaseItems.concat(["Third Display  Port 7114"])', qml)
        self.assertIn("Math.max(0, streamInfoGrid.width)", qml)
        self.assertIn("width: Math.max(0, (", qml)
        self.assertIn("page.streamInfoSpacing * (page.streamInfoVisibleColumns - 1)", qml)
        self.assertIn("/ page.streamInfoVisibleColumns", qml)
        self.assertIn("height: page.streamInfoCardHeight", qml)
        self.assertIn("fontSizeMode: Text.HorizontalFit", qml)
        self.assertIn("minimumPixelSize: 9", qml)
        self.assertNotIn("Text.ElideRight", qml)
        self.assertNotIn("model: backend.secondStreamActive", qml)
        self.assertNotIn("Third Display Inactive", qml)
        popup_index = qml.index("id: addDisplayPopup")
        cancel_index = qml.index('text: "Cancel"', popup_index)
        start_index = qml.index(
            'text: backend.detectedDe === "kde"', cancel_index
        )
        cancel_block = qml[cancel_index:start_index]
        self.assertIn("onClicked: addDisplayPopup.close()", cancel_block)
        self.assertIn("parent.hovered ? theme.borderHover : theme.surface", cancel_block)
        self.assertIn("border.color: parent.hovered ? theme.borderHover : theme.border", cancel_block)
        self.assertIn("Behavior on border.color", cancel_block)
        self.assertNotIn("#16182a", qml)
        self.assertNotIn("#222540", qml)
        self.assertNotIn("#f472b6", qml)
        self.assertIn("id: s2EncoderCombo", qml)
        self.assertIn("id: s2EncoderProfileCombo", qml)
        self.assertIn("id: s2CustomW", qml)
        self.assertIn("id: s2CustomH", qml)
        self.assertIn("id: s2CustomFps", qml)
        self.assertIn("function secondResolutionValue()", qml)
        self.assertIn("function secondFpsValue()", qml)
        self.assertIn("validator: IntValidator { bottom: 320; top: 7680 }", qml)
        self.assertIn("validator: IntValidator { bottom: 240; top: 4320 }", qml)
        self.assertIn("validator: IntValidator { bottom: 24; top: 240 }", qml)
        self.assertGreaterEqual(qml.count('"Custom..."'), 8)
        self.assertIn("id: s2TouchToggle", qml)
        self.assertIn("Enable touch for this display", qml)
        self.assertIn("id: s2StylusToggle", qml)
        self.assertIn("Enable stylus features for this display", qml)
        self.assertIn("backend.thirdEncryptionStatus", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 2)
        self.assertIn("width: Math.min(page.width - 40, 560)", qml)
        self.assertIn("Creates a second Hyprland HEADLESS display.", qml)
        self.assertIn("Creates Monitorize Display 2 in KDE.", qml)
        self.assertIn("▶  Create Virtual Display", qml)
        self.assertNotIn("host-side display backend is currently disabled", qml)

    def test_receiver_setup_uses_port_input_and_decoder_chips(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "ReceiverSetupPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn("id: portField", qml)
        self.assertIn('text: "7110"', qml)
        self.assertIn('portField.text = rec["manual_port"] || "7110"', qml)
        self.assertIn("validator: IntValidator { bottom: 1; top: 65535 }", qml)
        self.assertNotIn("id: displayCombo", qml)
        self.assertNotIn('model: ["Second display (7110)", "Third display (7114)"]', qml)
        self.assertIn("id: decoderCombo", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 1)
        self.assertEqual(qml.count("CustomComboBox {"), 0)

    def test_receiver_discovery_cards_are_clickable_and_show_online(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "ReceiverSetupPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn("id: deviceCard", qml)
        self.assertIn("onClicked: page.requestConnection(modelData)", qml)
        self.assertIn('"port": Number(device.port || 7110)', qml)
        self.assertNotIn("function selectedPort", qml)
        self.assertIn('text: "online"', qml)
        self.assertIn('color: "#4caf50"', qml)
        self.assertNotIn('text: modelData.encrypted === true ? "encrypted" : "wifi"', qml)
        self.assertNotIn('"  •  Second display"', qml)

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
            "thirdEncryption", "thirdEncryptionStatus", "presets", "presetLaunchStatus",
        } <= properties)
        self.assertTrue({
            "startStreaming", "stopStreaming", "connectToHost",
            "startHostDiscovery", "startUsbScan", "startSecondStream",
            "saveCurrentPreset", "launchPreset", "renamePreset", "deletePreset",
            "isAutostartEnabled", "setAutostartEnabled",
        } <= methods)
        backend.network_timer.stop()

    def test_windows_backend_exposes_receiver_only_capabilities(self):
        with (
            patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"),
            patch("monitorize.platform.utils.sys.platform", "win32"),
        ):
            backend = MonitorizeBackend("windows")
            self.assertFalse(backend.canAutostart)
        self.assertFalse(backend.canHostStream)
        with patch.object(backend.streaming, "start") as start:
            backend.startStreaming(
                "1920x1080", "60", "8000", "Extend",
                "Software (CPU / x264enc)", "Low Latency", True,
            )
        start.assert_not_called()
        self.assertEqual(
            backend.streaming.status,
            "Host streaming is not available on Windows yet.",
        )
        backend.network_timer.stop()

    def test_second_stream_active_comes_from_streaming_controller(self):
        with patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        backend.streaming.third_streaming = True
        self.assertTrue(backend.secondStreamActive)
        backend.streaming.third_streaming = False
        self.assertFalse(backend.secondStreamActive)
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
