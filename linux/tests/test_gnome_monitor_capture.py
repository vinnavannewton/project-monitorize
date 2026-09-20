import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitorize.platform.gnome_monitor_capture import GnomeMonitorCapture


class GnomeMonitorCaptureTest(unittest.TestCase):
    def setup_capture(self):
        capture = GnomeMonitorCapture()
        manager, session, stream, bus = Mock(), Mock(), Mock(), Mock()
        manager.CreateSession.return_value = '/session'
        session.RecordMonitor.return_value = '/stream'
        bus.get_object.side_effect = [manager, session, stream]
        dbus = SimpleNamespace(SessionBus=lambda: bus, Interface=lambda obj, _name: obj, UInt32=int)
        context = Mock()
        context.pending.return_value = False
        modules = {
            'dbus': dbus,
            'dbus.mainloop': SimpleNamespace(),
            'dbus.mainloop.glib': SimpleNamespace(DBusGMainLoop=Mock()),
            'gi.repository': SimpleNamespace(GLib=SimpleNamespace(main_context_default=lambda: context)),
        }
        return capture, session, stream, modules

    def test_records_existing_monitor_and_keeps_session_until_close(self):
        capture, session, stream, modules = self.setup_capture()
        session.Start.side_effect = lambda: stream.connect_to_signal.call_args.args[1](42)
        display = Mock()
        display.GetCurrentState.return_value = (1, [], [(1920, 0, 1, 0, False, [('Virtual-1',)], {})], {})
        with (patch.dict('sys.modules', modules),
              patch('monitorize.platform.gnome_virtual_monitor.display_config_interface', return_value=display)):
            self.assertEqual(capture.start('Virtual-1'), {'node_id': 42, 'offset_x': 1920, 'offset_y': 0})
        session.RecordMonitor.assert_called_once_with('Virtual-1', {'cursor-mode': 1})
        session.RecordVirtual.assert_not_called()
        session.Stop.assert_not_called()
        capture.close()
        capture.close()
        session.Stop.assert_called_once()

    def test_missing_node_closes_session(self):
        capture, session, _stream, modules = self.setup_capture()
        with patch.dict('sys.modules', modules):
            with self.assertRaisesRegex(RuntimeError, 'did not provide'):
                capture.start('Virtual-1', timeout=0)
        session.Stop.assert_called_once()

    def test_closed_session_is_reported(self):
        capture = GnomeMonitorCapture()
        capture.context = Mock()
        capture.context.pending.return_value = False
        capture._closed()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            capture.dispatch()
