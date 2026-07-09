import struct
import unittest
import threading
import types
from unittest.mock import Mock, call, patch

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

    def test_gnome_primary_flag_reaches_geometry(self):
        daemon = daemon_module.InputDaemon(
            100, 100, de="gnome", gnome_primary=True,
        )
        self.assertTrue(daemon.geometry.gnome_primary)

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


class GnomeGeometryTest(unittest.TestCase):
    def gnome_state(self, connector="Meta-0"):
        mode = (
            "1920x1200@60", 1920, 1200, 60.0, 1.0, [1.0],
            {"is-current": True},
        )
        monitor = (connector, "MTR", "Monitorize Virtual", "serial-1")
        return (
            1,
            [(monitor, [mode], {})],
            [(0, 0, 1.0, 0, False, [monitor], {})],
            {},
        )

    def gnome_mixed_state(self):
        mode = (
            "1920x1200@60", 1920, 1200, 60.0, 1.0, [1.0],
            {"is-current": True},
        )
        primary = ("eDP-1", "DEL", "Built-in Display", "serial-2")
        virtual = ("Meta-0", "MTR", "Monitorize Virtual", "serial-1")
        return (
            1,
            [(primary, [mode], {}), (virtual, [mode], {})],
            [
                (0, 0, 1.0, 0, True, [primary], {}),
                (1920, 0, 1.0, 0, False, [virtual], {}),
            ],
            {},
        )

    def test_gnome_virtual_monitor_edid_from_state(self):
        self.assertEqual(
            geometry.gnome_virtual_monitor_edid_from_state(self.gnome_state()),
            ("MTR", "Monitorize Virtual", "serial-1"),
        )

    def test_gnome_primary_monitor_edid_from_state(self):
        self.assertEqual(
            geometry.gnome_primary_monitor_edid_from_state(self.gnome_mixed_state()),
            ("DEL", "Built-in Display", "serial-2"),
        )

    def test_gnome_virtual_monitor_edid_missing_without_virtual_connector(self):
        mode = (
            "1920x1200@60", 1920, 1200, 60.0, 1.0, [1.0],
            {"is-current": True},
        )
        monitor = ("HDMI-1", "DEL", "External Display", "serial-2")
        state = (
            1,
            [(monitor, [mode], {})],
            [(0, 0, 1.0, 0, True, [monitor], {})],
            {},
        )
        self.assertIsNone(geometry.gnome_virtual_monitor_edid_from_state(state))

    def test_gnome_mapping_writes_touch_and_stylus_settings(self):
        created = {}

        def fake_settings(schema, path):
            settings = Mock()
            created[(schema, path)] = settings
            return settings

        with patch(
            "monitorize.input_bridge.geometry._gio_settings",
            side_effect=fake_settings,
        ):
            self.assertTrue(
                geometry.write_gnome_input_mapping(
                    ("MTR", "Monitorize Virtual", "serial-1"),
                    stylus_features=True,
                )
            )

        touch = created[(
            geometry.GNOME_TOUCHSCREEN_SCHEMA,
            "/org/gnome/desktop/peripherals/touchscreens/4d5a:1001/",
        )]
        stylus = created[(
            geometry.GNOME_TABLET_SCHEMA,
            "/org/gnome/desktop/peripherals/tablets/4d5a:1002/",
        )]
        touch.set_strv.assert_called_once_with(
            "output", ["MTR", "Monitorize Virtual", "serial-1"]
        )
        stylus.set_strv.assert_called_once_with(
            "output", ["MTR", "Monitorize Virtual", "serial-1"]
        )
        stylus.set_string.assert_called_once_with("mapping", "absolute")

    def test_gnome_mapping_failure_is_not_fatal(self):
        with patch(
            "monitorize.input_bridge.geometry._gio_settings",
            side_effect=RuntimeError("no settings"),
        ):
            self.assertFalse(
                geometry.write_gnome_input_mapping(
                    ("MTR", "Monitorize Virtual", "serial-1"),
                    stylus_features=True,
                )
            )

    def test_gnome_map_devices_retries_until_virtual_edid_exists(self):
        geom = geometry.Geometry("gnome", 1920, 1200)
        states = [RuntimeError("not ready"), self.gnome_state()]
        written = []

        def fake_state():
            value = states.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with (
            patch.object(geom, "_mutter_state", side_effect=fake_state),
            patch(
                "monitorize.input_bridge.geometry.write_gnome_input_mapping",
                side_effect=lambda edid, stylus: (
                    written.append((edid, stylus)) or True
                ),
            ),
            patch("monitorize.input_bridge.geometry.time.sleep"),
        ):
            self.assertTrue(geom.map_gnome_devices(stylus_features=True))
        self.assertEqual(written, [(("MTR", "Monitorize Virtual", "serial-1"), True)])
        self.assertTrue(geom._gnome_devices_mapped)

    def test_gnome_map_devices_can_target_primary_monitor(self):
        geom = geometry.Geometry("gnome", 1920, 1200, gnome_primary=True)
        written = []

        with (
            patch.object(geom, "_mutter_state", return_value=self.gnome_mixed_state()),
            patch(
                "monitorize.input_bridge.geometry.write_gnome_input_mapping",
                side_effect=lambda edid, stylus: (
                    written.append((edid, stylus)) or True
                ),
            ),
        ):
            self.assertTrue(geom.map_gnome_devices(stylus_features=False))

        self.assertEqual(written, [(("DEL", "Built-in Display", "serial-2"), False)])
        self.assertTrue(geom._gnome_devices_mapped)

    def test_gnome_map_devices_failed_write_leaves_devices_unmapped(self):
        geom = geometry.Geometry("gnome", 1920, 1200)

        with (
            patch.object(geom, "_mutter_state", return_value=self.gnome_state()),
            patch(
                "monitorize.input_bridge.geometry.write_gnome_input_mapping",
                return_value=False,
            ),
        ):
            self.assertFalse(geom.map_gnome_devices(stylus_features=True))

        self.assertFalse(geom._gnome_devices_mapped)

    def test_gnome_mapped_uinput_bounds_are_virtual_local(self):
        geom = geometry.Geometry("gnome", 1920, 1200)
        geom._gnome_devices_mapped = True

        with (
            patch.object(
                geom,
                "virtual_rect",
                return_value=(2560.0, 0.0, 1280.0, 800.0),
            ),
            patch.object(geom, "desktop_bounds") as desktop_bounds,
        ):
            self.assertEqual(
                geom.uinput_bounds(),
                (1280, 800, 0.0, 0.0, 1280.0, 800.0),
            )

        desktop_bounds.assert_not_called()

    def test_gnome_unmapped_uinput_bounds_keep_desktop_fallback(self):
        geom = geometry.Geometry("gnome", 1920, 1200)

        with (
            patch.object(
                geom,
                "virtual_rect",
                return_value=(2560.0, 0.0, 1280.0, 800.0),
            ),
            patch.object(
                geom,
                "desktop_bounds",
                return_value=(0.0, 0.0, 3840.0, 1600.0),
            ),
        ):
            self.assertEqual(
                geom.uinput_bounds(),
                (3840, 1600, 2560.0, 0.0, 1280.0, 800.0),
            )

    def test_gnome_input_node_mapping_success(self):
        mapper = Mock()
        bus = Mock()
        bus.get_object.return_value = object()
        dbus = types.SimpleNamespace(Interface=Mock(return_value=mapper))

        self.assertTrue(
            geometry.gnome_input_node_is_mapped(
                "/dev/input/event11",
                bus=bus,
                dbus=dbus,
                log_failure=False,
            )
        )

        bus.get_object.assert_called_once_with(
            geometry.GNOME_INPUT_MAPPING_SERVICE,
            geometry.GNOME_INPUT_MAPPING_PATH,
        )
        dbus.Interface.assert_called_once_with(
            bus.get_object.return_value,
            geometry.GNOME_INPUT_MAPPING_IFACE,
        )
        mapper.GetDeviceMapping.assert_called_once_with("/dev/input/event11")

    def test_gnome_input_node_mapping_failure_is_false(self):
        mapper = Mock()
        mapper.GetDeviceMapping.side_effect = RuntimeError("not mapped")
        bus = Mock()
        bus.get_object.return_value = object()
        dbus = types.SimpleNamespace(Interface=Mock(return_value=mapper))

        self.assertFalse(
            geometry.gnome_input_node_is_mapped(
                "/dev/input/event11",
                bus=bus,
                dbus=dbus,
                log_failure=False,
            )
        )

    def test_gnome_input_node_mapping_failure_warns_by_default(self):
        mapper = Mock()
        mapper.GetDeviceMapping.side_effect = RuntimeError("not mapped")
        bus = Mock()
        bus.get_object.return_value = object()
        dbus = types.SimpleNamespace(Interface=Mock(return_value=mapper))

        with patch("monitorize.input_bridge.geometry.log") as log:
            self.assertFalse(
                geometry.gnome_input_node_is_mapped(
                    "/dev/input/event11",
                    bus=bus,
                    dbus=dbus,
                )
            )

        log.warning.assert_called_once()
        log.debug.assert_not_called()

    def test_gnome_input_node_mapping_failure_can_be_debug_only(self):
        mapper = Mock()
        mapper.GetDeviceMapping.side_effect = RuntimeError("not mapped")
        bus = Mock()
        bus.get_object.return_value = object()
        dbus = types.SimpleNamespace(Interface=Mock(return_value=mapper))

        with patch("monitorize.input_bridge.geometry.log") as log:
            self.assertFalse(
                geometry.gnome_input_node_is_mapped(
                    "/dev/input/event11",
                    bus=bus,
                    dbus=dbus,
                    log_failure=False,
                )
            )

        log.debug.assert_called_once()
        log.warning.assert_not_called()

    def test_gnome_verify_devices_reuses_session_bus(self):
        geom = geometry.Geometry("gnome", 1920, 1200)
        devices = [
            types.SimpleNamespace(device=types.SimpleNamespace(path="/dev/input/event10")),
            types.SimpleNamespace(device=types.SimpleNamespace(path="/dev/input/event11")),
        ]
        bus = Mock()
        dbus = types.SimpleNamespace(SessionBus=Mock(return_value=bus))

        with (
            patch.dict("sys.modules", {"dbus": dbus}),
            patch(
                "monitorize.input_bridge.geometry.gnome_input_node_is_mapped",
                return_value=True,
            ) as is_mapped,
        ):
            self.assertEqual(
                geom.verify_gnome_devices(devices),
                {"event10", "event11"},
            )

        dbus.SessionBus.assert_called_once_with()
        self.assertEqual(
            [call.kwargs["bus"] for call in is_mapped.call_args_list],
            [bus, bus],
        )
        self.assertEqual(
            [call.kwargs["log_failure"] for call in is_mapped.call_args_list],
            [False, False],
        )

    def test_gnome_verify_devices_ignores_dbus_unavailable(self):
        geom = geometry.Geometry("gnome", 1920, 1200)

        with patch.dict("sys.modules", {"dbus": None}):
            self.assertEqual(geom.verify_gnome_devices([]), set())

    def test_gnome_verify_devices_ignores_dbus_session_failure(self):
        geom = geometry.Geometry("gnome", 1920, 1200)

        class FakeDBusException(Exception):
            pass

        dbus = types.SimpleNamespace(
            SessionBus=Mock(side_effect=FakeDBusException("no session")),
            exceptions=types.SimpleNamespace(DBusException=FakeDBusException),
        )

        with patch.dict("sys.modules", {"dbus": dbus}):
            self.assertEqual(geom.verify_gnome_devices([]), set())

    def test_gnome_verify_devices_does_not_hide_unexpected_session_errors(self):
        geom = geometry.Geometry("gnome", 1920, 1200)

        class FakeDBusException(Exception):
            pass

        dbus = types.SimpleNamespace(
            SessionBus=Mock(side_effect=ValueError("bug")),
            exceptions=types.SimpleNamespace(DBusException=FakeDBusException),
        )

        with patch.dict("sys.modules", {"dbus": dbus}):
            with self.assertRaises(ValueError):
                geom.verify_gnome_devices([])

    def test_gnome_verify_devices_returns_mapped_event_names(self):
        geom = geometry.Geometry("gnome", 1920, 1200)
        touch = types.SimpleNamespace(
            device=types.SimpleNamespace(path="/dev/input/event10")
        )
        stylus = types.SimpleNamespace(
            device=types.SimpleNamespace(path="/dev/input/event11")
        )
        dbus = types.SimpleNamespace(
            SessionBus=Mock(return_value=Mock()),
            exceptions=types.SimpleNamespace(DBusException=RuntimeError),
        )

        with (
            patch.dict("sys.modules", {"dbus": dbus}),
            patch(
                "monitorize.input_bridge.geometry.gnome_input_node_is_mapped",
                side_effect=lambda path, **_kwargs: path.endswith("event11"),
            ),
        ):
            self.assertEqual(
                geom.verify_gnome_devices([touch, stylus]),
                {"event11"},
            )


class UInputCreationTest(unittest.TestCase):
    def test_uinput_devices_use_stable_monitorize_ids(self):
        ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            EV_MSC=4,
            ABS_X=0,
            ABS_Y=1,
            ABS_PRESSURE=24,
            ABS_DISTANCE=25,
            ABS_TILT_X=26,
            ABS_TILT_Y=27,
            ABS_MISC=40,
            ABS_MT_SLOT=47,
            ABS_MT_POSITION_X=53,
            ABS_MT_POSITION_Y=54,
            ABS_MT_TRACKING_ID=57,
            MSC_SERIAL=0,
            BTN_TOUCH=330,
            BTN_TOOL_PEN=320,
            BTN_TOOL_RUBBER=321,
            BTN_STYLUS=331,
            BTN_STYLUS2=332,
            BUS_USB=3,
            INPUT_PROP_DIRECT=1,
        )
        fake_evdev = types.SimpleNamespace(
            AbsInfo=lambda value, minimum, maximum, fuzz, flat, resolution: (
                value, minimum, maximum, fuzz, flat, resolution,
            )
        )
        devices = [
            types.SimpleNamespace(
                device=types.SimpleNamespace(path="/dev/input/event10")
            ),
            types.SimpleNamespace(
                device=types.SimpleNamespace(path="/dev/input/event11")
            ),
        ]
        geom = Mock(
            de="gnome",
            screen_w=1920,
            screen_h=1200,
            map_gnome_devices=Mock(return_value=True),
            verify_gnome_devices=Mock(return_value={"event10", "event11"}),
            uinput_bounds=Mock(return_value=(1920, 1200, 0, 0, 1920, 1200)),
            rotation=Mock(return_value=0),
        )

        with (
            patch.object(uinput_backend, "ecodes", ecodes),
            patch.object(uinput_backend, "evdev", fake_evdev),
            patch.object(uinput_backend, "UInput", side_effect=devices) as uinput,
            patch("monitorize.input_bridge.uinput_backend.time.sleep"),
        ):
            UInputBackend(geom, Mock()).setup(stylus_features=True)

        touch_kwargs = uinput.call_args_list[0].kwargs
        stylus_kwargs = uinput.call_args_list[1].kwargs
        self.assertEqual(touch_kwargs["vendor"], geometry.MONITORIZE_INPUT_VENDOR_ID)
        self.assertEqual(touch_kwargs["product"], geometry.MONITORIZE_TOUCH_PRODUCT_ID)
        self.assertEqual(stylus_kwargs["vendor"], geometry.MONITORIZE_INPUT_VENDOR_ID)
        self.assertEqual(
            stylus_kwargs["product"], geometry.MONITORIZE_STYLUS_PRODUCT_ID
        )
        geom.map_gnome_devices.assert_called_once_with(True)
        geom.verify_gnome_devices.assert_called_once_with(devices)

    def test_kde_missing_event_node_reports_permission_hint(self):
        ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            ABS_X=0,
            ABS_Y=1,
            ABS_MT_SLOT=47,
            ABS_MT_POSITION_X=53,
            ABS_MT_POSITION_Y=54,
            ABS_MT_TRACKING_ID=57,
            BTN_TOUCH=330,
            BUS_USB=3,
            INPUT_PROP_DIRECT=1,
        )
        fake_evdev = types.SimpleNamespace(
            AbsInfo=lambda value, minimum, maximum, fuzz, flat, resolution: (
                value, minimum, maximum, fuzz, flat, resolution,
            )
        )
        geom = Mock(
            de="kde",
            screen_w=1920,
            screen_h=1200,
            map_kde_devices=Mock(return_value=set()),
            uinput_bounds=Mock(return_value=(1920, 1200, 0, 0, 1920, 1200)),
            rotation=Mock(return_value=0),
        )

        with (
            patch.object(uinput_backend, "ecodes", ecodes),
            patch.object(uinput_backend, "evdev", fake_evdev),
            patch.object(
                uinput_backend,
                "UInput",
                return_value=types.SimpleNamespace(device=None),
            ),
            patch("monitorize.input_bridge.uinput_backend.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "MONITORIZE_UINPUT_PERMISSION"):
                UInputBackend(geom, Mock()).setup()

    def test_stylus_capabilities_include_tablet_metadata(self):
        ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            EV_MSC=4,
            ABS_X=0,
            ABS_Y=1,
            ABS_PRESSURE=24,
            ABS_DISTANCE=25,
            ABS_TILT_X=26,
            ABS_TILT_Y=27,
            ABS_MISC=40,
            MSC_SERIAL=0,
            BTN_TOUCH=330,
            BTN_TOOL_PEN=320,
            BTN_TOOL_RUBBER=321,
            BTN_STYLUS=331,
            BTN_STYLUS2=332,
        )
        fake_evdev = types.SimpleNamespace(
            AbsInfo=lambda value, minimum, maximum, fuzz, flat, resolution: (
                value, minimum, maximum, fuzz, flat, resolution,
            )
        )
        backend = UInputBackend(Mock(screen_w=1920, screen_h=1200), Mock())
        backend.max_x = 1920
        backend.max_y = 1200

        with (
            patch.object(uinput_backend, "ecodes", ecodes),
            patch.object(uinput_backend, "evdev", fake_evdev),
        ):
            caps = backend._stylus_capabilities()

        abs_codes = {item[0] for item in caps[ecodes.EV_ABS]}
        self.assertTrue({
            ecodes.ABS_X,
            ecodes.ABS_Y,
            ecodes.ABS_PRESSURE,
            ecodes.ABS_DISTANCE,
            ecodes.ABS_TILT_X,
            ecodes.ABS_TILT_Y,
            ecodes.ABS_MISC,
        } <= abs_codes)
        self.assertEqual(
            next(item for item in caps[ecodes.EV_ABS] if item[0] == ecodes.ABS_X)[1][-1],
            uinput_backend.STYLUS_AXIS_RESOLUTION,
        )
        self.assertEqual(caps[ecodes.EV_MSC], [ecodes.MSC_SERIAL])


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


class UInputStylusTest(unittest.TestCase):
    def setUp(self):
        self.ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            EV_MSC=4,
            ABS_X=0,
            ABS_Y=1,
            ABS_PRESSURE=24,
            ABS_DISTANCE=25,
            ABS_TILT_X=26,
            ABS_TILT_Y=27,
            ABS_MISC=40,
            MSC_SERIAL=0,
            BTN_TOUCH=330,
            BTN_TOOL_PEN=320,
            BTN_TOOL_RUBBER=321,
            BTN_STYLUS=331,
            BTN_STYLUS2=332,
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
        backend.stylus = Mock()
        return backend

    def test_pen_down_writes_pressure_and_tablet_metadata(self):
        backend = self.backend()

        self.assertTrue(
            backend.inject_pen(
                ACTION_DOWN,
                1,
                32768,
                32768,
                1234,
                12,
                -8,
                7,
                0,
                0,
            )
        )

        writes = backend.stylus.write.call_args_list
        self.assertIn(call(self.ecodes.EV_MSC, self.ecodes.MSC_SERIAL, 1), writes)
        self.assertIn(call(self.ecodes.EV_ABS, self.ecodes.ABS_MISC, 1), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOOL_PEN, 1), writes)
        self.assertIn(call(self.ecodes.EV_ABS, self.ecodes.ABS_PRESSURE, 1234), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOUCH, 1), writes)
        backend.stylus.syn.assert_called_once()

    def test_rubber_down_writes_rubber_serial_and_tablet_metadata(self):
        backend = self.backend()

        self.assertTrue(
            backend.inject_pen(
                ACTION_DOWN,
                2,
                32768,
                32768,
                1234,
                12,
                -8,
                7,
                0,
                0,
            )
        )

        writes = backend.stylus.write.call_args_list
        self.assertIn(call(self.ecodes.EV_MSC, self.ecodes.MSC_SERIAL, 2), writes)
        self.assertIn(call(self.ecodes.EV_ABS, self.ecodes.ABS_MISC, 2), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOOL_RUBBER, 1), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOOL_PEN, 0), writes)

    def test_pen_down_skips_tablet_metadata_when_ecodes_do_not_expose_it(self):
        limited_ecodes = types.SimpleNamespace(
            EV_ABS=3,
            EV_KEY=1,
            ABS_X=0,
            ABS_Y=1,
            ABS_PRESSURE=24,
            ABS_DISTANCE=25,
            ABS_TILT_X=26,
            ABS_TILT_Y=27,
            BTN_TOUCH=330,
            BTN_TOOL_PEN=320,
            BTN_TOOL_RUBBER=321,
            BTN_STYLUS=331,
            BTN_STYLUS2=332,
        )
        backend = self.backend()

        with patch.object(uinput_backend, "ecodes", limited_ecodes):
            self.assertTrue(
                backend.inject_pen(
                    ACTION_DOWN,
                    1,
                    32768,
                    32768,
                    1234,
                    12,
                    -8,
                    7,
                    0,
                    0,
                )
            )

        writes = backend.stylus.write.call_args_list
        self.assertNotIn(call(self.ecodes.EV_MSC, self.ecodes.MSC_SERIAL, 1), writes)
        self.assertNotIn(call(self.ecodes.EV_ABS, self.ecodes.ABS_MISC, 1), writes)
        self.assertIn(call(limited_ecodes.EV_ABS, limited_ecodes.ABS_PRESSURE, 1234), writes)

    def test_hover_and_up_clear_pressure(self):
        backend = self.backend()

        backend.inject_pen(ACTION_HOVER, 1, 100, 200, 500, 0, 0, 12, 0, 0)
        self.assertIn(
            call(self.ecodes.EV_ABS, self.ecodes.ABS_PRESSURE, 0),
            backend.stylus.write.call_args_list,
        )

        backend.stylus.reset_mock()
        backend.inject_pen(ACTION_UP, 1, 100, 200, 500, 0, 0, 12, 0, 0)
        writes = backend.stylus.write.call_args_list
        self.assertIn(call(self.ecodes.EV_ABS, self.ecodes.ABS_PRESSURE, 0), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOUCH, 0), writes)
        self.assertIn(call(self.ecodes.EV_KEY, self.ecodes.BTN_TOOL_PEN, 0), writes)


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
