import sys
import types
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication

from gui.discovery_service import DiscoveryService
from gui.backend import MonitorizeBackend
from gui.receiver_controller import ReceiverController
from gui.streaming_controller import StreamingController
from gui.usb_controller import UsbController


app = QCoreApplication.instance() or QCoreApplication(sys.argv)


def process_mock():
    process = Mock()
    process.started.connect = Mock()
    process.readyReadStandardOutput.connect = Mock()
    process.finished.connect = Mock()
    process.errorOccurred.connect = Mock()
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


class StreamingControllerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
