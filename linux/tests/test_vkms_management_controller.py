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

    def test_reinstall_prepares_without_fetch_then_uses_install_coordinator(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('reinstall')
        with patch('monitorize.desktop.vkms_management.platform.prepare_update', return_value=dict(checkout='/fixture', revision='a' * 40)) as prepare:
            future, function = self.backend.futures[0]
            future.set_result(function())
        self.assertTrue(prepare.call_args.kwargs['reinstall'])
        self.backend.coordinator.begin.assert_called_once_with('vkms_reinstall')

    def test_immediate_completion_cannot_leave_card_stuck_busy(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        def immediate(operation):
            self.backend.recoveryOperationFinished.emit(dict(operation=operation, request_id='request',
                success=False, message='Update installed helpers first.'))
            return dict(pending=True, request_id='request')
        self.backend.coordinator.begin.side_effect = immediate
        c.start('reinstall')
        self.backend.futures[0][0].set_result(dict(checkout='/fixture', revision='a' * 40))
        self.assertFalse(c.busy)
        self.assertTrue(c.checking)

    def test_navigation_does_not_own_or_cancel_install(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.start('reinstall')
        self.assertTrue(c.snapshot()['busy'])
        c.refresh()
        self.assertEqual(len(self.backend.futures), 1)
        c.append_log('x' * 20000)
        self.assertLessEqual(len(c.snapshot()['logs']), 16384)

    def test_reinstall_runs_installer_and_reports_reinstall_result(self):
        c = self.controller
        c.operation = 'reinstall'
        c.prepared = dict(checkout='/fixture', revision='a' * 40)
        with patch('monitorize.desktop.vkms_management.platform.install_update',
                   return_value=dict(success=True, reboot_required=True, message='VKMS updated.')) as install:
            result = c.install()
        install.assert_called_once()
        self.assertEqual(install.call_args.args[0], c.prepared)
        self.assertEqual(result['message'], 'VKMS reinstalled. System restart required.')

    def test_up_to_date_requires_success_and_working_verification_then_expires(self):
        c = self.controller
        c.operation = 'update'
        c.prepared = dict(up_to_date=True)
        c.finish(dict(success=True))
        self.assertFalse(c.up_to_date)
        self.backend.futures[-1][0].set_result(dict(state='working', installed=True, message='Verified — working'))
        self.assertTrue(c.snapshot()['up_to_date'])
        self.assertTrue(c.notice_timer.isActive())
        self.assertEqual(c.notice_timer.interval(), 10000)
        c.notice_timer.timeout.emit()
        self.assertFalse(c.snapshot()['up_to_date'])
        self.assertEqual(c.snapshot()['message'], 'Verified — working')

    def test_no_up_to_date_for_failure_new_source_reinstall_or_reboot(self):
        for operation, unchanged, success, reboot, state in (
            ('update', True, False, False, 'working'),
            ('update', False, True, False, 'working'),
            ('reinstall', True, True, False, 'working'),
            ('update', True, True, True, 'restart_required'),
            ('update', True, True, False, 'needs_attention'),
            ('update', True, True, False, 'could_not_verify')):
            c = VkmsManagement(self.backend)
            c.operation = operation
            c.prepared = dict(up_to_date=unchanged)
            c.finish(dict(success=success, reboot_required=reboot))
            self.backend.futures[-1][0].set_result(dict(state=state, installed=True))
            self.assertFalse(c.up_to_date)
            self.assertFalse(c.notice_timer.isActive())

    def test_new_action_clears_previous_notice(self):
        c = self.controller
        c.health = dict(state='working', installed=True)
        c.up_to_date = True
        c.notice_timer.start()
        c.start('update')
        self.assertFalse(c.up_to_date)
        self.assertFalse(c.notice_timer.isActive())
