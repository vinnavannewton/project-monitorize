import struct
import unittest
import threading
import types
from unittest.mock import Mock, patch

from input_bridge import daemon as daemon_module
from input_bridge import libei_backend, transport, uinput_backend
from input_bridge.dispatcher import InputDispatcher
from input_bridge.uinput_backend import UInputBackend
from input_bridge.protocol import (
    ACTION_DOWN,
    ACTION_HOVER,
    ACTION_MOVE,
    ACTION_UP,
    PAYLOAD_FMT,
    PAYLOAD_SIZE,
    PKT_PEN,
    PKT_TOUCH,
    parse_udp_packets,
    pop_framed_packets,
)


def framed(packet_type, payload):
    return len(payload).to_bytes(4, "big") + bytes([packet_type]) + payload


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.payload = struct.pack(
            PAYLOAD_FMT, ACTION_DOWN, 1, 2, 100, 200, 300, -4, 5
        )

    def test_tcp_parser_handles_partial_and_multiple_frames(self):
        buffer = bytearray(framed(PKT_TOUCH, self.payload)[:8])
        self.assertEqual(pop_framed_packets(buffer), [])
        buffer.extend(
            framed(PKT_TOUCH, self.payload)[8:] + framed(PKT_PEN, self.payload)
        )
        self.assertEqual(
            [packet_type for packet_type, _ in pop_framed_packets(buffer)],
            [PKT_TOUCH, PKT_PEN],
        )
        self.assertEqual(buffer, bytearray())

    def test_tcp_parser_discards_invalid_prefix(self):
        buffer = bytearray(b"junk" + framed(PKT_TOUCH, self.payload))
        self.assertEqual(pop_framed_packets(buffer), [(PKT_TOUCH, self.payload)])

    def test_udp_parser_accepts_framed_and_legacy_packets(self):
        self.assertEqual(
            parse_udp_packets(framed(PKT_TOUCH, self.payload)),
            [(PKT_TOUCH, self.payload)],
        )
        self.assertEqual(
            parse_udp_packets(bytes([PKT_PEN]) + self.payload),
            [(PKT_PEN, self.payload)],
        )
        self.assertEqual(parse_udp_packets(b"bad"), [])


class DispatcherTest(unittest.TestCase):
    def test_stylus_releases_fingers_and_suppresses_touch(self):
        backend = Mock()
        backend.inject_pen.return_value = True
        dispatcher = InputDispatcher(backend)
        dispatcher.dispatch_touch(ACTION_DOWN, 1, 100, 200)
        dispatcher.dispatch_pen(
            ACTION_HOVER, 1, 5, 300, 400, 0, 0, 0, 0, 0, 0
        )
        dispatcher.dispatch_touch(ACTION_MOVE, 1, 110, 210)
        self.assertEqual(
            backend.inject_touch.call_args_list[1].args[:4],
            (ACTION_UP, 1, 100, 200),
        )
        self.assertEqual(len(backend.inject_touch.call_args_list), 2)

    def test_pen_falls_back_to_separate_touch_slot(self):
        backend = Mock()
        backend.inject_pen.return_value = False
        dispatcher = InputDispatcher(backend)
        dispatcher.dispatch_pen(
            ACTION_DOWN, 1, 3, 100, 200, 0, 0, 0, 0, 0, 0
        )
        self.assertEqual(
            backend.inject_touch.call_args.args[:4],
            (ACTION_DOWN, 10008, 100, 200),
        )

    def test_malformed_packet_is_rejected(self):
        dispatcher = InputDispatcher(Mock())
        self.assertFalse(dispatcher.dispatch_packet(PKT_TOUCH, b"x" * (PAYLOAD_SIZE - 1)))

    def test_release_all_sends_up_for_active_fingers(self):
        backend = Mock()
        dispatcher = InputDispatcher(backend)
        self.assertTrue(hasattr(dispatcher.lock, "acquire"))
        dispatcher.dispatch_touch(ACTION_DOWN, 1, 100, 200)
        dispatcher.dispatch_touch(ACTION_MOVE, 1, 110, 210)
        dispatcher.release_all("test")
        self.assertEqual(
            backend.inject_touch.call_args_list[-1].args[:4],
            (ACTION_UP, 1, 110, 210),
        )
        self.assertEqual(dispatcher.active_fingers, {})


class DaemonStartupTest(unittest.TestCase):
    def test_backend_setup_completes_before_transport_starts(self):
        order = []

        class ImmediateThread:
            def __init__(self, target, args=(), daemon=False):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        daemon = daemon_module.InputDaemon(100, 100, de="hyprland")
        daemon.backend = Mock()
        daemon.backend.setup.side_effect = lambda _stylus: order.append("setup")

        def fake_transport(*_args):
            order.append("transport")
            daemon.shutdown.set()

        with (
            patch("input_bridge.daemon.run_tcp_server", side_effect=fake_transport),
            patch("input_bridge.daemon.threading.Thread", ImmediateThread),
            patch("input_bridge.daemon.signal.signal"),
        ):
            daemon.run()

        self.assertEqual(order, ["setup", "transport"])


class TransportTest(unittest.TestCase):
    def test_tcp_disconnect_releases_active_fingers(self):
        payload = struct.pack(
            PAYLOAD_FMT, ACTION_DOWN, 1, 2, 100, 200, 300, -4, 5
        )

        class FakeClient:
            def __init__(self):
                self.chunks = [framed(PKT_TOUCH, payload), b""]

            def setsockopt(self, *_args):
                pass

            def recv(self, _size):
                return self.chunks.pop(0)

            def close(self):
                pass

        backend = Mock()
        dispatcher = InputDispatcher(backend)
        transport.handle_client(FakeClient(), ("127.0.0.1", 1234), dispatcher, threading.Event())
        self.assertEqual(
            backend.inject_touch.call_args_list[-1].args[:4],
            (ACTION_UP, 2, 100, 200),
        )

    def test_tcp_server_uses_no_reuseport_or_fuser(self):
        shutdown = threading.Event()

        class FakeServer:
            def __init__(self):
                self.options = []

            def setsockopt(self, *args):
                self.options.append(args)

            def bind(self, _addr):
                pass

            def listen(self, _backlog):
                pass

            def settimeout(self, _timeout):
                pass

            def accept(self):
                shutdown.set()
                raise transport.socket.timeout()

            def close(self):
                pass

        server = FakeServer()
        with patch("input_bridge.transport.socket.socket", return_value=server):
            transport.run_tcp_server(Mock(), shutdown)
        option_names = [option[1] for option in server.options]
        self.assertIn(transport.socket.SO_REUSEADDR, option_names)
        self.assertNotIn(getattr(transport.socket, "SO_REUSEPORT", object()), option_names)

    def test_new_tcp_client_releases_old_client(self):
        state = transport.ActiveTcpClient()
        dispatcher = Mock()
        old = Mock()
        new = Mock()
        state.replace(old, dispatcher)
        state.replace(new, dispatcher)
        dispatcher.release_all.assert_called_once_with("tcp reconnect")
        old.close.assert_called_once()


class UInputCoordinatesTest(unittest.TestCase):
    def backend(self):
        geometry = Mock(screen_w=1600, screen_h=1000)
        backend = UInputBackend(geometry, Mock())
        backend.max_x = 1600
        backend.max_y = 1000
        backend.target = (0, 0, 1600, 1000)
        return backend

    def test_surface_coordinates_are_not_rotated_again(self):
        backend = self.backend()
        self.assertEqual(backend._coords(0, 0), (0, 0))
        self.assertEqual(backend._coords(65535, 65535), (1600, 1000))
        self.assertEqual(backend._coords(32768, 32768), (800, 500))

    def test_right_rotated_output_gets_clockwise_correction(self):
        backend = self.backend()
        backend.rotation = 90
        self.assertEqual(backend._coords(0, 0), (1600, 0))
        self.assertEqual(backend._coords(65535, 0), (1600, 1000))
        self.assertEqual(backend._coords(0, 65535), (0, 0))

    def test_180_rotated_output_gets_correction(self):
        backend = self.backend()
        backend.rotation = 180
        self.assertEqual(backend._coords(0, 0), (1600, 1000))
        self.assertEqual(backend._coords(65535, 65535), (0, 0))

    def test_270_rotated_output_gets_correction(self):
        backend = self.backend()
        backend.rotation = 270
        self.assertEqual(backend._coords(0, 0), (0, 1000))
        self.assertEqual(backend._coords(65535, 0), (0, 0))
        self.assertEqual(backend._coords(0, 65535), (1600, 1000))

    def test_coordinate_hot_path_does_not_query_compositor(self):
        backend = self.backend()
        backend._coords(100, 200)
        backend._coords(300, 400)
        backend.geometry.rotation.assert_not_called()


class UInputSlotTest(unittest.TestCase):
    def setUp(self):
        self.ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            ABS_MT_SLOT=47,
            ABS_MT_TRACKING_ID=57,
            ABS_MT_POSITION_X=53,
            ABS_MT_POSITION_Y=54,
            ABS_X=0,
            ABS_Y=1,
            BTN_TOUCH=330,
        )
        self.ecodes_patch = patch.object(uinput_backend, "ecodes", self.ecodes)
        self.ecodes_patch.start()

    def tearDown(self):
        self.ecodes_patch.stop()

    def backend(self):
        geometry = Mock(screen_w=1600, screen_h=1000)
        backend = UInputBackend(geometry, Mock())
        backend.max_x = 1600
        backend.max_y = 1000
        backend.target = (0, 0, 1600, 1000)
        backend.touch = Mock()
        return backend

    def test_slot_allocator_avoids_modulo_collision(self):
        backend = self.backend()
        backend.inject_touch(ACTION_DOWN, 1, 100, 100)
        backend.inject_touch(ACTION_DOWN, 11, 200, 200)
        self.assertEqual(backend.active[1], 0)
        self.assertEqual(backend.active[11], 1)

    def test_rejects_eleventh_contact_without_corrupting_slots(self):
        backend = self.backend()
        for cid in range(11):
            backend.inject_touch(ACTION_DOWN, cid, 100, 100)
        self.assertEqual(len(backend.active), 10)
        self.assertNotIn(10, backend.active)


class LibeiPointerFallbackTest(unittest.TestCase):
    def test_pointer_button_stays_down_until_last_contact_releases(self):
        backend = libei_backend.LibeiBackend(Mock(screen_w=1600, screen_h=1000), Mock())
        backend.ctx = Mock()
        backend.geometry.virtual_rect.return_value = (0, 0, 1600, 1000)
        backend.touch = Mock()
        backend.touch.regions = []
        backend.touch.capabilities = []
        with patch.object(
            libei_backend,
            "ei",
            types.SimpleNamespace(DeviceCapability=types.SimpleNamespace(TOUCH="touch")),
        ):
            backend.inject_touch(ACTION_DOWN, 1, 100, 100)
            backend.inject_touch(ACTION_DOWN, 2, 200, 200)
            backend.inject_touch(ACTION_UP, 1, 100, 100)
            false_calls = [
                call for call in backend.touch.button_button.call_args_list
                if call.args == (0x110, False)
            ]
            self.assertEqual(false_calls, [])
            backend.inject_touch(ACTION_UP, 2, 200, 200)
            false_calls = [
                call for call in backend.touch.button_button.call_args_list
                if call.args == (0x110, False)
            ]
            self.assertEqual(len(false_calls), 1)

    def test_true_touch_release_all_releases_contacts_without_pointer_button(self):
        backend = libei_backend.LibeiBackend(Mock(screen_w=1600, screen_h=1000), Mock())
        backend.ctx = Mock()
        contact = Mock()
        backend.touch = Mock()
        backend.touch.capabilities = ["touch"]
        backend.active = {1: contact}
        with patch.object(
            libei_backend,
            "ei",
            types.SimpleNamespace(DeviceCapability=types.SimpleNamespace(TOUCH="touch")),
        ):
            backend.release_all()
        contact.up.assert_called_once()
        backend.touch.button_button.assert_not_called()


if __name__ == "__main__":
    unittest.main()
