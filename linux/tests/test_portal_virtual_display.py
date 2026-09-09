import types
import unittest
from unittest.mock import Mock, patch

from monitorize.platform.portal_virtual_display import run_portal_virtual_display


class PortalVirtualDisplayTest(unittest.TestCase):
    def run_portal(self, response_code=0, size=(1920, 1080), supported=True,
                   negotiated="READY 2560 1440\n", mapping=""):
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
        portal.OpenPipeWireRemote.return_value.take.return_value = 99
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
        negotiator.poll.return_value = None
        negotiator.stdout.readline.return_value = negotiated
        with patch.dict("sys.modules", {"dbus": dbus, "dbus.mainloop.glib": glib, "gi.repository": gi}), \
             patch("monitorize.platform.portal_virtual_display.signal.signal"), \
             patch("monitorize.platform.portal_virtual_display.sys.stdin", stdin), \
             patch("monitorize.platform.portal_virtual_display.select.select", side_effect=lambda fds, *args: (fds, [], [])), \
             patch("monitorize.platform.portal_virtual_display.subprocess.Popen", return_value=negotiator), \
             patch("monitorize.platform.portal_virtual_display.os.close") as close, \
             patch("monitorize.streaming.headless_virtual_display._emit_event") as emit:
            result = run_portal_virtual_display("additional", 2560, 1440, 90)
        return result, emit, portal, session, close, used_interfaces

    def test_session_owns_portal_until_removed_and_reports_actual_size(self):
        result, emit, portal, session, close, interfaces = self.run_portal()
        self.assertEqual(result, 0)
        event = emit.call_args.args[0]
        self.assertEqual((event["width"], event["height"]), (2560, 1440))
        self.assertEqual(event["node_id"], 42)
        self.assertTrue(event["portal"])
        self.assertIn("org.freedesktop.portal.ScreenCast", interfaces)
        self.assertEqual(portal.SelectSources.call_args.args[-1]["types"], 4)
        session.Close.assert_called_once()
        close.assert_called_once_with(99)

    def test_cancelled_permission_does_not_report_display_ready(self):
        result, emit, portal, *_ = self.run_portal(response_code=1)
        self.assertEqual(result, 1)
        emit.assert_not_called()
        portal.OpenPipeWireRemote.assert_not_called()

    def test_wrong_negotiated_size_does_not_claim_success(self):
        result, emit, _, session, close, _ = self.run_portal(negotiated="READY 1920 1080\n")
        self.assertEqual(result, 1)
        emit.assert_not_called()
        session.Close.assert_called_once()
        close.assert_called_once_with(99)

    def test_invalid_size_closes_session_without_claiming_ready(self):
        result, emit, _, session, close, _ = self.run_portal(size=(0, 0))
        self.assertEqual(result, 1)
        emit.assert_not_called()
        session.Close.assert_called_once()
        close.assert_not_called()

    def test_unsupported_portal_never_requests_a_virtual_display(self):
        result, emit, portal, *_ = self.run_portal(supported=False)
        self.assertEqual(result, 1)
        emit.assert_not_called()
        portal.CreateSession.assert_not_called()
