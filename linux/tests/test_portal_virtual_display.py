import types
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.portal_virtual_display import run_portal_virtual_display


class PortalVirtualDisplayTest(unittest.TestCase):
    def run_portal(self, response_code=0, size=(1920, 1080), supported=True,
                   negotiated="READY 2560 1440\n", mapping="", mirrored=False,
                   mirrored_twice=False, repair_ok=True,
                   negotiator_exit=None):
        bus = Mock()
        bus.get_unique_name.return_value = ":1.42"
        callbacks = {}
        bus.add_signal_receiver.side_effect = lambda callback, **kwargs: (
            callbacks.update({kwargs["path"]: callback}) or Mock()
        )
        portal = Mock()
        session = Mock()
        properties = Mock()
        properties.Get.return_value = 4 if supported else 1
        used_interfaces = []
        def interface(obj, name):
            used_interfaces.append(name)
            return {"org.freedesktop.portal.ScreenCast": portal,
                    "org.freedesktop.portal.Session": session,
                    "org.freedesktop.DBus.Properties": properties}.get(name, Mock())
        def respond(values):
            def call(*args):
                options = args[-1]
                request_path = "/org/freedesktop/portal/desktop/request/1_42/" + options["handle_token"]
                callbacks[request_path](response_code, values)
                return request_path
            return call
        portal.CreateSession.side_effect = respond({"session_handle": "/session/test"})
        portal.SelectSources.side_effect = respond({})
        portal.Start.side_effect = respond({"streams": [(42, {"size": size, "position": (120, 0), "mapping_id": mapping})], "devices": 6})
        portal.OpenPipeWireRemote.return_value.take.side_effect = [99, 100]
        dbus = types.ModuleType("dbus")
        dbus.SessionBus = lambda: bus
        dbus.Interface = interface
        dbus.Dictionary = lambda value, **kw: value
        dbus.UInt32 = int
        dbus.Boolean = bool
        dbus.DBusException = RuntimeError
        glib = types.ModuleType("dbus.mainloop.glib")
        glib.DBusGMainLoop = Mock()
        context = Mock()
        context.pending.return_value = False
        gi = types.ModuleType("gi.repository")
        gi.GLib = Mock()
        gi.GLib.main_context_default.return_value = context
        stdin = Mock()
        stdin.readline.return_value = "quit"
        negotiator = Mock()
        negotiator.poll.return_value = negotiator_exit
        negotiator.stdout.readline.return_value = negotiated
        physical = {"id": 1, "name": "eDP-1"}
        portal_output = {
            "id": 8,
            "name": "Virtual-virtual-xdp-kde-monitorize",
        }
        clean_topology = {
            "name": portal_output["name"],
            "position": (120, 0),
            "position_repaired": False,
            "was_mirrored": False,
        }
        mirrored_topology = dict(clean_topology, was_mirrored=True)
        repairs = (
            [
                (True, mirrored_topology, "repaired"),
                (
                    True,
                    mirrored_topology if mirrored_twice else clean_topology,
                    "clean",
                ),
            ]
            if mirrored else
            [(repair_ok, clean_topology if repair_ok else {}, "repair failed")]
        )
        with patch.dict("sys.modules", {"dbus": dbus, "dbus.mainloop.glib": glib, "gi.repository": gi}), \
             patch("monitorize.platform.portal_virtual_display.signal.signal"), \
             patch("monitorize.platform.portal_virtual_display.sys.stdin", stdin), \
             patch("monitorize.platform.portal_virtual_display.select.select", side_effect=lambda fds, *args: (fds, [], [])), \
             patch("monitorize.platform.portal_virtual_display.subprocess.Popen", return_value=negotiator), \
             patch("monitorize.platform.portal_virtual_display.os.close") as close, \
             patch("monitorize.platform.portal_virtual_display.wait_for_kde_outputs", return_value=[physical]), \
             patch("monitorize.platform.portal_virtual_display.wait_for_new_kde_output", return_value=(portal_output, [physical, portal_output])), \
             patch("monitorize.platform.portal_virtual_display.repair_kde_extended_topology", side_effect=repairs), \
             patch("monitorize.platform.portal_virtual_display.wait_for_kde_output_absent", return_value=True), \
             patch("monitorize.streaming.headless_virtual_display._emit_event") as emit:
            result = run_portal_virtual_display("additional", 2560, 1440, 90)
        return result, emit, portal, session, close, used_interfaces, negotiator

    def test_session_owns_portal_until_removed_and_reports_actual_size(self):
        result, emit, portal, session, close, interfaces, _ = self.run_portal()
        self.assertEqual(result, 0)
        event = emit.call_args.args[0]
        self.assertEqual((event["width"], event["height"]), (2560, 1440))
        self.assertEqual(event["node_id"], 42)
        self.assertTrue(event["portal"])
        self.assertIn("org.freedesktop.portal.ScreenCast", interfaces)
        self.assertEqual(portal.SelectSources.call_args.args[-1]["types"], 4)
        session.Close.assert_called_once()
        self.assertEqual([call.args[0] for call in close.call_args_list], [100, 99])

    def test_true_mirror_is_repaired_before_recreating_capture_stream(self):
        result, emit, portal, session, close, _, _ = self.run_portal(mirrored=True)
        self.assertEqual(result, 0)
        self.assertEqual(portal.Start.call_count, 2)
        self.assertEqual(portal.OpenPipeWireRemote.call_count, 2)
        self.assertEqual(session.Close.call_count, 2)
        emit.assert_called_once()
        self.assertEqual([call.args[0] for call in close.call_args_list], [100, 99])

    def test_repeated_true_mirror_fails_without_exposing_pipewire_stream(self):
        result, emit, portal, session, close, _, _ = self.run_portal(
            mirrored=True, mirrored_twice=True
        )
        self.assertEqual(result, 1)
        self.assertEqual(portal.Start.call_count, 2)
        portal.OpenPipeWireRemote.assert_not_called()
        self.assertEqual(session.Close.call_count, 2)
        emit.assert_not_called()
        close.assert_not_called()

    def test_topology_repair_failure_does_not_report_ready(self):
        result, emit, portal, session, close, _, _ = self.run_portal(repair_ok=False)
        self.assertEqual(result, 1)
        emit.assert_not_called()
        portal.OpenPipeWireRemote.assert_not_called()
        session.Close.assert_called_once()
        close.assert_not_called()

    def test_cancelled_permission_does_not_report_display_ready(self):
        result, emit, portal, *_ = self.run_portal(response_code=1)
        self.assertEqual(result, 1)
        emit.assert_not_called()
        portal.OpenPipeWireRemote.assert_not_called()

    def test_failed_resize_uses_valid_portal_size(self):
        result, emit, _, session, close, _, negotiator = self.run_portal(
            negotiated="", negotiator_exit=1
        )
        self.assertEqual(result, 0)
        event = emit.call_args.args[0]
        self.assertEqual((event["width"], event["height"]), (1920, 1080))
        session.Close.assert_called_once()
        self.assertEqual([call.args[0] for call in close.call_args_list], [100, 99])
        negotiator.stdout.close.assert_called_once()

    def test_matching_fixed_portal_size_skips_resize_negotiator(self):
        result, emit, _, session, close, _, negotiator = self.run_portal(
            size=(2560, 1440)
        )
        self.assertEqual(result, 0)
        event = emit.call_args.args[0]
        self.assertEqual((event["width"], event["height"]), (2560, 1440))
        negotiator.stdout.readline.assert_not_called()
        negotiator.stdout.close.assert_not_called()
        session.Close.assert_called_once()
        close.assert_called_once_with(99)

    def test_invalid_size_closes_session_without_claiming_ready(self):
        result, emit, _, session, close, _, _ = self.run_portal(size=(0, 0))
        self.assertEqual(result, 1)
        emit.assert_not_called()
        session.Close.assert_called_once()
        close.assert_not_called()

    def test_unsupported_portal_never_requests_a_virtual_display(self):
        result, emit, portal, *_ = self.run_portal(supported=False)
        self.assertEqual(result, 1)
        emit.assert_not_called()
        portal.CreateSession.assert_not_called()
