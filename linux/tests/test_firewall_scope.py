"""Network consent, stale authorization, and read-only drift detection."""
import subprocess
import unittest
from unittest.mock import patch

from monitorize.platform import firewall_scope as scope
from monitorize.platform import system_setup


class FirewallScopeTests(unittest.TestCase):
    def test_inactive_daemon_can_finish_its_ten_second_connection_retry(self):
        def run(command, **kwargs):
            if kwargs['timeout'] <= 10.42:
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return subprocess.CompletedProcess(command, 252, '',
                'Waiting on dbus connection...\nnot running\n')
        with patch.object(scope.subprocess, 'run', side_effect=run) as process:
            current = scope.snapshot('/usr/bin/firewall-cmd')
        self.assertFalse(current['running'])
        self.assertEqual(current['zones'], [])
        self.assertEqual(process.call_count, 1)

    def test_longer_state_deadline_does_not_hide_probe_failures(self):
        command = ['/usr/bin/firewall-cmd', '--state']
        with patch.object(scope.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired(command, 15)):
            with self.assertRaises(subprocess.TimeoutExpired):
                scope.snapshot(command[0])
        with patch.object(scope.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(command, 1, '', 'Access denied')):
            with self.assertRaisesRegex(RuntimeError, 'Access denied'):
                scope.snapshot(command[0])

    def test_other_firewall_queries_keep_the_short_deadline(self):
        command = ['/usr/bin/firewall-cmd', '--get-active-zones']
        with patch.object(scope.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(command, 0, '', '')) as process:
            scope.execute(command)
        self.assertEqual(process.call_args.kwargs['timeout'], 5)

    def test_client_sends_explicit_scope_as_separate_arguments(self):
        with patch.object(system_setup, 'setup_helper_command', side_effect=lambda args:(args,None)):
            command, error = system_setup.system_setup_command(False, True, request_id='a' * 32,
                firewall_zones=['home'], firewall_context='b' * 64)
            self.assertIsNone(error)
            self.assertIn('--firewall-zone', command)
            self.assertEqual(command[command.index('--firewall-zone')+1], 'home')
            self.assertEqual(command[command.index('--firewall-context')+1], 'b' * 64)
            for zones in (['home; touch /tmp/unsafe'], ['--trusted']):
                command, error = system_setup.system_setup_command(False, True, firewall_zones=zones)
                self.assertIsNone(command)
                self.assertFalse(error['success'])

    def inspect(self, active='home\n  interfaces: wlan0\ndocker\n  interfaces: docker0\n'):
        def run(command):
            text = active if '--get-active-zones' in command else 'public' if '--get-default-zone' in command else 'running'
            return subprocess.CompletedProcess(command, 0, text, '')
        return scope.snapshot('firewall-cmd', run)

    def test_multiple_networks_need_explicit_consent(self):
        current = self.inspect()
        with self.assertRaisesRegex(RuntimeError, 'Choose'):
            scope.choose_zones(current)
        self.assertEqual(scope.choose_zones(current, ['home']), ['home'])
        with self.assertRaises(ValueError):
            scope.choose_zones(current, [])
        with self.assertRaises(ValueError):
            scope.choose_zones(current, ['unseen'])
        single = self.inspect('home\n  interfaces: wlan0\n')
        with self.assertRaisesRegex(RuntimeError, 'Choose'):
            scope.choose_zones(single, None, single['fingerprint'])

    def test_authorization_rejects_network_change_even_with_same_zone_name(self):
        before = self.inspect('home\n  interfaces: wlan0\n')
        after = self.inspect('home\n  interfaces: enp1s0\n')
        with self.assertRaisesRegex(RuntimeError, 'changed during authorization'):
            scope.choose_zones(after, ['home'], before['fingerprint'])

    def test_no_active_interfaces_uses_the_default_zone(self):
        current = self.inspect('')
        self.assertEqual(scope.choose_zones(current), ['public'])
        self.assertTrue(current['zones'][0]['default'])

    def test_new_zone_prompts_but_previously_declined_zone_does_not(self):
        current = self.inspect()
        saved = scope.evidence(current, ['home'])
        calls = []
        def run(command):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, ' '.join(scope.PORTS), '')
        self.assertTrue(scope.assess(current, saved, 'firewall-cmd', run)['scope_current'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('--zone=home' in command and '--list-ports' in command for command in calls))
        changed = self.inspect('home\n  interfaces: wlan0\ndocker\n  interfaces: docker0\nwork\n  interfaces: eth0\n')
        self.assertTrue(scope.assess(changed, saved)['needs_attention'])
        self.assertIn('work', scope.assess(changed, saved)['message'])

    def test_missing_runtime_or_persistent_ports_invalidates_success(self):
        current = self.inspect('home\n  interfaces: wlan0\n')
        saved = scope.evidence(current, ['home'])
        for missing in (False, True):
            def run(command):
                text = '' if ('--permanent' in command) == missing else ' '.join(scope.PORTS)
                return subprocess.CompletedProcess(command, 0, text, '')
            self.assertFalse(scope.assess(current, saved, 'firewall-cmd', run)['scope_current'])

    def test_merged_ranges_count_as_coverage_and_mdns_is_required(self):
        self.assertTrue(scope.covers_ports(['50000-52000/tcp', '50000-52000/udp', '5353/udp']))
        self.assertFalse(scope.covers_ports([port for port in scope.PORTS if port != '5353/udp']))

    def test_manager_change_and_old_receipts_require_review(self):
        current = self.inspect()
        self.assertTrue(scope.assess(current, {})['needs_attention'])
        saved = scope.evidence(current, ['home'])
        self.assertTrue(scope.assess(scope.snapshot(None), saved)['needs_attention'])
        self.assertFalse(scope.assess(current, None)['needs_attention'])

    def test_discovery_failure_is_not_readiness(self):
        with patch.object(scope, 'snapshot', side_effect=RuntimeError('Access denied')):
            result = scope.discover()
        self.assertFalse(result['available'])
        self.assertFalse(result['scope_current'])
        self.assertTrue(result['needs_attention'])

    def test_latest_receipt_overrides_historical_success(self):
        saved = scope.evidence(self.inspect(), ['home'])
        receipt = dict(history={'firewall': dict(state='succeeded', evidence=saved)}, actions={})
        with patch('monitorize.platform.setup_state.read_receipt', return_value=receipt):
            self.assertEqual(scope.saved_scope(), saved)
            receipt['actions']['firewall'] = dict(state='failed')
            self.assertEqual(scope.saved_scope(), {})
            receipt['actions']['firewall'] = dict(state='started')
            self.assertEqual(scope.saved_scope(), {})


if __name__ == '__main__':
    unittest.main()
