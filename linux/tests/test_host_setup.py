import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from monitorize.platform import host_setup, system_setup


ROOT = Path(__file__).resolve().parents[2]


class HostSetupTest(unittest.TestCase):
    def setUp(self):
        host = patch.object(host_setup, '_host_only', return_value=None)
        host.start()
        self.addCleanup(host.stop)

    def test_setup_scopes_firewall_to_the_selected_network_snapshot(self):
        zones = [{'name': 'home'}, {'name': 'public'}]
        scope = {'available': True, 'running': True, 'zones': zones, 'fingerprint': 'f' * 64}
        helper = str(system_setup.HELPER)
        receipt = {'success': True, 'actions': {'firewall': {'success': True}}}
        with patch.object(host_setup.firewall_scope, 'discover', return_value=scope), \
             patch.object(system_setup, 'setup_helper_command', side_effect=lambda arguments: (
                 ['/usr/bin/pkexec', '--disable-internal-agent', helper, *arguments], None
             )), patch.object(host_setup, '_run_installed', return_value=receipt) as run, \
             patch('monitorize.config.settings.save_general_settings') as save:
            missing = host_setup.apply_setup(firewall=True)
            self.assertFalse(missing['success'])
            run.assert_not_called()
            self.assertEqual(host_setup.apply_setup(firewall=True, firewall_zones=['home']), receipt)
            command = run.call_args.args[1]
            self.assertEqual(command[:5], ['--firewall', '--firewall-zone', 'home', '--firewall-context', 'f' * 64])
            self.assertEqual(command[-1], run.call_args.kwargs['request_id'])
            save.assert_called_once_with(system_setup_decided=True)

    def test_terminal_runner_preserves_setup_receipts_and_enables_the_text_agent(self):
        request = 'a' * 32
        receipt = {'protocol': 2, 'request_id': request, 'success': True}
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), '')
        with patch.object(host_setup, '_read'), patch.object(host_setup.os, 'access', return_value=True), \
             patch.object(host_setup.subprocess, 'run', return_value=completed) as run:
            response = host_setup._run_installed(system_setup.HELPER, ['--input', '--request-id', request],
                                                 request_id=request)
        self.assertEqual(response, receipt)
        self.assertEqual(run.call_args.args[0], [
            '/usr/bin/pkexec', str(system_setup.HELPER), '--input', '--request-id', request,
        ])

    def test_terminal_runner_requires_a_trusted_terminal_capability_for_boot(self):
        with patch.object(host_setup, '_read'), patch.object(host_setup.os, 'access', return_value=True), \
             patch.object(host_setup, '_terminal_protocol', return_value=False), \
             patch.object(host_setup.subprocess, 'run') as run:
            result = host_setup._boot_request('disable')
        self.assertFalse(result['success'])
        self.assertIn('terminal setup support', result['message'])
        run.assert_not_called()

    def test_setup_never_executes_a_checkout_bootstrap(self):
        fallback = ['/usr/bin/pkexec', '/usr/bin/python3', '-I', '/tmp/source-maintenance.py', 'bootstrap']
        with patch.object(system_setup, 'system_setup_command', return_value=(fallback, None)), \
             patch.object(host_setup, '_run_installed') as run:
            result = host_setup.apply_setup(input_access=True)
        self.assertFalse(result['success'])
        run.assert_not_called()

    def test_boot_requires_a_matching_acknowledgement(self):
        with patch.object(host_setup, '_run_installed', return_value={'success': True, 'action': 'enable'}) as run:
            result = host_setup._boot_request('disable')
        run.assert_called_once_with(system_setup.HELPER, ['--remote-login', 'disable'], terminal=True)
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])

    def test_failed_enrollment_restores_the_desktop_controller(self):
        readiness = {'available': True, 'supported': True, 'enabled': False, 'live_available': True}
        snapshot = object()
        with patch.object(system_setup, 'remote_login_setup_status', return_value=readiness), \
             patch.object(host_setup, '_terminal_protocol', return_value=True), \
             patch.object(host_setup, '_has_graphical_session', return_value=True), \
             patch.object(host_setup, '_prepare_boot_controller', return_value=snapshot), \
             patch.object(host_setup, '_boot_request', return_value={'success': False, 'cancelled': True}) as request, \
             patch('monitorize.desktop.remote_login_controller.restore_controller_snapshot') as restore:
            result = host_setup.apply_boot('enable')
        self.assertTrue(result['cancelled'])
        request.assert_called_once_with('enable-live', None)
        restore.assert_called_once_with(snapshot)

    def test_enrolls_for_next_boot_without_a_graphical_session(self):
        readiness = {'available': True, 'supported': True, 'enabled': False, 'live_available': False}
        result = {'success': True, 'reboot_required': True}
        with patch.object(system_setup, 'remote_login_setup_status', return_value=readiness), \
             patch.object(host_setup, '_terminal_protocol', return_value=True), \
             patch.object(host_setup, '_has_graphical_session', return_value=False), \
             patch.object(host_setup, '_prepare_boot_controller'), \
             patch.object(host_setup, '_boot_request', return_value=result) as request:
            response = host_setup.apply_boot('enable')
        request.assert_called_once_with('enable', None)
        self.assertTrue(response['configured'])
        self.assertTrue(response['reboot_required'])

    def test_terminal_authorization_uses_a_distinct_installed_path(self):
        completed = subprocess.CompletedProcess([], 0, '{"success":true}', '')
        with patch.object(host_setup, '_read'), patch.object(host_setup.os, 'access', return_value=True), \
             patch.object(host_setup, '_terminal_protocol', return_value=True), \
             patch.object(host_setup.subprocess, 'run', return_value=completed) as run:
            result = host_setup._run_installed(system_setup.HELPER, ['--remote-login', 'disable'], terminal=True)
        self.assertTrue(result['success'])
        self.assertEqual(run.call_args.args[0], [
            '/usr/bin/pkexec', str(host_setup._TERMINAL_ENTRY), '--terminal', '--remote-login', 'disable',
        ])

    def test_successful_boot_timeout_updates_desktop_saved_timeout(self):
        with patch.object(host_setup, '_terminal_protocol', return_value=True), \
             patch.object(host_setup, '_boot_request', return_value={'success': True}) as request, \
             patch('monitorize.config.settings.save_display_idle_timeout') as save:
            self.assertTrue(host_setup.apply_boot('timeout', seconds=30)['success'])
        request.assert_called_once_with('timeout', 30)
        save.assert_called_once_with(30)

    def test_terminal_policy_is_explicit_and_requires_admin_authentication(self):
        policy = ElementTree.parse(ROOT / 'packaging/common' / host_setup._POLICY.name)
        actions = {action.attrib['id'].split('.')[-1]: action for action in policy.findall('action')}
        terminal = actions['terminal-setup']
        for name in ('allow_any', 'allow_inactive', 'allow_active'):
            self.assertEqual(terminal.findtext('defaults/' + name), 'auth_admin')
        self.assertEqual(terminal.findtext("annotate[@key='org.freedesktop.policykit.exec.argv1']"), '--terminal')
        self.assertEqual(terminal.findtext("annotate[@key='org.freedesktop.policykit.exec.path']"),
                         str(host_setup._TERMINAL_ENTRY))
        self.assertEqual(actions['remote-login-setup'].findtext('defaults/allow_any'), 'no')


if __name__ == '__main__':
    unittest.main()
