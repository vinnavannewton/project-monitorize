"""Disable/resume entry orchestration; every privileged dependency is mocked."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from monitorize.remote_login import enrollment


class DisableSwitchTests(unittest.TestCase):
    def run_disable(self,failure=None):
        settings=SimpleNamespace(uid=1000,enabled=False)
        with patch.object(enrollment,'read_settings',return_value=settings), \
             patch.object(enrollment,'Commands'),patch.object(enrollment,'restore_linger'), \
             patch('monitorize.remote_login.package_lifecycle.InstalledBackend'), \
             patch('monitorize.remote_login.package_lifecycle.PackageLifecycle') as lifecycle, \
             patch('monitorize.remote_login.live_setup.SetupReceipt') as receipt:
            receipt.return_value.load.return_value=None
            lifecycle.return_value.disable_enrollment.side_effect=failure
            result=enrollment.disable(1000)
            lifecycle.return_value.disable_enrollment.assert_called_once_with(1000)
            lifecycle.return_value.resume.assert_not_called()
            return result

    def test_success_allows_normal_streaming_without_reboot(self):
        result=self.run_disable()
        self.assertTrue(result['success']);self.assertFalse(result['reboot_required'])
        self.assertIs(result['configured'],False)

    def test_release_failure_reports_partial_state_and_retry(self):
        result=self.run_disable(RuntimeError('still busy'))
        self.assertFalse(result['success']);self.assertTrue(result['cleanup_pending'])
        self.assertIn('still busy',result['message']);self.assertIs(result['configured'],False)

    def test_maintenance_recovery_cannot_resume_another_account(self):
        with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000,enabled=True)), \
             patch('monitorize.remote_login.package_lifecycle.InstalledBackend'), \
             patch('monitorize.remote_login.package_lifecycle.PackageLifecycle') as lifecycle:
            lifecycle.return_value.load.return_value={'uid':2000}
            with self.assertRaises(PermissionError):enrollment.recover_maintenance(1000)
            lifecycle.return_value.resume.assert_not_called()

    def test_successful_disable_retires_prior_live_setup_continuation(self):
        with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000,enabled=False)), \
             patch.object(enrollment,'Commands'),patch.object(enrollment,'restore_linger'), \
             patch('monitorize.remote_login.package_lifecycle.InstalledBackend'), \
             patch('monitorize.remote_login.package_lifecycle.PackageLifecycle'), \
             patch('monitorize.remote_login.live_setup.SetupReceipt') as receipt:
            prior={'stage':'waiting_for_controller'}
            receipt.return_value.load.return_value=prior
            result=enrollment.disable(1000)
            self.assertTrue(result['success'])
            receipt.return_value.save.assert_called_once_with({'stage':'rolled_back'})


class HostRecoveryEnrollmentTests(unittest.TestCase):
    def exercise(self,response,receipt=None):
        import json
        with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000,enabled=True)), \
             patch('monitorize.remote_login.package_lifecycle.InstalledBackend'), \
             patch('monitorize.remote_login.package_lifecycle.PackageLifecycle') as factory, \
             patch.object(enrollment.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response))) as run:
            lifecycle=factory.return_value;lifecycle.load.return_value=receipt
            result=enrollment.recover_host(1000)
            return result,lifecycle,run

    def test_enrolled_host_pauses_and_releases_before_helper_and_resumes_only_after_success(self):
        result,lifecycle,run=self.exercise(dict(success=True))
        self.assertTrue(result['success'])
        lifecycle.prepare.assert_called_once_with('upgrade',include_idle_services=True)
        lifecycle.resume.assert_called_once();self.assertEqual(run.call_args.args[0][-1],'recover')

    def test_failed_host_recovery_retains_maintenance_for_retry(self):
        result,lifecycle,run=self.exercise(dict(success=False,message='module missing'))
        self.assertTrue(result['cleanup_pending']);lifecycle.resume.assert_not_called()

    def test_retry_uses_existing_gate_without_new_release_against_blocked_host(self):
        receipt=dict(uid=1000,operation='upgrade',stage='prepared',boot_id='same',masks=['owned'])
        import json
        with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000)), \
             patch('monitorize.remote_login.package_lifecycle.InstalledBackend'), \
             patch('monitorize.remote_login.package_lifecycle.PackageLifecycle') as factory, \
             patch.object(enrollment.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(dict(success=True)))):
            lifecycle=factory.return_value;lifecycle.load.return_value=receipt;lifecycle.backend.boot_id='same'
            self.assertTrue(enrollment.recover_host(1000)['success'])
            lifecycle.prepare.assert_not_called()
            lifecycle.backend.pause.assert_called_once_with(1000,['owned'],allow_disconnect=False)
            lifecycle.resume.assert_called_once()
