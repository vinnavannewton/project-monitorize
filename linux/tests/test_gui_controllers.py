import json
import os
import signal
import sys
import socket
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from subprocess import TimeoutExpired

from PyQt6.QtCore import QCoreApplication, QProcess

from monitorize.streaming import Streamer_gnome, gst_session, pipeline_builder
from monitorize.streaming import kde_native_streamer, portal_streamer
from monitorize.config import app_log, autostart, settings
from monitorize.platform import (
    gnome_virtual_monitor,
    kde_virtual_monitor,
    process_utils,
)
from monitorize.platform.display_controller import DisplayController
from monitorize.desktop.discovery_service import DiscoveryService
from monitorize.desktop.backend import (
    MonitorizeBackend,
    recommended_wifi_bitrate_kbps,
)
from monitorize.desktop.receiver_controller import (
    ReceiverController,
    RtpLossTracker,
    _load_gst,
)
from monitorize.desktop.streaming_controller import StreamingController
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
        service.add_device("New", "10.0.0.2", 7110, True)
        self.assertEqual(len(service.devices), 1)
        self.assertEqual(service.devices[0]["name"], "New")
        self.assertTrue(service.devices[0]["thirdAvailable"])

    def test_advertisement_contains_udp_and_third_display_state(self):
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
            service.advertise("127.0.0.1", True)
        self.assertEqual(len(registered), 2)
        self.assertEqual(registered[0].port, 7110)
        self.assertEqual(registered[0].properties["video_transport"], "rtp-udp-v1")
        self.assertEqual(registered[0].properties["fps"], "60")
        self.assertEqual(registered[0].properties["width"], "1280")
        self.assertEqual(registered[0].properties["height"], "800")
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
            service.advertise(
                "127.0.0.1", True, 90, 75,
                2560, 1600, 1920, 1200,
            )
        self.assertEqual(registered[0].properties["fps"], "90")
        self.assertEqual(registered[1].properties["fps"], "75")
        self.assertEqual(registered[0].properties["width"], "2560")
        self.assertEqual(registered[0].properties["height"], "1600")
        self.assertEqual(registered[1].properties["width"], "1920")
        self.assertEqual(registered[1].properties["height"], "1200")


    def test_advertisement_is_plain_udp(self):
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
            service.advertise("127.0.0.1", True)
        self.assertEqual(len(registered), 2)
        for advertisement in registered:
            self.assertEqual(advertisement.properties["video_transport"], "rtp-udp-v1")

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

    def test_worker_thread_discovery_signal_reaches_qt_owner(self):
        service = DiscoveryService()
        worker = threading.Thread(target=lambda: service.deviceResolved.emit(
            "Host", "10.0.0.2", 7110, True, 7114, "svc"
        ))
        worker.start()
        worker.join()
        self.assertEqual(service.devices, [])
        app.processEvents()
        self.assertEqual(service.devices[0]["name"], "Host")

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
            service.advertise("127.0.0.1", True)
            service.advertise("127.0.0.1", True)
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
            service.advertise("127.0.0.1", True)
            service.advertise("127.0.0.1", False)

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


class ReceiverControllerTest(unittest.TestCase):
    def test_pipeline_preserves_compressed_frames_and_drops_only_after_decode(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder = "Hardware"
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.sink = "xvimagesink"
        with (
            patch(
                "monitorize.desktop.receiver_controller._gst_has_property",
                return_value=True,
            ),
            patch(
                "monitorize.desktop.receiver_controller.gst_has_element",
                side_effect=lambda name: name in {"vapostproc", "cairooverlay"},
            ),
        ):
            args = controller._udp_pipeline_args("xvimagesink", 7114)
        self.assertIn("vah264dec", args)
        self.assertIn("vapostproc", args)
        self.assertIn("video/x-raw,format=NV12", args)
        self.assertIn("disable-passthrough=true", args)
        self.assertIn("config-interval=-1", args)
        self.assertIn("video/x-h264,stream-format=byte-stream,alignment=au", args)
        decoder_index = args.index("vah264dec")
        first_queue_index = args.index("name=receiver_compressed_queue")
        self.assertLess(first_queue_index, decoder_index)
        self.assertNotIn("leaky=downstream", args[first_queue_index:decoder_index])
        self.assertIn("leaky=downstream", args[decoder_index:])
        self.assertIn("sync=false", args)
        self.assertIn("async=false", args)
        self.assertIn("force-aspect-ratio=false", args)
        self.assertIn("port=7114", args)
        self.assertIn("name=receiver_stats_overlay", args)

    def test_udp_pipeline_uses_standalone_fullscreen_wayland_sink(self):
        controller = ReceiverController("kde", Mock())
        controller.decoder_args = ["avdec_h264"]
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            return_value=True,
        ):
            description = controller._udp_pipeline_description("waylandsink")
        self.assertIn("waylandsink", description)
        self.assertIn("fullscreen=true", description)
        self.assertIn("cairooverlay name=receiver_stats_overlay", description)
        self.assertIn("waylandsink name=receiver_sink", description)
        self.assertNotIn("videoscale", description)

    def test_rtp_loss_tracker_handles_reordering_wrap_and_duplicates(self):
        tracker = RtpLossTracker(reorder_window=2)
        self.assertEqual(tracker.add(65534), 0)
        self.assertEqual(tracker.add(0), 0)
        self.assertEqual(tracker.add(65535), 0)
        self.assertEqual(tracker.add(65535), 0)
        self.assertEqual(tracker.add(2), 0)
        self.assertEqual(tracker.add(3), 1)

    def test_stats_card_can_be_enabled_and_disabled_live(self):
        controller = ReceiverController("kde", Mock())
        controller.overlay_supported = True
        controller.last_snapshot = {
            "transport": "Wi-Fi RTP/UDP", "rx_kbps": 8000.0,
            "pps": 1000.0, "loss": 0.1, "input_fps": 60.0,
            "decoded_fps": 60.0, "display_fps": 59.0,
            "display_label": "display", "decode_ms": 2.0,
            "compressed_q": 1, "raw_q": 0, "raw_drops": 0,
            "sink_drops": 0, "decoder": "Software avdec_h264",
            "sink": "fakesink",
        }
        controller.gst_pipeline = Mock()
        controller.set_stats_visible(True)
        self.assertIsNotNone(controller.overlay_surface)
        controller.set_stats_visible(False)
        self.assertIsNone(controller.overlay_surface)

    def test_cairo_overlay_headless_pipeline_draws_and_finishes(self):
        Gst = _load_gst()
        pipeline = Gst.parse_launch(
            "videotestsrc num-buffers=2 ! videoconvert ! "
            "video/x-raw,format=BGRx ! cairooverlay name=overlay ! fakesink"
        )
        draws = []

        def draw(_overlay, context, _timestamp, _duration):
            draws.append(True)
            context.set_source_rgba(0, 0, 0, 0.7)
            context.rectangle(2, 2, 20, 10)
            context.fill()

        pipeline.get_by_name("overlay").connect("draw", draw)
        pipeline.set_state(Gst.State.PLAYING)
        message = pipeline.get_bus().timed_pop_filtered(
            3 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        pipeline.set_state(Gst.State.NULL)
        self.assertIsNotNone(message)
        self.assertEqual(message.type, Gst.MessageType.EOS)
        self.assertEqual(len(draws), 2)

    def test_stats_snapshot_reports_rates_queues_and_honest_tcp_unknowns(self):
        controller = ReceiverController("kde", Mock())
        controller._reset_stats()
        controller.sink = "fakesink"
        controller.last_stats_time = 10.0
        controller.stats_counts.update({
            "bytes": 1_000_000, "packets": 90, "lost": 10,
            "input": 60, "decoded": 59, "sink_input": 58,
            "raw_drops": 2, "decode_ns": 12_000_000, "decode_samples": 2,
        })
        queue = Mock()
        queue.find_property.return_value = object()
        queue.get_property.return_value = 1
        sink = Mock()
        sink.find_property.return_value = None
        controller.stats_elements = {
            "receiver_sink": sink,
            "receiver_compressed_queue": queue,
            "receiver_raw_queue": queue,
        }
        controller.udp_transport = True
        with patch("monitorize.desktop.receiver_controller.time.monotonic", return_value=11.0):
            snapshot = controller._stats_snapshot()
        self.assertEqual(snapshot["rx_kbps"], 8000)
        self.assertEqual(snapshot["pps"], 90)
        self.assertEqual(snapshot["loss"], 10)
        self.assertEqual(snapshot["display_label"], "output")
        self.assertEqual(snapshot["display_fps"], 58)
        self.assertEqual(snapshot["decode_ms"], 6)
        self.assertEqual(snapshot["compressed_q"], 1)
        self.assertEqual(snapshot["raw_drops"], 2)

        controller.stats_counts["bytes"] = 100
        controller.last_stats_time = 11.0
        controller.udp_transport = False
        with patch("monitorize.desktop.receiver_controller.time.monotonic", return_value=12.0):
            snapshot = controller._stats_snapshot()
        self.assertIsNone(snapshot["pps"])
        self.assertIsNone(snapshot["loss"])

    def test_receiver_always_launches_standalone_pipeline(self):
        controller = ReceiverController("gnome", Mock())
        with patch.object(controller, "_launch_external_pipeline") as launch:
            controller._launch_pipeline("10.0.0.2", 7110, generation=0)
        launch.assert_called_once_with("10.0.0.2", 7110, 0)

    def test_receiver_connect_marks_session_active_before_stable(self):
        controller = ReceiverController("kde", Mock())
        emitted = []
        controller.receivingChanged.connect(lambda value: emitted.append(value))
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("10.0.0.2", 7110, "Software")
        start.assert_called_once()
        self.assertTrue(controller.receiving)
        self.assertFalse(controller.stable)
        self.assertIn(True, emitted)

    def test_receiver_connect_uses_udp_for_wifi(self):
        controller = ReceiverController("kde", Mock())
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("10.0.0.2", 7110, "Software")
        start.assert_called_once()
        self.assertTrue(controller.receiving)

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

    def test_hardware_decoder_enables_recovery_when_supported(self):
        controller = ReceiverController("kde", Mock())
        supported = {
            "discard-corrupted-frames",
            "automatic-request-sync-points",
            "automatic-request-sync-point-flags",
            "min-force-key-unit-interval",
            "qos",
            "max-errors",
        }
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            side_effect=lambda _element, prop: prop in supported,
        ):
            args = controller._hardware_decoder_args("vah264dec")
        self.assertEqual(args[0], "vah264dec")
        self.assertIn("discard-corrupted-frames=true", args)
        self.assertIn("automatic-request-sync-points=true", args)
        self.assertIn(
            "automatic-request-sync-point-flags=corrupt-output+discard-input",
            args,
        )
        self.assertIn("min-force-key-unit-interval=250000000", args)
        self.assertIn("qos=true", args)
        self.assertIn("max-errors=-1", args)

    def test_hardware_decoder_skips_unsupported_recovery_properties(self):
        controller = ReceiverController("kde", Mock())
        with patch(
            "monitorize.desktop.receiver_controller._gst_has_property",
            side_effect=lambda _element, prop: prop == "qos",
        ):
            args = controller._hardware_decoder_args("vaapih264dec")
        self.assertEqual(args, ["vaapih264dec", "qos=true"])

    def test_sink_selection_prefers_fullscreen_wayland_sink(self):
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
                ["waylandsink", "glimagesink", "autovideosink"],
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

    def test_standalone_sink_explicitly_requests_its_own_window(self):
        calls = []

        class FakeOverlay:
            @staticmethod
            def set_window_handle(sink, handle):
                calls.append((sink, handle))

        class FakeSink(FakeOverlay):
            pass

        sink = FakeSink()
        pipeline = Mock()
        pipeline.get_by_name.return_value = sink
        controller = ReceiverController("gnome", Mock())
        video = types.SimpleNamespace(VideoOverlay=FakeOverlay)
        with patch("monitorize.desktop.receiver_controller._GST_VIDEO", video):
            controller._prepare_standalone_sink(pipeline)
        self.assertEqual(calls, [(sink, 0)])

    def test_immediate_hardware_failure_retries_next_sink_before_software(self):
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
        controller.hardware_decoder_candidates = ["vah264dec"]
        controller.hardware_decoder_index = 0
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API"
        controller.process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        with (
            patch.object(controller, "_launch_external_pipeline") as launch,
            patch.object(controller, "_software_decoder_args", return_value=["avdec_h264"]) as software,
        ):
            controller._finished(1, None, controller.process, generation=4)
        launch.assert_called_once_with("10.0.0.2", 7110, 4)
        self.assertEqual(controller.sink, "autovideosink")
        self.assertEqual(controller.decoder_args[0], "vah264dec")
        self.assertFalse(controller.pipeline_fallback_used)
        software.assert_not_called()

    def test_hardware_failure_tries_alternate_decoder_before_software(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 4
        controller.decoder = "Hardware"
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.receiver_host = "10.0.0.2"
        controller.receiver_port = 7110
        controller.sink_candidates = ["glimagesink"]
        controller.sink_index = 0
        controller.sink = "glimagesink"
        controller.hardware_decoder_candidates = ["vah264dec", "vaapih264dec"]
        controller.hardware_decoder_index = 0
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API vah264dec"
        controller.process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        with (
            patch.object(controller, "_launch_external_pipeline") as launch,
            patch.object(controller, "_software_decoder_args", return_value=["avdec_h264"]) as software,
        ):
            controller._finished(1, None, controller.process, generation=4)
        launch.assert_called_once_with("10.0.0.2", 7110, 4)
        self.assertEqual(controller.sink, "glimagesink")
        self.assertEqual(controller.decoder_args[0], "vaapih264dec")
        self.assertEqual(controller.decoder_label, "VA-API vaapih264dec")
        self.assertFalse(controller.pipeline_fallback_used)
        software.assert_not_called()

    def test_hardware_failure_uses_software_after_va_paths_are_exhausted(self):
        controller = ReceiverController("kde", Mock())
        controller.generation = 4
        controller.decoder = "Hardware"
        controller.host = "10.0.0.2"
        controller.port = 7110
        controller.receiver_host = "10.0.0.2"
        controller.receiver_port = 7110
        controller.sink_candidates = ["glimagesink"]
        controller.sink_index = 0
        controller.sink = "glimagesink"
        controller.hardware_decoder_candidates = ["vah264dec"]
        controller.hardware_decoder_index = 0
        controller.decoder_args = ["vah264dec"]
        controller.decoder_label = "VA-API vah264dec"
        controller.process = process_mock()
        controller.attempt_started = __import__("time").monotonic()
        with (
            patch.object(controller, "_launch_external_pipeline") as launch,
            patch.object(controller, "_software_decoder_args", return_value=["avdec_h264"]),
        ):
            controller._finished(1, None, controller.process, generation=4)
        launch.assert_called_once_with("10.0.0.2", 7110, 4)
        self.assertEqual(controller.decoder, "Software")
        self.assertEqual(controller.decoder_args, ["avdec_h264"])
        self.assertTrue(controller.pipeline_fallback_used)


class ReceiverLifecycleTest(unittest.TestCase):

    def test_immediate_eos_schedules_retry(self):
        controller = ReceiverController("kde", Mock())
        controller.host = "10.0.0.2"
        controller.port = 7114
        controller.attempt_started = __import__("time").monotonic()
        controller.process = process_mock()
        controller._finished(0, None)
        self.assertTrue(controller.retry_pending)
        self.assertEqual(controller.retry_count, 1)
        self.assertTrue(controller.retry_timer.isActive())
        controller.retry_timer.stop()

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
        controller.host = "10.0.0.2"
        controller.port = 7110
        with patch.object(controller, "_launch_pipeline") as launch:
            controller._start_attempt(generation=4)
        launch.assert_not_called()

    def test_invalid_receiver_target_is_rejected(self):
        controller = ReceiverController("kde", Mock())
        with patch.object(controller, "_start_attempt") as start:
            controller.connect("  ", 7110, "Software")
        start.assert_not_called()
        self.assertEqual(controller.status, "Invalid host or port")

    def test_receiver_stats_setting_defaults_off_and_round_trips(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                self.assertFalse(settings.load_receiver_settings()["show_stats"])
                settings.save_receiver_stats_visible(True)
                self.assertTrue(settings.load_receiver_settings()["show_stats"])
                settings.save_receiver_settings(
                    ip="10.0.0.2", port="7110", decoder="Hardware"
                )
                self.assertTrue(settings.load_receiver_settings()["show_stats"])
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
                self.assertEqual(loaded["fec_mode"], "Off")
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

    def test_first_run_wifi_defaults_follow_moonlight_1080p60_curve(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                loaded = settings.load_wifi_settings()
                self.assertEqual(loaded["resolution"], "1920x1080")
                self.assertEqual(loaded["bitrate"], "20000")
                self.assertEqual(loaded["encoder"], "Software (CPU / x264enc)")
                self.assertFalse(loaded["enable_audio"])
                self.assertNotIn("use_encryption", loaded)
                self.assertNotIn("stream_type", loaded)
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_primary_and_additional_wifi_fec_choices_save_independently(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings.save_wifi_settings(
                    resolution="1920x1080", custom_w="", custom_h="",
                    fps="60", custom_fps="", bitrate="20000",
                    display_type="Extend",
                    encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency", fec_mode="ULPFEC 10%",
                    enable_audio=True,
                )
                settings.save_second_display_settings(
                    resolution="1280x720", fps="60", bitrate="8000",
                    encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency", fec_mode="Off",
                )
                self.assertEqual(
                    settings.load_wifi_settings()["fec_mode"], "ULPFEC 10%"
                )
                self.assertTrue(settings.load_wifi_settings()["enable_audio"])
                self.assertEqual(
                    settings.load_second_display_settings()["fec_mode"], "Off"
                )
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
                self.assertFalse(loaded["enable_audio"])
            finally:
                settings.CONFIG_DIR, settings.CONFIG_FILE = old_dir, old_file

    def test_wifi_and_usb_audio_settings_are_independent(self):
        old_dir, old_file = settings.CONFIG_DIR, settings.CONFIG_FILE
        with tempfile.TemporaryDirectory() as directory:
            try:
                settings.CONFIG_DIR = directory
                settings.CONFIG_FILE = str(Path(directory) / "settings.ini")
                settings.save_wifi_settings(
                    resolution="1920x1080", custom_w="", custom_h="",
                    fps="60", custom_fps="", bitrate="20000",
                    display_type="Extend", encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency", enable_audio=True,
                )
                settings.save_usb_settings(
                    resolution="1920x1080", custom_w="", custom_h="",
                    fps="60", custom_fps="", bitrate="16000",
                    display_type="Extend", encoder="Software (CPU / x264enc)",
                    encoder_profile="Low Latency", enable_audio=False,
                )
                self.assertTrue(settings.load_wifi_settings()["enable_audio"])
                self.assertFalse(settings.load_usb_settings()["enable_audio"])
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
                            "enable_audio": True,
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
                self.assertTrue(loaded[0]["primary"]["enable_audio"])
                self.assertNotIn("wifi", loaded[0])
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
        controller.streaming = True
        controller.display_type = "Extend"
        controller.env = Mock()
        controller.generation = 7
        return controller

    def test_enabled_audio_sender_launches_once_and_survives_video_relaunch(self):
        controller = self.kde_controller()
        controller.audio_enabled = True
        process = process_mock()
        with patch(
            "monitorize.desktop.streaming_controller.QProcess", return_value=process
        ) as process_type:
            controller._launch_audio()
            controller._launch_audio()

        process.start.assert_called_once_with(
            sys.executable,
            ["-m", "monitorize.streaming.audio_sender", "wifi", "--port", "7120"],
        )
        self.assertEqual(process_type.call_count, 1)

    def test_audio_sender_exit_is_non_fatal(self):
        controller = self.kde_controller()
        process = process_mock()
        controller.audio_process = process

        controller._audio_finished(1, None, controller.generation, process)

        self.assertTrue(controller.streaming)
        self.assertIsNone(controller.audio_process)

    def test_rtp_telemetry_parses_fixed_bitrate_host_and_client_lines(self):
        controller = self.kde_controller()

        self.assertTrue(controller._update_rtp_telemetry(
            "[RTP][Host] capture=59.0fps paced=60.0fps encoded=58.0fps "
            "rtp=132.0pps tx=7500kbps bitrate=8000kbps "
            "videoBitrate=7200kbps fec=10% fecPps=12.0 "
            "pacing=200000kbps encodePath=7.2ms senderQueue=1 "
            "senderDelay=0.8ms senderDrops=0 sendErrors=0 scheduledIdr=3 "
            "recoveryIdr=2 confirmedIdr=2 coalescedIdr=1 idrKiB=84.5 idrMs=12.5"
        ))
        self.assertTrue(controller._update_rtp_telemetry(
            "[RTP][Client] rx=7200kbps pps=120 loss=0.2% incomplete=0 "
            "render=57.0fps queue=1 decode=11.0ms renderLatency=22.0ms dropped=0 "
            "media=108 fec=12 recovered=2 unrecoverable=1 residual=1 "
            "assemblyP95=3.5ms late=0"
        ))
        self.assertTrue(controller.telemetry["available"])
        self.assertEqual(59.0, controller.telemetry["hostCaptureFps"])
        self.assertEqual(7200.0, controller.telemetry["clientRxKbps"])
        self.assertEqual(8000.0, controller.telemetry["bitrateKbps"])
        self.assertEqual(7200.0, controller.telemetry["videoBitrateKbps"])
        self.assertEqual(10.0, controller.telemetry["effectiveFecPercent"])
        self.assertEqual(2.0, controller.telemetry["clientFecRecovered"])
        self.assertEqual(3.0, controller.telemetry["scheduledIdr"])
        self.assertEqual(2.0, controller.telemetry["recoveryIdr"])
        self.assertEqual(1.0, controller.telemetry["coalescedIdr"])
        self.assertEqual(84.5, controller.telemetry["idrKiB"])
        self.assertEqual(0.8, controller.telemetry["senderDelayMs"])
        self.assertEqual(3.5, controller.telemetry["clientAssemblyP95Ms"])
        self.assertEqual(2.0, controller.telemetry["confirmedIdr"])

    def test_invalid_rtp_line_keeps_existing_telemetry(self):
        controller = self.kde_controller()
        controller.telemetry = {"available": True, "hostCaptureFps": 60.0}

        self.assertTrue(controller._update_rtp_telemetry("[RTP][Host] capture=oops"))

        self.assertEqual({"available": True, "hostCaptureFps": 60.0}, controller.telemetry)

    def test_rtp_telemetry_reset_marks_overlay_unavailable(self):
        controller = self.kde_controller()
        controller.telemetry = {"available": True, "clientRenderFps": 60.0}

        controller._reset_telemetry()

        self.assertEqual({"available": False}, controller.telemetry)

    def test_periodic_rtp_metrics_do_not_reach_generic_log(self):
        controller = self.kde_controller()
        process = Mock()
        process.readAllStandardOutput.return_value = (
            b"[RTP][Host] capture=60.0fps paced=60.0fps encoded=60.0fps "
            b"rtp=120.0pps tx=8000kbps bitrate=8000kbps "
            b"pacing=10000kbps encodePath=7.0ms\n[Pipeline] READY\n"
        )
        controller.streamer = process
        emitted = []
        controller.logAppended.connect(lambda kind, message: emitted.append((kind, message)))

        controller._read_streamer(controller.generation, process)

        self.assertTrue(controller.telemetry["available"])
        self.assertEqual([("STREAMER", "[Pipeline] READY\n")], emitted)

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
            "10.0.0.1", False, 60, 60,
            1920, 1200, None, None,
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
        self.assertIn("monitorize\\.input_bridge\\.touch_daemon", patterns)
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

    def test_wifi_input_stays_on_the_public_udp_port(self):
        controller = self.kde_controller()
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
        self.assertNotIn("--local-udp", args)

    def test_gnome_plain_wifi_input_uses_public_udp(self):
        controller = self.gnome_controller()
        controller.wifi = True
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

    def test_gnome_wifi_input_stays_on_the_public_udp_port(self):
        controller = self.gnome_controller()
        controller.wifi = True
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

    def test_input_ready_marker_updates_status(self):
        controller = self.kde_controller()
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[TouchDaemon] INFO READY input_slot=primary\n"
        )
        controller.input_bridge = process

        controller._read_input(generation=3, process=process)

        self.assertEqual(controller.status, "Touch and stylus input ready")

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
        controller.runtime_general = {
            "minimize_to_tray": True,
            "enable_touch": True,
            "enable_stylus_features": True,
        }
        config = controller.active_configuration()
        self.assertEqual(config["primary"]["resolution"], "1920x1200")
        self.assertEqual(config["primary"]["encoder_profile"], "Balanced")
        self.assertNotIn("wifi", config)
        self.assertEqual(config["third"], {"enabled": False})
        self.assertTrue(config["general"]["enable_stylus_features"])

    def test_active_configuration_includes_active_third_display_settings(self):
        controller = self.kde_controller()
        controller.encoder = "Intel/AMD VA-API (vah264enc)"
        controller.encoder_profile = "Balanced"
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
            "fec_mode": "Off",
            "enable_touch": True,
            "enable_stylus_features": False,
        })

    def test_kde_third_display_uses_distinct_native_virtual_slot(self):
        discovery = Mock()
        controller = StreamingController("kde", "10.0.0.1", discovery)
        controller.streaming = True
        controller.wifi = True
        controller.streaming = True
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
            "10.0.0.1", True, 60, 60,
            1920, 1080, 1920, 1080,
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

    def test_dual_wifi_caps_secondary_bitrate_to_shared_budget(self):
        controller = self.kde_controller()
        controller.primary_ready = True
        controller.bitrate = 20250
        logs = []
        controller.logAppended.connect(lambda label, message: logs.append((label, message)))
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "1920x1200", "60", "20500",
                "NVIDIA NVENC (nvh264enc)", "Low Latency",
            )

        args = process.start.call_args.args[1]
        self.assertEqual(args[5], "9750")
        self.assertTrue(any("Bitrate limited" in message for _, message in logs))

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

    def test_third_display_uses_the_udp_control_port(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.primary_ready = True
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller.start_third(
                "1920x1080", "60", "8000",
                "Software (CPU / x264enc)", "Low Latency",
            )

        env = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(env.value("MONITORIZE_PORT"), "7114")
        self.assertEqual(env.value("MONITORIZE_HOST"), "0.0.0.0")

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

    def test_third_input_ready_marker_updates_status(self):
        controller = self.kde_controller()
        controller.third_generation = 5
        process = process_mock()
        process.readAllStandardOutput.return_value = (
            b"[TouchDaemon] INFO READY input_slot=additional\n"
        )
        controller.third_input_bridge = process

        controller._read_third_input(5, process)

        self.assertEqual(
            controller.status,
            "Additional-display touch and stylus ready",
        )

    def test_third_touch_uses_the_public_udp_port(self):
        controller = self.kde_controller()
        controller.third_streaming = True
        controller.third_ready = True
        controller.third_generation = 5
        controller.third_output = "Virtual-Monitorize-2"
        controller.third_width, controller.third_height = 1280, 720
        process = process_mock()

        with patch("monitorize.desktop.streaming_controller.QProcess", return_value=process):
            controller._maybe_launch_third_input(5)

        args = process.start.call_args.args[1]
        self.assertEqual(args[args.index("--port") + 1], "7117")
        self.assertIn("--wifi", args)
        self.assertNotIn("--local-udp", args)

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
            b"[Pipeline] READY\n"
        )
        controller.third_streamer = process

        controller._read_third_streamer(2, process)

        self.assertTrue(controller.third_ready)
        self.assertEqual(
            discovery.advertise.call_args_list[-1].args,
            ("10.0.0.1", True, 60, 60, 1920, 1080, 1920, 1080),
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

    def test_ready_wifi_streamer_exit_schedules_restart(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.generation = 7
        controller.streamer_was_ready = True
        controller.primary_ready = True
        controller.gst_pids = {123}
        process = process_mock()
        controller.streamer = process
        with (
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids") as kill,
            patch("monitorize.desktop.streaming_controller.QTimer.singleShot") as retry,
        ):
            controller._streamer_finished(1, None, 7, process)

        self.assertTrue(controller.streaming)
        self.assertFalse(controller.primary_ready)
        self.assertIsNone(controller.streamer)
        self.assertEqual(controller.status, "Stream interrupted; restarting…")
        self.assertEqual(controller.gst_pids, set())
        kill.assert_called_once_with({123})
        self.assertEqual(retry.call_args.args[0], 1000)

    def test_wifi_runtime_retry_stays_enabled_before_next_ready(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.generation = 7
        controller.streamer_was_ready = True
        process = process_mock()
        controller.streamer = process
        with patch(
            "monitorize.desktop.streaming_controller.QTimer.singleShot"
        ) as retry:
            controller._streamer_finished(1, None, 7, process)
        self.assertTrue(controller.streamer_was_ready)
        retry.assert_called_once()

    def test_stale_wifi_restart_callback_is_ignored(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = True
        controller.generation = 8
        controller.streamer = None
        with patch.object(controller, "_launch_streamer") as launch:
            controller._restart_wifi_streamer(7)
        launch.assert_not_called()

    def test_usb_streamer_exit_never_schedules_wifi_retry(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        controller.streaming = True
        controller.wifi = False
        controller.streamer_was_ready = True
        process = process_mock()
        controller.streamer = process
        with (
            patch.object(controller, "stop") as stop,
            patch("monitorize.desktop.streaming_controller.QTimer.singleShot") as retry,
        ):
            controller._streamer_finished(1, None, controller.generation, process)
        stop.assert_called_once()
        retry.assert_not_called()

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

    def test_udp_preserves_selected_settings_and_encoder(self):
        controller = StreamingController("hyprland", "10.0.0.1", Mock())
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "2337x1081", "90", "14000", "Extend",
                "NVIDIA NVENC (nvh264enc)", "Quality", True,
                options={},
            )
        self.assertEqual((controller.width, controller.height), (2336, 1080))
        self.assertEqual((controller.fps, controller.bitrate), (90, 14000))
        self.assertEqual(controller.encoder, "NVIDIA NVENC (nvh264enc)")
        self.assertEqual(controller.encoder_profile, "Quality")
        self.assertEqual(controller.env.value("MONITORIZE_ENCODER"), "nvidia")
        self.assertEqual(controller.env.value("MONITORIZE_ENCODER_PROFILE"), "Quality")
        self.assertEqual(controller.env.value("MONITORIZE_REQUIRE_HARDWARE_ENCODER"), "0")
        self.assertEqual(controller.env.value("MONITORIZE_VIDEO_TRANSPORT"), "rtp-udp-v1")
        controller._advertise()
        controller.discovery.advertise.assert_called_with(
            "10.0.0.1", False, 90, 60, 2336, 1080,
            None, None,
        )

    def test_wifi_fec_setting_is_scoped_to_each_stream_environment(self):
        controller = StreamingController("kde", "10.0.0.1", Mock())
        with (
            patch("monitorize.desktop.streaming_controller.stop_processes"),
            patch("monitorize.desktop.streaming_controller.kill_patterns"),
            patch("monitorize.desktop.streaming_controller.kill_tracked_pids"),
            patch.object(controller.display, "cleanup"),
            patch.object(controller, "_prepare_display"),
        ):
            controller.start(
                "1920x1200", "60", "20000", "Extend",
                "Software (CPU / x264enc)", "Low Latency", True,
                fec_mode="ULPFEC 10%",
            )
        self.assertEqual(controller.fec_mode, "ULPFEC 10%")
        self.assertEqual(controller.env.value("MONITORIZE_FEC_PERCENT"), "10")

        controller.streaming = True
        controller.primary_ready = True
        process = process_mock()
        with patch(
            "monitorize.desktop.streaming_controller.QProcess",
            return_value=process,
        ):
            controller.start_third(
                "1280x720", "60", "8000",
                "Software (CPU / x264enc)", "Low Latency",
                fec_mode="Off",
            )
        third_env = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(third_env.value("MONITORIZE_FEC_PERCENT"), "0")

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
    def test_virtual_output_owner_requests_embedded_cursor(self):
        root = Path(__file__).resolve().parents[2]
        source = (
            root / "linux/native/kde_virtual_output/monitorize-kde-virtual-output.c"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "app.width, app.height, wl_fixed_from_double(1.0), CURSOR_EMBEDDED",
            source,
        )
        self.assertNotIn("CURSOR_HIDDEN", source)

    def _run_case(
        self,
        fps,
        slot="primary",
        endpoint=("192.0.2.1", 49152, 1, "high", 0),
    ):
        events = []
        output_name = kde_virtual_monitor.virtual_slot(slot)["output_name"]
        helper = Mock()
        helper.stdin = Mock()
        helper.stdin.write.side_effect = lambda _value: events.append("capture")
        helper.stdout = Mock()
        helper.poll.return_value = 0
        gst = Mock()
        gst.poll.return_value = 0
        gst.returncode = 0
        actual = {
            "name": output_name,
            "uuid": f"uuid-{slot}",
            "width": 1920,
            "height": 1200,
            "refresh_rate": float(fps),
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
                        "name": output_name,
                        "node_id": 10,
                        "target_object": "100",
                    },
                    {
                        "event": "capture_ready",
                        "name": output_name,
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
                "prepare_rtp_endpoint",
                side_effect=lambda **_kwargs: (
                    events.append("negotiate") or endpoint
                ),
            ),
            patch.object(
                kde_native_streamer,
                "_start_capture_wakeup",
                side_effect=lambda _output: events.append("wakeup"),
            ),
            patch.object(
                kde_native_streamer,
                "launch_with_fallback",
                return_value=gst,
            ) as launch,
            patch("builtins.print") as output,
            patch.object(kde_native_streamer.signal, "signal"),
        ):
            result = kde_native_streamer.run_native_streamer(
                slot, 1920, 1200, fps, 8000,
                "wifi" if endpoint is not None else "usb", 7110, None,
                "0.0.0.0",
            )

        return result, events, helper, launch, output

    def test_rtp_60_uses_owner_after_wakeup(self):
        result, events, helper, launch, output = self._run_case(60)

        self.assertEqual(result, 0)
        self.assertEqual(["negotiate", "wakeup"], events)
        helper.stdin.write.assert_not_called()
        self.assertEqual(launch.call_args.kwargs["target_object"], "100")
        self.assertEqual(
            ("192.0.2.1", 49152, 1, "high", 0),
            launch.call_args.kwargs["rtp_endpoint"],
        )
        self.assertTrue(launch.call_args.kwargs["preserve_source_size"])
        self.assertTrue(launch.call_args.kwargs["preserve_source_rate"])
        output.assert_any_call(
            "[KDE Native] Capture path=owner node=10 target=100", flush=True
        )

    def test_additional_rtp_above_60_uses_post_mode_capture(self):
        result, events, helper, launch, output = self._run_case(
            120, slot="additional"
        )

        self.assertEqual(result, 0)
        self.assertEqual(["negotiate", "wakeup", "capture"], events)
        helper.stdin.write.assert_called_once_with("capture\n")
        self.assertEqual(launch.call_args.kwargs["target_object"], "101")
        output.assert_any_call(
            "[KDE Native] Capture path=post-mode node=11 target=101", flush=True
        )

    def test_usb_keeps_post_mode_capture(self):
        result, events, helper, launch, _output = self._run_case(60, endpoint=None)

        self.assertEqual(result, 0)
        self.assertEqual(["negotiate", "capture"], events)
        helper.stdin.write.assert_called_once_with("capture\n")
        self.assertEqual(launch.call_args.kwargs["target_object"], "101")
        self.assertIsNone(launch.call_args.kwargs["rtp_endpoint"])

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
        options = dict(
            pw_fd=None,
            node_id=42,
            width=1280,
            height=800,
            fps=60,
            bitrate=8000,
            port=7110,
        )
        options.update(kwargs)
        argv = pipeline_builder.build_pipeline(**options)
        return " ".join(argv)

    def test_native_pipewire_target_preserves_kwin_source_rate(self):
        text = self._pipeline_text(
            target_object="101", preserve_source_rate=True
        )
        self.assertIn("target-object=101", text)
        self.assertNotIn("videorate", text)
        self.assertNotIn("framerate=60/1", text)
        self.assertIn("keepalive-time=17", text)
        self.assertIn("max-buffers=4", text)

    def test_cpu_wifi_preserves_native_kwin_source_rate(self):
        text = self._pipeline_text(
            target_object="101", preserve_source_rate=True, wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152, 1, "constrained-baseline"),
        )
        self.assertIn("keepalive-time=1000", text)
        self.assertIn("name=monitorize_kwin_source", text)
        self.assertIn("video/x-raw(ANY),max-framerate=60/1", text)
        self.assertNotIn("videorate", text)
        self.assertNotIn("imagefreeze", text)
        self.assertNotIn("skip-to-first=false", text)
        self.assertNotIn("video/x-raw(ANY),framerate=60/1", text)

    def test_cpu_udp_pipeline_uses_selected_cpu_rtp_settings(self):
        text = self._pipeline_text(
            width=2336, height=1080, fps=90, bitrate=14000,
            wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152, 1, "constrained-baseline"),
        )
        self.assertIn("x264enc", text)
        self.assertIn("width=2336,height=1080", text)
        self.assertIn("framerate=90/1", text)
        self.assertIn("bitrate=14000", text)
        self.assertIn("key-int-max=2700", text)
        self.assertIn("rtph264pay", text)
        self.assertIn("udpsink", text)
        self.assertNotIn("rtpulpfec", text)
        self.assertNotIn("nvh264enc", text)
        self.assertNotIn("vah264enc", text)
        self.assertNotIn("tls", text)

    def test_udp_pipeline_has_no_activity_branch(self):
        text = self._pipeline_text(
            wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152, 1, "constrained-baseline"),
        )

        self.assertNotIn("monitorize_activity_tee", text)
        self.assertNotIn("monitorize_activity_sink", text)

    def test_udp_pipeline_uses_selected_hardware_encoder(self):
        endpoint = ("192.0.2.1", 49152, 1, "constrained-baseline")
        vaapi = self._pipeline_text(
            width=1920, height=1080, fps=60, bitrate=12000,
            hw_encoder="vah264enc", wifi_mode=True,
            encoder_profile="Quality", rtp_endpoint=endpoint,
        )
        nvenc = self._pipeline_text(
            width=1920, height=1080, fps=60, bitrate=12000,
            hw_encoder="nvh264enc", nvidia_memory="system", wifi_mode=True,
            encoder_profile="Quality", rtp_endpoint=endpoint,
        )
        for text in (vaapi, nvenc):
            self.assertIn("framerate=60/1", text)
            self.assertIn("bitrate=12000", text)
            self.assertIn("rtph264pay", text)
            self.assertIn("udpsink", text)
            self.assertNotIn("rtpulpfec", text)
            self.assertNotIn("tls", text)
        self.assertIn("vah264enc", vaapi)
        self.assertIn("vapostproc", vaapi)
        self.assertNotIn("x264enc", vaapi)
        self.assertIn("nvh264enc", nvenc)
        self.assertIn("preset=p5", nvenc)
        self.assertNotIn("x264enc", nvenc)

    def test_hardware_wifi_preserves_native_kwin_source_rate(self):
        for encoder in ("nvh264enc", "vah264enc"):
            with self.subTest(encoder=encoder):
                text = self._pipeline_text(
                    hw_encoder=encoder, target_object="101",
                    preserve_source_rate=True, wifi_mode=True,
                    rtp_endpoint=("192.0.2.1", 49152, 1, "high"),
                )
                self.assertIn("keepalive-time=1000", text)
                self.assertIn("video/x-raw(ANY),max-framerate=60/1", text)
                self.assertNotIn("videorate", text)
                self.assertNotIn("imagefreeze", text)
                self.assertNotIn("skip-to-first=false", text)
                self.assertNotIn("video/x-raw(ANY),framerate=60/1", text)

    def test_native_kwin_rate_cap_does_not_duplicate_for_any_encoder_path(self):
        endpoint = ("192.0.2.1", 49152, 1, "high")
        cases = (
            (None, "cuda"),
            ("vah264enc", "cuda"),
            ("nvh264enc", "gl"),
            ("nvh264enc", "cuda"),
            ("nvh264enc", "system"),
        )
        for encoder, memory in cases:
            with self.subTest(encoder=encoder, memory=memory):
                text = self._pipeline_text(
                    fps=30, hw_encoder=encoder, nvidia_memory=memory,
                    target_object="101", preserve_source_rate=True,
                    wifi_mode=True, rtp_endpoint=endpoint,
                )
                self.assertIn("video/x-raw(ANY),max-framerate=30/1", text)
                self.assertNotIn("video/x-raw(ANY),framerate=30/1", text)
                self.assertNotIn("videorate", text)
                self.assertNotIn("imagefreeze", text)

    def test_native_kwin_vaapi_wifi_releases_compositor_buffers_before_upload(self):
        text = self._pipeline_text(
            hw_encoder="vah264enc", target_object="101",
            preserve_source_rate=True, wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152, 1, "high"),
        )
        self.assertIn("always-copy=false", text)
        self.assertIn("videoconvert name=monitorize_kwin_copy n-threads=4", text)
        self.assertIn("video/x-raw,format=NV12", text)
        self.assertIn("vapostproc", text)
        self.assertLess(text.index("monitorize_kwin_copy"), text.index("queue"))
        self.assertLess(text.index("monitorize_kwin_copy"), text.index("vapostproc"))

    def test_non_native_vaapi_does_not_insert_kwin_copy_boundary(self):
        text = self._pipeline_text(hw_encoder="vah264enc", wifi_mode=True)
        self.assertNotIn("monitorize_kwin_copy", text)
        self.assertNotIn("max-buffers=4", text)

    def test_native_kwin_vaapi_non_rtp_keeps_copy_after_queue(self):
        text = self._pipeline_text(
            hw_encoder="vah264enc", target_object="101",
            preserve_source_rate=True, wifi_mode=True,
        )
        self.assertNotIn("monitorize_kwin_copy", text)
        self.assertLess(text.index("queue"), text.index("videoconvert"))

    def test_native_kwin_non_vaapi_does_not_insert_va_copy_boundary(self):
        endpoint = ("192.0.2.1", 49152, 1, "high")
        for encoder in (None, "nvh264enc"):
            with self.subTest(encoder=encoder):
                text = self._pipeline_text(
                    hw_encoder=encoder, target_object="101",
                    preserve_source_rate=True, wifi_mode=True,
                    rtp_endpoint=endpoint,
                )
                self.assertNotIn("monitorize_kwin_copy", text)

    def test_hardware_wifi_emits_aud_and_uses_one_frame_rate_control_buffer(self):
        with patch.object(
            pipeline_builder, "_probe_encoder_properties", side_effect=lambda value: value,
        ):
            nvenc = self._pipeline_text(hw_encoder="nvh264enc", wifi_mode=True)
            vaapi = self._pipeline_text(hw_encoder="vah264enc", wifi_mode=True)
        self.assertIn("aud=true", nvenc)
        self.assertIn("vbv-buffer-size=134", nvenc)
        self.assertIn("aud=true", vaapi)
        self.assertIn("cpb-size=134", vaapi)
        self.assertIn("async-depth=3", vaapi)
        self.assertNotIn("cpb-size=2000", vaapi)

    def test_low_latency_encoder_profile_keeps_current_nvenc_settings(self):
        with patch.object(
            pipeline_builder, "_probe_encoder_properties", side_effect=lambda value: value,
        ):
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

    def test_cpu_uses_parallel_same_frame_threads(self):
        text = self._pipeline_text()
        self.assertIn("sliced-threads=true", text)
        self.assertIn("threads=0", text)
        self.assertNotIn("threads=1", text)

    def test_tcp_client_backlog_is_bounded_for_low_latency(self):
        text = self._pipeline_text()
        self.assertIn("buffers-max=3", text)
        self.assertIn("buffers-soft-max=2", text)

    def test_rtp_video_uses_simple_interoperable_udp_packetization(self):
        text = self._pipeline_text(rtp_endpoint=("10.0.0.8", 49152))
        self.assertIn("rtph264pay", text)
        self.assertIn("aggregate-mode=none", text)
        self.assertIn("mtu=1200", text)
        self.assertNotIn("rtpulpfecenc", text)
        self.assertIn("udpsink host=10.0.0.8 port=49152", text)
        self.assertNotIn("tcpserversink", text)

    def test_fixed_cpu_rtp_gop_uses_thirty_second_cadence(self):
        text = self._pipeline_text(rtp_endpoint=("10.0.0.8", 49152))
        self.assertIn("key-int-max=1800", text)
        self.assertNotIn("intra-refresh", text)

    def test_thirty_second_gop_applies_to_all_rtp_encoders(self):
        cases = (
            ({}, "key-int-max"),
            ({"hw_encoder": "vah264enc", "wifi_mode": True}, "key-int-max"),
            ({"hw_encoder": "nvh264enc"}, "gop-size"),
        )
        for kwargs, property_name in cases:
            with self.subTest(kwargs=kwargs):
                endpoint = {"rtp_endpoint": ("10.0.0.8", 49152)}
                self.assertIn(
                    f"{property_name}=1800", self._pipeline_text(**endpoint, **kwargs)
                )
                self.assertIn(
                    f"{property_name}=3600",
                    self._pipeline_text(fps=120, **endpoint, **kwargs),
                )

    def test_tcp_gop_cadence_is_unchanged(self):
        self.assertIn("key-int-max=15", self._pipeline_text())
        self.assertIn("key-int-max=30", self._pipeline_text(fps=120))

    def test_wifi_bitrate_recommendation_matches_moonlight_curve(self):
        self.assertEqual(10_000, recommended_wifi_bitrate_kbps(1280, 720, 60))
        self.assertEqual(20_000, recommended_wifi_bitrate_kbps(1920, 1080, 60))
        self.assertEqual(23_000, recommended_wifi_bitrate_kbps(1920, 1200, 60))
        self.assertEqual(28_000, recommended_wifi_bitrate_kbps(1920, 1080, 120))
        self.assertEqual(80_000, recommended_wifi_bitrate_kbps(3840, 2160, 60))
        self.assertEqual(1_000, recommended_wifi_bitrate_kbps(320, 240, 24))
        self.assertEqual(100_000, recommended_wifi_bitrate_kbps(7680, 4320, 240))

    def test_nvidia_auto_prefers_same_gpu_gl_then_cuda_then_system(self):
        encoder = "memory:GLMemory memory:CUDAMemory"
        with (
            patch.object(
                pipeline_builder, "_gst_inspect",
                side_effect=lambda element: encoder if element == "nvh264enc" else "ok",
            ),
            patch.object(
                pipeline_builder, "_same_nvidia_kwin_gpu",
                return_value=(True, "NVIDIA GPU at 0000:01:00.0"),
            ),
        ):
            self.assertEqual(
                pipeline_builder._nvidia_memory_candidates(),
                ["gl", "cuda", "system"],
            )

    def test_nvidia_auto_skips_gl_when_gpu_identity_or_elements_fail(self):
        encoder = "memory:GLMemory memory:CUDAMemory"
        cases = (
            ((False, "KWin renders on AMD"), lambda _element: "ok"),
            ((True, "NVIDIA GPU"), lambda element: "" if element == "glupload" else "ok"),
        )
        for identity, inspect_gl in cases:
            with self.subTest(identity=identity):
                with (
                    patch.object(
                        pipeline_builder, "_gst_inspect",
                        side_effect=lambda element: (
                            encoder if element == "nvh264enc" else inspect_gl(element)
                        ),
                    ),
                    patch.object(
                        pipeline_builder, "_same_nvidia_kwin_gpu",
                        return_value=identity,
                    ),
                ):
                    self.assertEqual(
                        pipeline_builder._nvidia_memory_candidates(),
                        ["cuda", "system"],
                    )

    def test_same_nvidia_kwin_gpu_gate_is_fail_closed(self):
        cases = (
            (("NVIDIA Corporation", "NVIDIA GeForce RTX 4060 Laptop GPU"),
             [("NVIDIA GeForce RTX 4060 Laptop GPU", "0000:01:00.0", "Enabled")], True),
            (("AMD", "AMD Radeon 780M"),
             [("NVIDIA GeForce RTX 4060 Laptop GPU", "0000:01:00.0", "Enabled")], False),
            (("NVIDIA Corporation", "NVIDIA GeForce RTX 4060 Laptop GPU"),
             [("NVIDIA GeForce RTX 4060 Laptop GPU", "0000:01:00.0", "Disabled")], False),
            (("NVIDIA Corporation", "NVIDIA GeForce RTX 4060 Laptop GPU"), [], False),
            (("NVIDIA Corporation", "NVIDIA GeForce RTX 4060 Laptop GPU"),
             [("NVIDIA GeForce RTX 4070", "0000:01:00.0", "Enabled")], False),
            (("NVIDIA Corporation", "NVIDIA GeForce RTX 4060 Laptop GPU"), [
                ("NVIDIA GeForce RTX 4060 Laptop GPU", "0000:01:00.0", "Enabled"),
                ("NVIDIA GeForce RTX 4070", "0000:02:00.0", "Disabled"),
            ], False),
        )
        for renderer, gpus, expected in cases:
            with self.subTest(renderer=renderer, gpus=gpus):
                pipeline_builder._same_nvidia_kwin_gpu.cache_clear()
                with (
                    patch.object(pipeline_builder, "_kwin_renderer", return_value=renderer),
                    patch.object(pipeline_builder, "_nvidia_display_gpus", return_value=gpus),
                ):
                    self.assertEqual(
                        pipeline_builder._same_nvidia_kwin_gpu()[0], expected
                    )

    def test_explicit_nvidia_selection_never_becomes_cpu(self):
        with patch.object(pipeline_builder, "_gst_inspect", return_value=""):
            self.assertEqual(pipeline_builder.get_encoder("nvidia"), "nvh264enc")

    def test_nvenc_uses_fixed_short_gop_without_unsupported_intra_refresh(self):
        with patch.object(
            pipeline_builder, "_probe_encoder_properties", side_effect=lambda value: value,
        ):
            text = self._pipeline_text(hw_encoder="nvh264enc")
        self.assertIn("gop-size=15", text)
        self.assertIn("repeat-sequence-header=true", text)
        self.assertNotIn("intra-refresh", text)

    def test_encoder_probe_removes_unsupported_properties(self):
        inspected = """Element Properties:\n  bitrate             : target bitrate\n  bframes             : B frames\n"""
        with patch.object(pipeline_builder, "_gst_inspect", return_value=inspected):
            value = pipeline_builder._probe_encoder_properties(
                "nvh264enc bitrate=8000 bframes=0 not-installed=true"
            )
        self.assertEqual(value, "nvh264enc bitrate=8000 bframes=0")

    def test_nvenc_gl_path_detaches_dmabuf_through_cuda_memory(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", nvidia_memory="gl"
        )
        self.assertIn("always-copy=false", text)
        self.assertIn("memory:DMABuf", text)
        self.assertIn("format=DMA_DRM", text)
        self.assertIn("glupload", text)
        self.assertIn("glcolorconvert", text)
        self.assertIn("glcolorscale", text)
        self.assertIn("memory:GLMemory", text)
        self.assertIn("cudaupload", text)
        self.assertIn("memory:CUDAMemory", text)
        self.assertIn("format=RGBA", text)
        self.assertNotIn("format=NV12", text)
        self.assertNotIn("cudaconvertscale", text)

    def test_native_nvenc_gl_path_skips_scaling_but_keeps_gpu_copy(self):
        text = self._pipeline_text(
            hw_encoder="nvh264enc", nvidia_memory="gl",
            target_object="101", preserve_source_size=True,
            preserve_source_rate=True, wifi_mode=True,
            rtp_endpoint=("192.0.2.1", 49152, 1, "high"),
        )
        self.assertNotIn("glcolorscale", text)
        self.assertIn("memory:DMABuf", text)
        self.assertIn("memory:GLMemory", text)
        self.assertIn("cudaupload", text)
        self.assertIn("memory:CUDAMemory", text)

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

    def test_missing_gstreamer_fec_element_falls_back_to_off(self):
        proc = Mock(pid=123)
        proc.wait.side_effect = TimeoutExpired("gst-launch-1.0", 1.0)
        endpoint = ("192.0.2.1", 49152, 1, "constrained-baseline", 0)
        with (
            patch.dict(os.environ, {
                "MONITORIZE_VIDEO_TRANSPORT": "rtp-udp-v1",
                "MONITORIZE_FEC_PERCENT": "10",
            }),
            patch(
                "monitorize.streaming.pipeline_builder._gst_inspect",
                return_value="",
            ),
            patch(
                "monitorize.streaming.pipeline_builder.wait_for_client",
                return_value=endpoint,
            ) as wait,
            patch(
                "monitorize.streaming.pipeline_builder.subprocess.Popen",
                return_value=proc,
            ) as popen,
        ):
            pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, server_mode=True,
            )
        self.assertEqual(wait.call_args.kwargs["requested_fec_percent"], 0)
        self.assertNotIn("rtpulpfecenc", " ".join(popen.call_args.args[0]))

    def test_hardware_launch_falls_back_to_cpu_on_immediate_failure(self):
        failed = Mock()
        failed.pid = 1
        failed.returncode = 1
        failed.wait.return_value = 1
        cpu = Mock()
        cpu.pid = 2
        cpu.wait.side_effect = TimeoutExpired("gst-launch-1.0", 1.0)
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

    def test_udp_hardware_failure_does_not_fall_back_to_cpu_when_required(self):
        failed = Mock(pid=1, returncode=1)
        failed.wait.return_value = 1
        with (
            patch.dict(os.environ, {
                "MONITORIZE_VIDEO_TRANSPORT": "rtp-udp-v1",
                "MONITORIZE_REQUIRE_HARDWARE_ENCODER": "1",
            }),
            patch(
                "monitorize.streaming.pipeline_builder.subprocess.Popen",
                return_value=failed,
            ) as popen,
            patch(
                "monitorize.streaming.pipeline_builder._probe_encoder_properties",
                side_effect=lambda encoder: encoder,
            ),
        ):
            result = pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, hw_encoder="vah264enc",
            )
        self.assertIs(result, failed)
        self.assertEqual(popen.call_count, 1)
        self.assertIn("vah264enc", popen.call_args.args[0])

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
        failed.wait.assert_called_once_with(timeout=3.0)
        cuda.wait.assert_called_once_with(timeout=1.0)
        self.assertEqual(2, popen.call_count)

    def test_nvenc_exhausts_three_modes_without_cpu_fallback(self):
        failed = [Mock(pid=index, returncode=1) for index in range(1, 4)]
        for process in failed:
            process.wait.return_value = 1
        with (
            patch.dict(os.environ, {"MONITORIZE_NVIDIA_MEMORY": "auto"}),
            patch.object(
                pipeline_builder, "_nvidia_memory_candidates",
                return_value=["gl", "cuda", "system"],
            ),
            patch(
                "monitorize.streaming.pipeline_builder.subprocess.Popen",
                side_effect=failed,
            ) as popen,
        ):
            result = pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, hw_encoder="nvh264enc",
            )
        self.assertIs(result, failed[-1])
        self.assertEqual(popen.call_count, 3)
        self.assertFalse(any("x264enc" in call.args[0] for call in popen.call_args_list))

    def test_nvenc_memory_override_forces_one_path(self):
        failed = Mock(pid=1, returncode=1)
        failed.wait.return_value = 1
        with (
            patch.dict(os.environ, {"MONITORIZE_NVIDIA_MEMORY": "system"}),
            patch(
                "monitorize.streaming.pipeline_builder.subprocess.Popen",
                return_value=failed,
            ) as popen,
        ):
            result = pipeline_builder.launch_with_fallback(
                pw_fd=None, node_id=42, width=1280, height=800,
                fps=60, bitrate=8000, port=7110, hw_encoder="nvh264enc",
            )
        self.assertIs(result, failed)
        self.assertEqual(popen.call_count, 1)
        argv = popen.call_args.args[0]
        self.assertIn("nvh264enc", argv)
        self.assertNotIn("glupload", argv)
        self.assertNotIn("cudaupload", argv)


class GstSessionReadinessTest(unittest.TestCase):
    def test_bus_error_sets_nonzero_exit_status(self):
        session = gst_session.Session.__new__(gst_session.Session)
        session.exit_code = 0
        session.loop = Mock()
        message = Mock(type=gst_session.Gst.MessageType.ERROR)
        message.parse_error.return_value = (RuntimeError("failed"), "debug")
        session.bus_message(None, message)
        self.assertEqual(session.exit_code, 1)
        session.loop.quit.assert_called_once_with()

    def test_session_does_not_publish_ready_before_outer_probation(self):
        session = gst_session.Session.__new__(gst_session.Session)
        session.pipeline = Mock()
        session.pipeline.set_state.return_value = gst_session.Gst.StateChangeReturn.SUCCESS
        session.pipeline.get_by_name.return_value = None
        session.loop = Mock()
        session.running = True
        session.exit_code = 0
        session.control_loop = Mock()
        session.install_diagnostics = Mock()
        session.stop_sender = Mock()
        with (
            patch.object(gst_session.threading, "Thread", return_value=Mock()),
            patch("builtins.print") as output,
        ):
            self.assertEqual(session.run(), 0)
        self.assertFalse(any(
            call.args and call.args[0] == "[Pipeline] READY"
            for call in output.call_args_list
        ))


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
        controller._authorized_serials = lambda: ["test-device"]
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
        self.assertEqual(
            calls[3][0], ["reverse", "tcp:7120", "tcp:7120"]
        )
        controller._audio_done(0, None)
        self.assertEqual(controller.status, "Device ready!")
        self.assertFalse(controller.busy)

    def test_audio_reverse_failure_does_not_fail_usb_video(self):
        controller = UsbController()
        controller.touch_reverse_failed = False
        completed = []
        controller.scanFinished.connect(completed.append)

        controller._audio_done(1, None)

        self.assertEqual(controller.status, "Warning: audio unavailable")
        self.assertEqual(completed, [True])


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
        self.assertIn('HELPER_DESKTOP_FILE="${APP_ID}-kde-virtual-output.desktop"', installer)
        self.assertIn("monitorize-kde-virtual-output.desktop", nix_package)
        self.assertIn("monitorize-kde-virtual-output.desktop", rpm_spec)
        self.assertIn("Exec=/usr/bin/monitorize-kde-virtual-output", rpm_permission)
        self.assertNotIn("BuildArch:      noarch", rpm_spec)
        self.assertIn("kbuildsycoca6", installer)

    def test_native_rtp_sender_is_built_by_all_packages(self):
        root = Path(__file__).resolve().parents[2]
        packaging = [
            (root / "linux/scripts/install.sh").read_text(encoding="utf-8"),
            (root / "nix/package.nix").read_text(encoding="utf-8"),
            (root / "monitorize.spec").read_text(encoding="utf-8"),
        ]
        for text in packaging:
            self.assertIn("native/rtp_sender/build.sh", text)
            self.assertIn("monitorize-rtp-sender", text)

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
        for protocol, port in (("tcp", "7110"), ("udp", "7110"),
                               ("tcp", "7114"), ("udp", "7114"),
                               ("udp", "7113"), ("udp", "7117"),
                               ("tcp", "7120"), ("udp", "7120")):
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

    def test_streaming_page_has_a_lower_right_rtp_telemetry_overlay(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "StreamingPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn("readonly property var telemetry: backend.streamingTelemetry", qml)
        self.assertIn("visible: page.telemetry.available === true", qml)
        self.assertIn("anchors.right: parent.right", qml)
        self.assertIn("anchors.bottom: parent.bottom", qml)
        self.assertIn("lineHeightMode: Text.FixedHeight", qml)
        self.assertIn('text: "Wi-Fi RTP/UDP\\n"', qml)
        self.assertIn('" fps · encode path "', qml)
        self.assertIn("page.telemetry.bitrateKbps", qml)
        self.assertNotIn("page.telemetry.hostActivity", qml)
        self.assertNotIn("page.telemetry.controllerReason", qml)

    def test_wifi_pages_offer_non_destructive_bitrate_recommendations(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        wifi = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        streaming = (qml_dir / "StreamingPage.qml").read_text(encoding="utf-8")
        for qml in (wifi, streaming):
            self.assertIn("recommendedWifiBitrateKbps", qml)
            self.assertIn('text: "Use auto"', qml)
            self.assertNotIn('text: "Auto bitrate: " +', qml)
            self.assertIn("autoSelected", qml)
            self.assertIn("readonly property int optionChipWidth: 150", qml)
            self.assertIn(
                "Layout.preferredWidth: page.optionChipWidth * 1.5", qml
            )
            self.assertIn(
                "Layout.preferredWidth: page.optionChipWidth * 0.5", qml
            )
            self.assertGreaterEqual(
                qml.count("Layout.preferredWidth: page.optionChipWidth"), 3
            )
            self.assertNotIn(
                "page.optionChipWidth * 2 + page.optionGridSpacing", qml
            )
            self.assertNotIn("Reserves 10%", qml)
        self.assertIn("resolutionOrFpsChanged", wifi)
        self.assertIn("property bool autoBitrate: true", wifi)
        self.assertIn("primary: page.autoBitrate", wifi)
        self.assertIn("visible: page.isWifi", wifi)
        self.assertIn("page.autoBitrate = false", wifi)
        self.assertIn("Layout.fillWidth: true", wifi)
        self.assertIn("// Checkbox Settings, kept in the same grid column as the cards.", wifi)
        self.assertIn("secondResolutionOrFpsChanged", streaming)
        self.assertIn("property bool secondAutoBitrate: true", streaming)
        self.assertIn("primary: page.secondAutoBitrate", streaming)
        self.assertIn("visible: backend.isWifiStreaming", streaming)
        self.assertIn("page.secondAutoBitrate = false", streaming)
        self.assertIn("visible: backend.isWifiStreaming", streaming)
        self.assertIn("id: addDisplayWindow", streaming)
        self.assertIn("minimumWidth: 520", streaming)
        self.assertIn("minimumHeight: 420", streaming)

    def test_main_menu_presets_align_to_mode_cards(self):
        qml_path = (
            Path(__file__).resolve().parents[1]
            / "monitorize"
            / "qml"
            / "MainMenuPage.qml"
        )
        qml = qml_path.read_text(encoding="utf-8")
        self.assertIn("readonly property int modeCardWidth: Math.max(", qml)
        self.assertIn("Math.min(320, Math.floor(", qml)
        self.assertIn("(page.width - 40 - modeCardSpacing * 2) / 3", qml)
        self.assertIn("readonly property int modeCardSpacing: 30", qml)
        self.assertIn("readonly property int modeCardsWidth", qml)
        self.assertIn("id: modeCardsRow", qml)
        self.assertEqual(qml.count("implicitWidth: page.modeCardWidth"), 3)
        self.assertEqual(qml.count("Layout.preferredWidth: modeCardsRow.implicitWidth"), 2)
        self.assertIn("width: modeCardsRow.implicitWidth", qml)
        self.assertIn("horizontalAlignment: Text.AlignLeft", qml)
        self.assertIn("id: presetMenu", qml)
        self.assertIn("width: 132", qml)
        self.assertIn("padding: 6", qml)
        self.assertIn("radius: theme.controlRadius", qml)
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

    def test_receiver_stays_on_setup_page_with_disconnect_control(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        main_qml = (qml_dir / "main.qml").read_text(encoding="utf-8")
        setup_qml = (qml_dir / "ReceiverSetupPage.qml").read_text(encoding="utf-8")
        self.assertNotIn("ReceiverStreamingPage.qml", main_qml)
        self.assertIn('backend.isReceiving ? "Disconnect"', setup_qml)
        self.assertIn("backend.stopReceiving()", setup_qml)
        self.assertFalse((qml_dir / "ReceiverStreamingPage.qml").exists())

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

    def test_streaming_config_pages_expose_return_source(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        wifi_qml = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        usb_qml = (qml_dir / "UsbStep2Page.qml").read_text(encoding="utf-8")
        self.assertIn(
            'readonly property string returnPageSource: page.isWifi ? "WifiPage.qml" : "UsbStep2Page.qml"',
            wifi_qml,
        )
        self.assertNotIn('text: "Use encryption"', wifi_qml)
        self.assertNotIn("direct RTP/UDP", wifi_qml)
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
        self.assertNotIn("Wi-Fi video uses direct RTP/UDP", qml)
        self.assertNotIn("MUST EXACTLY MATCH", qml)
        self.assertNotIn("WarningCard", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 4)
        self.assertEqual(qml.count("chipWidth: page.optionChipWidth"), 4)
        self.assertEqual(qml.count("CustomComboBox {"), 2)
        self.assertIn("RowLayout {", chips_qml)
        self.assertIn("property int chipWidth: 112", chips_qml)
        self.assertIn("Layout.preferredWidth: chips.chipWidth", chips_qml)
        self.assertIn("theme.buttonBackgroundHover", chips_qml)
        self.assertIn("theme.buttonBackground", chips_qml)
        self.assertIn("function find(val)", chips_qml)
        self.assertIn('return "NVIDIA NVENC (Beta)"', chips_qml)
        self.assertIn('return "VA-API (Recommended)"', chips_qml)
        self.assertIn('return "ULPFEC 10% (Beta)"', chips_qml)
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
        ):
            self.assertIn(f"id: {control_id}", qml)

    def test_wifi_usb_settings_page_uses_toggles(self):
        qml_dir = Path(__file__).resolve().parents[1] / "monitorize" / "qml"
        qml = (qml_dir / "WifiPage.qml").read_text(encoding="utf-8")
        toggle_qml = (qml_dir / "CustomToggle.qml").read_text(encoding="utf-8")
        checkbox_qml = (qml_dir / "CustomCheckBox.qml").read_text(encoding="utf-8")
        self.assertEqual(qml.count("CustomToggle {"), 3)
        self.assertIn('text: "Enable Audio"', qml)
        self.assertIn('"Audio adds ≈0.13 Mbps;', qml)
        self.assertIn('"Audio adds 0.77 Mbps PCM;', qml)
        self.assertNotIn("CustomCheckBox {", qml)
        self.assertNotIn('text: "Use encryption"', qml)
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
        for control_id in ("touchCheck", "stylusCheck"):
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
        window_index = qml.index("id: addDisplayWindow")
        cancel_index = qml.index('text: "Cancel"', window_index)
        start_index = qml.index(
            'text: backend.detectedDe === "kde"', cancel_index
        )
        cancel_block = qml[cancel_index:start_index]
        self.assertIn("onClicked: addDisplayWindow.hide()", cancel_block)
        self.assertIn("parent.hovered ? theme.borderHover : theme.surface", cancel_block)
        self.assertIn("border.color: parent.hovered ? theme.borderHover : theme.border", cancel_block)
        self.assertIn("Behavior on border.color", cancel_block)
        window_block = qml[window_index:cancel_index]
        self.assertIn("import QtQuick.Window", qml)
        self.assertIn("flags: Qt.Dialog", window_block)
        self.assertIn("modality: Qt.ApplicationModal", window_block)
        self.assertIn("id: addDisplayScroll", window_block)
        self.assertIn("Layout.fillHeight: true", window_block)
        self.assertIn("ScrollBar.vertical.policy: ScrollBar.AsNeeded", window_block)
        self.assertNotIn("id: addDisplayPopup", qml)
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
        self.assertNotIn("backend.thirdEncryptionStatus", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 3)
        self.assertIn("width: 720", window_block)
        self.assertIn("height: 640", window_block)
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
        self.assertIn('portField.text = rec["port"] || "7110"', qml)
        self.assertIn("validator: IntValidator { bottom: 1; top: 65535 }", qml)
        self.assertNotIn("id: displayCombo", qml)
        self.assertNotIn('model: ["Second display (7110)", "Third display (7114)"]', qml)
        self.assertIn("id: decoderCombo", qml)
        self.assertEqual(qml.count("ChoiceChips {"), 1)
        self.assertEqual(qml.count("CustomComboBox {"), 0)
        self.assertIn('text: "Streaming stats overlay"', qml)
        self.assertIn('statsToggle.checked = rec["show_stats"] === true', qml)
        self.assertIn("backend.setReceiverStatsVisible(checked)", qml)
        self.assertIn("id: receiverScroll", qml)
        self.assertIn("contentHeight: receiverContent.implicitHeight", qml)
        self.assertIn("model: backend.discoveredDevices", qml)

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
            "discoveredDevices", "secondStreamActive", "presets", "presetLaunchStatus",
        } <= properties)
        self.assertTrue({
            "startStreaming", "stopStreaming", "connectToHost",
            "startHostDiscovery", "startUsbScan", "startSecondStream",
            "saveCurrentPreset", "launchPreset", "renamePreset", "deletePreset",
            "isAutostartEnabled", "setAutostartEnabled", "setReceiverStatsVisible",
        } <= methods)
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
            backend.connectToHost("", 7110, "Software")
            backend.connectToHost("host", 70000, "Software")
        connect.assert_not_called()
        self.assertEqual(backend.receiver.status, "Invalid host or port")
        backend.network_timer.stop()

    def test_backend_persists_and_applies_receiver_stats_live(self):
        with patch("monitorize.desktop.backend.get_local_ip", return_value="127.0.0.1"):
            backend = MonitorizeBackend("kde")
        with (
            patch("monitorize.desktop.backend.save_receiver_stats_visible") as save,
            patch.object(backend.receiver, "set_stats_visible") as apply_live,
        ):
            backend.setReceiverStatsVisible(True)
        save.assert_called_once_with(True)
        apply_live.assert_called_once_with(True)
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
