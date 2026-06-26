import struct
import unittest
import threading
import types
from unittest.mock import Mock, patch

from monitorize.input_bridge import daemon as daemon_module
from monitorize.input_bridge import geometry, transport, uinput_backend
from monitorize.input_bridge.dispatcher import InputDispatcher
from monitorize.input_bridge.uinput_backend import UInputBackend
from monitorize.input_bridge.protocol import (
    ACTION_DOWN,
    ACTION_HOVER,
    ACTION_MOVE,
    ACTION_UP,
    PEN_EXT_FMT,
    PAYLOAD_FMT,
    PAYLOAD_SIZE,
    PKT_PEN,
    PKT_PEN_EXT,
    PKT_TOUCH,
    TOOL_MOUSE,
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


class TransportCoalescingTest(unittest.TestCase):
    @staticmethod
    def payload(action, cid, x):
        return struct.pack(PAYLOAD_FMT, action, 0, cid, x, 200, 300, 0, 0)

    @staticmethod
    def pen_ext_payload(action, cid, x):
        return struct.pack(PEN_EXT_FMT, action, 1, cid, x, 200, 300, 0, 0, 0, 0, 0)

    @staticmethod
    def actions(packets):
        return [struct.unpack(PAYLOAD_FMT, payload)[0] for _pkt_type, payload in packets]

    @staticmethod
    def contacts_and_x(packets):
        return [
            (struct.unpack(PAYLOAD_FMT, payload)[2], struct.unpack(PAYLOAD_FMT, payload)[3])
            for _pkt_type, payload in packets
        ]

    def test_coalesces_multiple_moves_for_same_contact(self):
        packets = [
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 100)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 200)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 300)),
        ]

        self.assertEqual(
            self.contacts_and_x(transport.coalesce_motion_packets(packets)),
            [(1, 300)],
        )

    def test_keeps_down_latest_move_and_up(self):
        packets = [
            (PKT_TOUCH, self.payload(ACTION_DOWN, 1, 100)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 200)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 300)),
            (PKT_TOUCH, self.payload(ACTION_UP, 1, 300)),
        ]

        result = transport.coalesce_motion_packets(packets)

        self.assertEqual(self.actions(result), [ACTION_DOWN, ACTION_MOVE, ACTION_UP])
        self.assertEqual(self.contacts_and_x(result), [(1, 100), (1, 300), (1, 300)])

    def test_keeps_latest_move_per_contact(self):
        packets = [
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 100)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 2, 200)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 300)),
        ]

        self.assertEqual(
            self.contacts_and_x(transport.coalesce_motion_packets(packets)),
            [(2, 200), (1, 300)],
        )

    def test_coalesces_pen_and_pen_ext_motion(self):
        packets = [
            (PKT_PEN, self.payload(ACTION_MOVE, 1, 100)),
            (PKT_PEN, self.payload(ACTION_MOVE, 1, 200)),
            (PKT_PEN_EXT, self.pen_ext_payload(ACTION_HOVER, 1, 300)),
            (PKT_PEN_EXT, self.pen_ext_payload(ACTION_HOVER, 1, 400)),
        ]

        result = transport.coalesce_motion_packets(packets)

        self.assertEqual([pkt_type for pkt_type, _payload in result], [PKT_PEN, PKT_PEN_EXT])
        self.assertEqual(struct.unpack(PAYLOAD_FMT, result[0][1])[3], 200)
        self.assertEqual(struct.unpack(PEN_EXT_FMT, result[1][1])[3], 400)

    def test_dispatch_batch_frames_only_last_packet(self):
        dispatcher = Mock()
        packets = [
            (PKT_TOUCH, self.payload(ACTION_MOVE, 1, 100)),
            (PKT_TOUCH, self.payload(ACTION_MOVE, 2, 200)),
        ]

        transport.dispatch_packet_batch(dispatcher, packets)

        self.assertEqual(
            [call.args[2] for call in dispatcher.dispatch_packet.call_args_list],
            [False, True],
        )


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

    def test_mouse_packet_uses_pointer_instead_of_touch(self):
        payload = struct.pack(
            PAYLOAD_FMT, ACTION_HOVER, TOOL_MOUSE, 2, 100, 200, 0, 0, 0
        )
        backend = Mock()
        dispatcher = InputDispatcher(backend)

        self.assertTrue(dispatcher.dispatch_packet(PKT_TOUCH, payload))

        backend.inject_pointer.assert_called_once_with(ACTION_HOVER, 100, 200, 0, True)
        backend.inject_touch.assert_not_called()

    def test_mouse_packet_releases_same_contact_finger_first(self):
        payload = struct.pack(
            PAYLOAD_FMT, ACTION_HOVER, TOOL_MOUSE, 2, 300, 400, 0, 0, 0
        )
        backend = Mock()
        dispatcher = InputDispatcher(backend)
        dispatcher.dispatch_touch(ACTION_DOWN, 2, 100, 200)

        self.assertTrue(dispatcher.dispatch_packet(PKT_TOUCH, payload))

        self.assertEqual(
            backend.inject_touch.call_args_list[-1].args,
            (ACTION_UP, 2, 100, 200, False),
        )
        backend.inject_pointer.assert_called_once_with(ACTION_HOVER, 300, 400, 0, True)


class DaemonStartupTest(unittest.TestCase):
    def test_all_desktops_use_uinput_backend(self):
        for de in daemon_module.UINPUT_DESKTOPS:
            daemon = daemon_module.InputDaemon(100, 100, de=de)
            self.assertIsInstance(daemon.backend, UInputBackend)

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
            patch("monitorize.input_bridge.daemon.run_tcp_server", side_effect=fake_transport),
            patch("monitorize.input_bridge.daemon.threading.Thread", ImmediateThread),
            patch("monitorize.input_bridge.daemon.signal.signal"),
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
        with patch("monitorize.input_bridge.transport.socket.socket", return_value=server):
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


class KdeGeometryTest(unittest.TestCase):
    def test_kde_virtual_output_accepts_portal_created_virtual_name(self):
        outputs = [
            {"name": "eDP-1", "enabled": True, "connected": True},
            {"name": "Virtual-1", "enabled": True, "connected": True},
        ]
        self.assertEqual(geometry.kde_virtual_output(outputs)["name"], "Virtual-1")

    def test_kde_rect_uses_detected_virtual_output(self):
        geom = geometry.Geometry("kde", 2560, 1600)
        outputs = {
            "outputs": [
                {
                    "name": "Virtual-1",
                    "enabled": True,
                    "connected": True,
                    "pos": {"x": 1463, "y": 0},
                    "size": {"width": 2560, "height": 1600},
                    "scale": 1,
                }
            ]
        }
        with patch("monitorize.input_bridge.geometry.json_command", return_value=outputs):
            self.assertEqual(geom._rect_kde(), (1463.0, 0.0, 2560.0, 1600.0))

    def test_kde_portal_geometry_does_not_fallback_to_primary_output(self):
        geom = geometry.Geometry("kde", 1920, 1200)
        outputs = {
            "outputs": [
                {
                    "name": "eDP-1",
                    "enabled": True,
                    "connected": True,
                    "priority": 1,
                    "pos": {"x": 100, "y": 200},
                    "size": {"width": 2560, "height": 1600},
                    "scale": 1.5,
                },
            ]
        }
        with (
            patch.dict(
                geometry.os.environ,
                {"MONITORIZE_PORTAL_SOURCE_TYPE": "4"},
                clear=False,
            ),
            patch("monitorize.input_bridge.geometry.json_command", return_value=outputs),
        ):
            self.assertIsNone(geom._rect_kde())

    def test_kde_rotation_accepts_numeric_and_lowercase_values(self):
        cases = (
            (1, 0), (2, 270), (4, 180), (8, 90),
            ("none", 0), ("left", 270), ("inverted", 180), ("right", 90),
        )
        for value, expected in cases:
            geom = geometry.Geometry("kde", 1920, 1200)
            with patch("monitorize.input_bridge.geometry.json_command", return_value={
                "outputs": [{
                    "name": "Virtual-1",
                    "enabled": True,
                    "connected": True,
                    "rotation": value,
                }]
            }):
                self.assertEqual(geom.rotation(), expected)


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

if __name__ == "__main__":
    unittest.main()
