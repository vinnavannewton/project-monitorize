import struct
import unittest
from unittest.mock import Mock, patch

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

    def test_coordinate_hot_path_does_not_query_compositor(self):
        backend = self.backend()
        backend._coords(100, 200)
        backend._coords(300, 400)
        backend.geometry.rotation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
