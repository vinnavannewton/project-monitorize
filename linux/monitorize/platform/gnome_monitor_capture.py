"""Own a Mutter ScreenCast session for an existing GNOME output."""

import time


class GnomeMonitorCapture:
    """Keep an existing monitor's PipeWire stream alive until explicitly closed."""

    def __init__(self):
        self.session = None
        self.node_id = 0
        self.closed = False
        self.context = None

    def start(self, connector, timeout=10.0):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        from monitorize.platform import gnome_virtual_monitor

        DBusGMainLoop(set_as_default=True)
        self.context = GLib.main_context_default()
        bus = dbus.SessionBus()
        service = 'org.gnome.Mutter.ScreenCast'
        manager = dbus.Interface(bus.get_object(service, '/org/gnome/Mutter/ScreenCast'), service)
        path = manager.CreateSession({})
        self.session = dbus.Interface(bus.get_object(service, path), service + '.Session')
        try:
            self.session.connect_to_signal('Closed', self._closed)
            path = self.session.RecordMonitor(connector, {'cursor-mode': dbus.UInt32(1)})
            stream = bus.get_object(service, path)
            stream.connect_to_signal('PipeWireStreamAdded', self._stream_added,
                                     dbus_interface=service + '.Stream')
            self.session.Start()
            deadline = time.monotonic() + timeout
            while not self.node_id and not self.closed and time.monotonic() < deadline:
                self.dispatch()
                time.sleep(0.01)
            if not self.node_id or self.closed:
                raise RuntimeError(f'Mutter did not provide a PipeWire capture stream for {connector}')
            state = gnome_virtual_monitor.display_config_interface(bus, dbus).GetCurrentState()
            for logical in state[2]:
                if connector in [str(spec[0]) for spec in logical[5]]:
                    return {'node_id': self.node_id, 'offset_x': int(logical[0]), 'offset_y': int(logical[1])}
            raise RuntimeError(f'GNOME output {connector} disappeared before capture was ready')
        except BaseException:
            self.close()
            raise

    def _stream_added(self, node_id):
        self.node_id = int(node_id)

    def _closed(self, *_args):
        self.closed = True

    def dispatch(self):
        while self.context.pending():
            self.context.iteration(False)
        if self.closed:
            raise RuntimeError('GNOME closed the virtual monitor capture session')

    def close(self):
        session, self.session = self.session, None
        self.closed = True
        if session is not None:
            try:
                session.Stop()
            except Exception:
                pass
