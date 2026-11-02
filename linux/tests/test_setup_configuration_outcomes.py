"""Configuration failures block setup; extra policy diagnostics do not."""
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationOutcomes(unittest.TestCase):
    def check(self, query='yes'):
        function = runpy.run_path(str(ROOT/'packaging/common/monitorize-system-setup'))['enable_firewall']
        commands = []
        scope = SimpleNamespace(snapshot=lambda *a: {'running': True},
                                choose_zones=lambda *a: ['home'], evidence=lambda *a: {},
                                PORTS=('50084/tcp',))
        def diagnose(*args):
            raise RuntimeError('IPv4 filter/INPUT: Tailscale source restriction needs a client test')
        def run(command):
            commands.append(command)
            return query if any(a.startswith('--query-port=') for a in command) else ''
        evidence = {}
        with patch.dict(function.__globals__, firewall_scope_module=lambda: scope,
                        firewall_policy_module=lambda: SimpleNamespace(verify_firewalld=diagnose),
                        command_path=lambda *paths: paths[0] if 'firewall-cmd' in paths[0] else None,
                        run=run):
            message = function(['home'], evidence=evidence)
        return message, evidence, commands

    def test_policy_diagnostic_is_saved_without_claiming_connectivity(self):
        message, evidence, commands = self.check()
        self.assertIn('Connection verification needs a Moonlight test', message)
        self.assertIn('Tailscale', evidence['policy_diagnostic'])
        self.assertFalse(evidence['connectivity_verified'])
        self.assertEqual(sum(any(a.startswith('--add-port=') for a in c) for c in commands), 2)
        self.assertFalse(any('--reload' in c for c in commands))

    def test_failed_native_readback_still_fails_setup(self):
        with self.assertRaisesRegex(RuntimeError, 'Could not verify Monitorize port'):
            self.check(query='no')
