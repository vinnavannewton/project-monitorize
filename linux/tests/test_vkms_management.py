"""VKMS Settings adapter tests never fetch the network or run privileged commands."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from monitorize.platform import vkms_management as m


class VkmsManagementTests(unittest.TestCase):
    def completed(self, code=0, value=None, stderr=''):
        return subprocess.CompletedProcess([], code, json.dumps(value or {}), stderr)

    def test_new_verifier_and_installed_version_are_used_outside_app_venv(self):
        value = dict(schema_version=1, state='working', installed=True, checks=[])
        with patch.object(m.shutil, 'which', return_value='/usr/bin/monitorize-vkms'), \
             patch.object(m.subprocess, 'run', return_value=self.completed(value=value)) as run, \
             patch.object(m, 'VERSION', Mock(is_file=Mock(return_value=True), read_text=Mock(return_value='1.0\n'))), \
             patch.object(m, 'REBOOT_RECORD', Mock(is_file=Mock(return_value=False))):
            result = m.verify()
        self.assertEqual(result['state'], 'working')
        self.assertEqual(result['version'], '1.0')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/monitorize-vkms', 'verify', '--json'])

    def test_old_cli_uses_read_only_json_diagnostics(self):
        legacy = dict(success=True, all_passed=True, checks=[])
        pretty = subprocess.CompletedProcess([], 0, json.dumps(legacy, indent=2), '')
        with patch.object(m.shutil, 'which', return_value='/usr/bin/monitorize-vkms'), \
             patch.object(m.subprocess, 'run', side_effect=[self.completed(2, stderr="invalid choice: 'verify'"), pretty, subprocess.CompletedProcess([], 0, 'active', ''), subprocess.CompletedProcess([], 0, m.os.uname().release, '')]) as run, \
             patch.object(m, 'VERSION', Mock(is_file=Mock(return_value=False))), \
             patch.object(m, 'REBOOT_RECORD', Mock(is_file=Mock(return_value=False))):
            self.assertEqual(m.verify()['state'], 'working')
        self.assertEqual(run.call_args_list[1].args[0], ['/usr/bin/monitorize-vkms', 'doctor', '--json'])

    def test_timeout_and_malformed_response_are_not_absence(self):
        for effect in (subprocess.TimeoutExpired('verify', 20), self.completed(value={'state': 'working'})):
            with self.subTest(effect=effect), patch.object(m.shutil, 'which', return_value='/usr/bin/monitorize-vkms'), \
                 patch.object(m.subprocess, 'run', side_effect=effect if isinstance(effect, Exception) else None,
                              return_value=effect if not isinstance(effect, Exception) else None):
                value = m.verify()
                self.assertTrue(value['installed'])
                self.assertEqual(value['state'], 'could_not_verify')

    def test_update_preparation_does_not_install_and_binds_exact_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'monitorize_vkms').mkdir()
            (root / 'monitorize_vkms/managed_install.py').write_text('fixture')
            responses = [str(root), next(iter(m.OFFICIAL_REMOTES)), 'main', '', 'b' * 40, '', '0', '', 'a' * 40]
            with patch.object(m, 'source_checkout', return_value=root), patch.object(m, 'git', side_effect=responses) as git, \
                 patch.object(m.os, 'geteuid', return_value=1000), patch.object(m.subprocess, 'Popen') as process:
                value = m.prepare_update()
                process.assert_not_called()
            self.assertEqual(value['revision'], 'a' * 40)
            self.assertFalse(value['up_to_date'])
            self.assertIn(('fetch', 'origin', 'main'), [c.args[1:] for c in git.call_args_list])

    def test_dirty_source_is_preserved_without_fetch(self):
        root = Path('/fixture')
        responses = [str(root), next(iter(m.OFFICIAL_REMOTES)), 'main', ' M install.sh']
        with patch.object(m, 'source_checkout', return_value=root), patch.object(m, 'git', side_effect=responses) as git, \
             patch.object(m.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'local changes'):
                m.prepare_update()
        self.assertFalse(any('fetch' in c.args for c in git.call_args_list))

    def test_install_has_no_timeout_and_reports_authorization_cancellation(self):
        process = Mock(stdout=iter(['authentication cancelled\n']))
        process.wait.return_value = 126
        with patch.object(m.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch.object(m.subprocess, 'Popen', return_value=process) as spawn:
            result = m.install_update(dict(checkout='/fixture with spaces', revision='a' * 40))
        self.assertTrue(result['cancelled'])
        self.assertNotIn('timeout', spawn.call_args.kwargs)
        self.assertIn('/fixture with spaces/monitorize_vkms/managed_install.py', spawn.call_args.args[0])

    def test_install_missing_result_is_unknown_and_not_replayed(self):
        process = Mock(stdout=iter(['installer interrupted\n']))
        process.wait.return_value = 1
        with patch.object(m.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch.object(m.subprocess, 'Popen', return_value=process) as spawn:
            result = m.install_update(dict(checkout='/fixture', revision='a' * 40))
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(spawn.call_count, 1)

    def test_started_installer_is_never_replayed_even_after_log_truncation(self):
        process = Mock(stdout=iter(['{"management_started": true}\n', 'x' * 70000,
                                   'No authentication agent found\n']))
        process.wait.return_value = 127
        with patch.object(m.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch.object(m.subprocess, 'Popen', return_value=process) as spawn, \
             patch('monitorize.platform.polkit_agent.start_agent') as agent:
            result = m.install_update(dict(checkout='/fixture', revision='a' * 40))
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(spawn.call_count, 1)
        agent.assert_not_called()

    def test_reinstall_uses_current_revision_without_network_or_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'monitorize_vkms').mkdir()
            (root / 'monitorize_vkms/managed_install.py').write_text('fixture')
            responses = [str(root), next(iter(m.OFFICIAL_REMOTES)), 'main', '', 'b' * 40]
            with patch.object(m, 'source_checkout', return_value=root), \
                 patch.object(m, 'git', side_effect=responses) as git, \
                 patch.object(m.os, 'geteuid', return_value=1000):
                value = m.prepare_update(reinstall=True)
            self.assertEqual(value['revision'], 'b' * 40)
            commands = [call.args[1] for call in git.call_args_list]
            self.assertNotIn('fetch', commands)
            self.assertNotIn('merge', commands)

    def test_update_marks_unchanged_remote_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'monitorize_vkms').mkdir()
            (root / 'monitorize_vkms/managed_install.py').write_text('fixture')
            responses = [str(root), next(iter(m.OFFICIAL_REMOTES)), 'main', '', 'a' * 40,
                         '', '0', '', 'a' * 40]
            with patch.object(m, 'source_checkout', return_value=root), \
                 patch.object(m, 'git', side_effect=responses), \
                 patch.object(m.os, 'geteuid', return_value=1000):
                self.assertTrue(m.prepare_update()['up_to_date'])
