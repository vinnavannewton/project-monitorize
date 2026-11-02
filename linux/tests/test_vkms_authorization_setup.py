import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from monitorize.platform import system_setup


class VkmsAuthorizationSetupTest(unittest.TestCase):
    def test_failed_setup_reports_host_error_without_treating_it_as_success(self):
        result = system_setup.parse_system_setup_result(1, '{"success":false,"message":"Insecure grant directory"}', '')
        self.assertFalse(result['success'])
        self.assertEqual(result['message'], 'Insecure grant directory')

    def test_enable_and_revoke_use_fixed_scoped_host_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'authorization'
            path.touch()
            with patch.object(system_setup, 'VKMS_AUTHORIZATION_HELPER', path), patch.object(system_setup.shutil, 'which', return_value='/usr/bin/pkexec'):
                for enabled, operation in ((True, 'enable'), (False, 'disable')):
                    command, error = system_setup.vkms_authorization_command(enabled)
                    self.assertIsNone(error)
                    self.assertEqual(command, ['/usr/bin/pkexec', str(path), operation])

    def test_missing_host_never_offers_generic_privileged_command(self):
        with patch.object(system_setup.Path, 'is_file', return_value=False):
            command, error = system_setup.vkms_authorization_command(True)
        self.assertIsNone(command)
        self.assertFalse(error['success'])

    def test_combined_setup_authorizes_all_actions_in_one_invocation(self):
        with patch.object(system_setup, 'get_system_setup_status', return_value={'available': True}), patch.object(system_setup.shutil, 'which', return_value='/usr/bin/pkexec'):
            command, error = system_setup.system_setup_command(True, True, True, request_id='a' * 32)
        self.assertIsNone(error)
        self.assertEqual(command.count('/usr/bin/pkexec'), 1)
        self.assertEqual(command[-5:], ['--input', '--firewall', '--unattended-vkms', '--request-id', 'a' * 32])
