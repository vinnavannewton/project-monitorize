"""Backend-owned maintenance state and stale-result behavior without services."""
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from monitorize.desktop.vkms_management import VkmsManagement

APP = QApplication.instance() or QApplication([])


class Backend(QObject):
    recoveryOperationFinished = pyqtSignal(object)
    def __init__(self):
        super().__init__()
        self._closing = False
        self.applyingConfiguration = False
        self.systemSetupRunning = False
        self.remoteLoginSetupBusy = False
        self.futures = []
        self._background_executor = Mock()
        self._background_executor.submit.side_effect = self.submit
        self.coordinator = Mock()
        self.coordinator.begin.return_value = dict(pending=True, request_id='request')
    def submit(self, function):
        future = Future()
        self.futures.append((future, function))
        return future
    def _recovery_operations(self):
        return self.coordinator


class VkmsControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.controller = VkmsManagement(self.backend)

    def test_repeated_activation_deduplicates_then_rechecks(self):
        c = self.controller
        c.refresh();c.refresh()
        self.assertTrue(c.checking)
        self.assertEqual(len(self.backend.futures), 1)
        self.backend.futures[0][0].set_result(dict(state='working', installed=True))
        self.assertFalse(c.checking)
        c.refresh()
        self.assertEqual(len(self.backend.futures), 2)

    def test_stale_result_cannot_replace_new_state(self):
        c = self.controller
        c.refresh()
        c.complete('verify', c.serial - 1, dict(state='not_installed', installed=False))
        self.assertTrue(c.checking)
        self.assertIsNone(c.health['installed'])

    def test_missing_installation_never_starts_maintenance(self):
        self.controller.health = dict(state='not_installed', installed=False)
        self.controller.start('update')
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.backend.futures)

    def test_fetch_failure_never_requests_session_stop_or_authorization(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('update')
        self.backend.futures[0][0].set_result(dict(success=False, message='Offline'))
        self.backend.coordinator.begin.assert_not_called()
        self.assertFalse(c.busy)
        self.assertEqual(c.message, 'Offline')
        self.assertTrue(c.checking)

    def test_prepare_then_coordinator_and_matching_completion(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('update')
        self.backend.futures[0][0].set_result(dict(checkout='/fixture', revision='a' * 40))
        self.backend.coordinator.begin.assert_called_once_with('vkms_update')
        self.backend.recoveryOperationFinished.emit(dict(operation='vkms_update', request_id='stale', success=True))
        self.assertTrue(c.busy)
        self.backend.recoveryOperationFinished.emit(dict(operation='vkms_update', request_id='request', cancelled=True,
                                                       success=False, message='Cancelled'))
        self.assertFalse(c.busy)
        self.assertTrue(c.checking)

    def test_restart_never_fetches_source(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('restart')
        self.backend.coordinator.begin.assert_called_once_with('vkms_restart')
        self.assertFalse(self.backend.futures)

    def test_immediate_completion_cannot_leave_card_stuck_busy(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        def immediate(operation):
            self.backend.recoveryOperationFinished.emit(dict(operation=operation, request_id='request',
                success=False, message='Update installed helpers first.'))
            return dict(pending=True, request_id='request')
        self.backend.coordinator.begin.side_effect = immediate
        c.start('restart')
        self.assertFalse(c.busy)
        self.assertTrue(c.checking)

    def test_navigation_does_not_own_or_cancel_install(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('restart')
        self.assertTrue(c.snapshot()['busy'])
        c.refresh()
        self.assertFalse(self.backend.futures)
        c.append_log('x' * 20000)
        self.assertLessEqual(len(c.snapshot()['logs']), 16384)
