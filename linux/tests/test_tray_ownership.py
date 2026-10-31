import time
import unittest
import uuid
from unittest.mock import patch, Mock
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtNetwork import QLocalServer
from monitorize.desktop import tray_agent


class TrayOwnershipTest(unittest.TestCase):
    def setUp(self):
        remote=patch('monitorize.__main__._remote_controller_required',return_value=False)
        remote.start();self.addCleanup(remote.stop)

    def test_login_initializes_hidden_listener_but_idle_handoff_does_not(self):
        from monitorize.__main__ import _use_light_tray
        with patch('monitorize.__main__.load_general_settings', return_value={'client_listen_on_open': True}):
            self.assertFalse(_use_light_tray(['monitorize', '--tray-agent']))
            self.assertFalse(_use_light_tray(['monitorize', '--start-in-tray']))
            self.assertTrue(_use_light_tray(['monitorize', '--tray-agent', '--restore-from-tray']))
            self.assertFalse(_use_light_tray(['monitorize', '--restore-from-tray']))
        with patch('monitorize.__main__.load_general_settings', return_value={'client_listen_on_open': False}):
            self.assertFalse(_use_light_tray(['monitorize', '--tray-agent']))
        with patch('monitorize.__main__.load_general_settings',return_value={'streaming_backend':'none'}):
            self.assertTrue(_use_light_tray(['monitorize','--tray-agent']))

    def test_login_dispatches_full_controller_hidden_when_listening_enabled(self):
        from monitorize import __main__ as entry
        argv = ['monitorize', '--tray-agent']
        with patch.object(entry.sys, 'argv', argv), patch.object(entry, 'load_general_settings', return_value={'client_listen_on_open': True}), patch('monitorize.desktop.main_window.main') as full, patch('monitorize.desktop.tray_agent.main') as light:
            entry.main()
        full.assert_called_once()
        light.assert_not_called()
        self.assertIn('--start-in-tray', argv)

    def test_restoring_tray_window_does_not_request_launch_autostart(self):
        from monitorize.desktop.main_window import _should_start_configured_listeners
        self.assertTrue(_should_start_configured_listeners(['monitorize']))
        self.assertFalse(_should_start_configured_listeners(['monitorize', '--restore-from-tray']))
        self.assertFalse(_should_start_configured_listeners(['monitorize'], restored_from_tray=True))
        agent = SimpleNamespace(_launch_and_quit=Mock())
        tray_agent.TrayAgent.show_app(agent)
        agent._launch_and_quit.assert_called_once_with(['--restore-from-tray'])

    def test_single_tray_owner_and_full_app_handoff(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        name = 'monitorize-tray-test-' + uuid.uuid4().hex
        with patch.object(tray_agent, 'TRAY_SERVER', name):
            server = tray_agent._claim_tray(app)
            self.assertIsNotNone(server)
            try:
                self.assertIsNone(tray_agent._claim_tray(app))
                with patch.object(app, 'quit') as quit_app:
                    tray_agent.dismiss_tray_agent()
                    deadline = time.monotonic() + 1
                    while not quit_app.called and time.monotonic() < deadline:
                        app.processEvents()
                    quit_app.assert_called_once()
            finally:
                server.close()
                server.deleteLater()
                QLocalServer.removeServer(name)

    def test_hidden_start_stays_hidden_before_tray_host_is_ready(self):
        from monitorize.desktop.main_window import _show_initial_window
        window=SimpleNamespace(tray=Mock(),show=Mock())
        with patch('monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable',return_value=False), patch('monitorize.desktop.main_window.QApplication.setQuitOnLastWindowClosed') as keep_alive, patch('monitorize.desktop.main_window.app_log.write'):
            self.assertFalse(_show_initial_window(window,True))
        window.show.assert_not_called();window.tray.show.assert_called_once()
        keep_alive.assert_called_once_with(False)

    def test_manual_start_shows_window(self):
        from monitorize.desktop.main_window import _show_initial_window
        window=SimpleNamespace(tray=Mock(),show=Mock())
        self.assertTrue(_show_initial_window(window,False))
        window.show.assert_called_once()
        window.tray.show.assert_called_once()

    def test_window_restore_keeps_tray_and_does_not_start_session(self):
        from monitorize.desktop.main_window import MonitorizeWindow
        window=SimpleNamespace(tray=Mock(),showNormal=Mock(),raise_=Mock(),activateWindow=Mock(),backend=Mock())
        MonitorizeWindow._restore_from_tray(window)
        window.tray.show.assert_called_once();window.tray.hide.assert_not_called()
        window.backend.startConfiguredListeners.assert_not_called()

    def test_singleton_lock_preserves_existing_socket_and_forwards_show(self):
        import tempfile
        from monitorize.desktop import main_window as module
        app=QCoreApplication.instance() or QCoreApplication([])
        name='monitorize-singleton-test-'+uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as d, patch.object(module,'INSTANCE_SERVER',name), patch.object(module.QStandardPaths,'writableLocation',return_value=d), patch.object(module.app_log,'write'):
            server,lock=module._claim_instance(app,b'show')
            self.assertIsNotNone(server)
            window=SimpleNamespace(_restore_from_tray=Mock(),backend=Mock())
            server.newConnection.connect(lambda:module._handle_instance_command(server,window))
            try:
                second,other=module._claim_instance(app,b'show')
                self.assertIsNone(second);self.assertIsNone(other)
                deadline=time.monotonic()+1
                while not window._restore_from_tray.called and time.monotonic()<deadline:app.processEvents()
                window._restore_from_tray.assert_called_once()
                self.assertTrue(server.isListening())
            finally:
                server.close();lock.unlock();server.deleteLater();QLocalServer.removeServer(name)

    def test_unresponsive_owner_does_not_remove_socket_or_launch_duplicate(self):
        from monitorize.desktop import main_window as module
        lock=Mock();lock.tryLock.return_value=False
        with patch.object(module,'QLockFile',return_value=lock), patch.object(module,'_forward_to_instance',return_value=False), patch.object(module.QLocalServer,'removeServer') as remove, patch.object(module.app_log,'write'):
            self.assertEqual(module._claim_instance(QCoreApplication.instance(),b'show'),(None,None))
            remove.assert_not_called()

    def test_explicit_quit_close_event_bypasses_minimize_and_handoff(self):
        from monitorize.desktop.main_window import MonitorizeWindow
        window=SimpleNamespace(_quitting=True,backend=Mock(),_quit_to_tray_agent=Mock())
        event=Mock()
        MonitorizeWindow.closeEvent(window,event)
        event.accept.assert_called_once()
        event.ignore.assert_not_called()
        window.backend.should_minimize_to_tray.assert_not_called()
        window._quit_to_tray_agent.assert_not_called()

    def test_cancelled_quit_does_not_mark_shutdown(self):
        from monitorize.desktop.main_window import MonitorizeWindow, QMessageBox
        window=SimpleNamespace(backend=SimpleNamespace(_vkms_management=None,configurationDirty=True,close=Mock()),
                               _finish_quit_when_displays_stop=Mock())
        with patch('monitorize.desktop.main_window.QMessageBox.question',return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(MonitorizeWindow._quit_app(window))
        self.assertFalse(getattr(window,'_quitting',False))
        window.backend.close.assert_not_called()

    def test_regular_window_close_still_minimizes_to_existing_tray_when_active(self):
        from monitorize.desktop.main_window import MonitorizeWindow
        window=SimpleNamespace(backend=SimpleNamespace(should_minimize_to_tray=lambda:True,isStreaming=True),
            hide=Mock(),tray=Mock(),_quit_to_tray_agent=Mock())
        event=Mock()
        with patch('monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable',return_value=True):
            MonitorizeWindow.closeEvent(window,event)
        event.ignore.assert_called_once();window.hide.assert_called_once();window.tray.show.assert_called_once()
        window._quit_to_tray_agent.assert_not_called()
