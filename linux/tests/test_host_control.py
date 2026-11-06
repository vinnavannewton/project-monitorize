import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from monitorize.desktop import host_control


class Restart(QObject):
    finished = pyqtSignal(bool, str)


class HostControlTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        settings = patch.multiple('monitorize.config.host_config',
                                  config_path=lambda: directory / 'monitorize.json',
                                  saved_revision=lambda: 'saved',
                                  sunshine_revision=lambda: 'sunshine',
                                  streaming_revision=lambda: self.streaming_revision)
        settings.start()
        self.addCleanup(settings.stop)
        runtime = patch.dict(os.environ, XDG_RUNTIME_DIR=str(directory))
        runtime.start()
        self.addCleanup(runtime.stop)
        self.streaming_revision = 'streaming'
        self.backend = SimpleNamespace(
            localIp='192.0.2.1', configurationRestartRequired=False, remoteLoginEnabled=False,
            sessionRunning=False, sessionBusy=False, isStreaming=False, applyingConfiguration=False,
            systemSetupRunning=False, _restart=Restart(),
        )
        self.backend.startHostSession = lambda: self._running(True)
        self.backend.markHostSessionVerified = lambda: None
        self.backend.stopSession = lambda: self._running(False)
        self.backend._configuration_ready = lambda: self.backend.sessionRunning
        self.backend._configuration_failure = lambda: ''
        self.backend._owned_session_stopped = lambda: not self.backend.sessionRunning
        self.window = QObject()
        self.window.backend = self.backend

    def _running(self, running):
        self.backend.sessionRunning = self.backend.isStreaming = running

    def _serve(self):
        server = host_control.serve(self.window)
        self.addCleanup(server.close)

    def _request(self, *args, **kwargs):
        with ThreadPoolExecutor() as executor:
            future = executor.submit(host_control.request, *args, **kwargs)
            deadline = time.monotonic() + 2
            while not future.done() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            # Closing the test server releases a waiting client on regressions.
            if not future.done():
                for peer in self.window.findChildren(QObject):
                    if hasattr(peer, 'abort'):
                        peer.abort()
                self.fail('Monitorize socket did not respond')
            return future.result()

    def test_running_owner_handles_start_status_and_stop(self):
        with patch('monitorize.platform.sunshine_service.is_sunshine_running',
                   side_effect=lambda instance: self.backend.sessionRunning and instance == 1):
            self._serve()
            result = self._request('start')
            self.assertTrue(result['ready'])
            self.assertEqual(result['instances'], [1])
            self.assertEqual(result['listeners'][0]['address'], '192.0.2.1')
            self.assertEqual(self._request('status')['running_revision'], 'saved')
            result = self._request('stop')
            self.assertEqual(result['state'], 'stopped')
            self.assertEqual(result['instances'], [])

    def test_pending_reload_reports_changed_streaming_revision(self):
        self._serve()
        self._running(True)
        calls = []

        def reload(*, restart, streaming_revision):
            calls.append((restart, streaming_revision))
            return dict(saved_revision='edited', streaming_revision='edited-streaming',
                        sunshine_revision='sunshine',
                        restarting=False, restart_pending=True, message='Pending restart')

        self.backend.reloadHostConfiguration = reload
        self.streaming_revision = 'edited-streaming'
        result = self._request('reload')
        self.assertEqual(calls, [(False, 'streaming')])
        self.assertEqual(result['running_revision'], 'saved')
        self.assertTrue(result['restart_pending'])

    def test_different_install_is_rejected(self):
        self._serve()
        with patch.object(host_control, '_profile_identity', return_value=['different', 'profile']):
            with self.assertRaisesRegex(RuntimeError, 'different Monitorize installation'):
                self._request('status')

    def test_stopped_reload_persists_without_starting_a_controller(self):
        with patch('monitorize.config.host_config.reload_config', return_value={'saved_revision': 'new'}):
            result = host_control.request('reload')
        self.assertFalse(result['controller'])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['saved_revision'], 'new')

    def test_preset_launch_is_temporary(self):
        self._serve()
        calls = []

        def launch(name):
            calls.append(name)
            self._running(True)

        self.backend.startHostPreset = launch
        result = self._request('preset', preset='Tablet')
        self.assertEqual(calls, ['Tablet'])
        self.assertEqual(result['active_preset'], 'Tablet')
        self.assertIsNone(result['running_revision'])

    def test_disruptive_detection_fails_closed_for_mirror_and_remote(self):
        from monitorize.desktop.backend import MonitorizeBackend

        self.backend.session = SimpleNamespace(client_runtime=None)
        self.backend.streaming = SimpleNamespace(streaming=True, third_streaming=False)
        self.assertTrue(MonitorizeBackend._host_restart_is_disruptive(self.backend))

        slot = SimpleNamespace(status='waiting', owner='', request_id='')
        self.backend.session.client_runtime = SimpleNamespace(active=True, slots={1: slot})
        self.assertFalse(MonitorizeBackend._host_restart_is_disruptive(self.backend))
        slot.request_id = 'in-flight'
        self.assertTrue(MonitorizeBackend._host_restart_is_disruptive(self.backend))

        self.backend.remoteLoginEnabled = True
        self.backend._remote_login = SimpleNamespace(status={'state': 'desktop_listening'})
        self.assertFalse(MonitorizeBackend._host_restart_is_disruptive(self.backend))
        self.backend._remote_login.status = {'state': 'desktop_available', 'capture_ready': True}
        self.assertTrue(MonitorizeBackend._host_restart_is_disruptive(self.backend))

    def test_shared_desktop_lock_survives_another_claim_attempt(self):
        first = host_control.claim_desktop()
        self.assertIsNotNone(first)
        with first:
            self.assertIsNone(host_control.claim_desktop())
        with host_control.claim_desktop() as second:
            self.assertFalse(second.closed)
