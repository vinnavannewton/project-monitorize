"""Do not equate firewalld port presence with effective permission."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from monitorize.platform import firewall_policy as policy
from monitorize.platform.firewall_scope import PORTS
try:
    from .firewall_fixtures import ZONE_INFO, firewall_response
except ImportError:
    from firewall_fixtures import ZONE_INFO, firewall_response


class FirewalldPolicyTests(unittest.TestCase):
    def rich(self, rule, **kwargs):
        def service(name):
            return {'ports': '22/tcp' if name == 'ssh' else '5353/udp', 'protocols': ''}
        return policy.rich_rule_conflicts(rule, PORTS, service, **kwargs)

    def check(self, *, zone=ZONE_INFO, policies=None, nft=None, direct='', saved=None):
        policies = policies or {}
        commands = []
        def run(command):
            commands.append(command)
            for arg in command:
                if arg.startswith('--info-zone='):
                    return zone
                if arg.startswith('--info-policy='):
                    return policies[arg.split('=', 1)[1]]
            if '--get-policies' in command:
                return ' '.join(policies)
            if '--get-all-rules' in command:
                return direct
            if command[-1] == 'ruleset':
                return json.dumps({'nftables': nft or []})
            if command[0].endswith('tables-save'):
                return saved if saved is not None else '*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
            response = firewall_response(command)
            if response is not None:
                return response
            raise AssertionError('Unexpected/unsupported command: ' + str(command))
        return policy.verify_firewalld('firewall-cmd', ['home'], lambda *paths:paths[0], run, PORTS), commands

    def test_default_policy_uses_readonly_valid_queries_for_both_scopes(self):
        result, commands = self.check()
        self.assertIn('verified', result)
        self.assertTrue(any('--permanent' in command for command in commands))
        self.assertFalse(any(any(arg in command for arg in ('--get-priority', '--get-target', '--reload')) for command in commands))

    def test_fedora_connmark_compat_hooks_are_verified_without_ignoring_denies(self):
        saved = (Path(__file__).parent/'fixtures/firewall/fedora44-connmark-mangle.rules').read_text()
        hooks = [{'chain': {'family': 'ip', 'table': 'mangle', 'name': name, 'hook': hook}}
                 for name, hook in (('PREROUTING', 'prerouting'), ('OUTPUT', 'output'))]
        result, _ = self.check(saved=saved, nft=hooks)
        self.assertIn('verified', result)
        blocked = saved.replace('COMMIT', '-A PREROUTING -p tcp --dport 50084 -j DROP\nCOMMIT')
        with self.assertRaisesRegex(RuntimeError, 'mangle/PREROUTING'):
            self.check(saved=blocked, nft=hooks)

    def test_port_disappearing_during_verification_invalidates_success(self):
        with self.assertRaisesRegex(RuntimeError, 'ports changed'):
            self.check(zone=ZONE_INFO.replace('5353/udp', ''))

    def test_relevant_and_unrelated_zone_denies(self):
        self.assertTrue(self.rich('rule port port="50089" protocol="tcp" drop', zone=True))
        self.assertTrue(self.rich('rule port port="50098-50110" protocol="udp" reject', zone=True))
        self.assertFalse(self.rich('rule port port="22" protocol="tcp" reject', zone=True))
        self.assertFalse(self.rich('rule service name="ssh" drop', zone=True))
        self.assertTrue(self.rich('rule service name="mdns" drop', zone=True))

    def test_zone_rule_priority_preserves_first_match_order(self):
        self.assertTrue(self.rich('rule priority="-10" port port="50089" protocol="tcp" drop', zone=True))
        self.assertFalse(self.rich('rule priority="10" port port="50089" protocol="tcp" drop', zone=True))
        self.assertTrue(self.rich('rule priority="10" port port="50089" protocol="tcp" drop'))
        self.assertTrue(self.rich('rule priority="10" forward-port port="50089" protocol="tcp" to-port="22"', zone=True))
        self.assertTrue(self.rich('rule priority="10" protocol value="ipv6-icmp" drop', zone=True))

    def test_multiline_forward_ports_do_not_disappear_from_metadata(self):
        zone = ZONE_INFO.replace('  forward-ports:', '  forward-ports:\n    port=50089:proto=tcp:toport=22:toaddr=192.0.2.1')
        with self.assertRaisesRegex(RuntimeError, 'port forwarding'):
            self.check(zone=zone)

    def test_source_only_and_peer_port_rules_are_not_ignored(self):
        self.assertTrue(self.rich('rule family="ipv4" source address="192.0.2.0/24" drop', zone=True))
        self.assertTrue(self.rich('rule source-port port="22" protocol="tcp" drop', zone=True))
        self.assertTrue(self.rich('rule port port="22" protocol="tcp" drop', direction='output'))

    def test_icmp_types_do_not_all_block_streaming_but_neighbor_discovery_matters(self):
        with patch.object(policy, 'ipv6_enabled', return_value=True):
            self.assertFalse(self.rich('rule family="ipv6" icmp-type name="echo-request" drop'))
            self.assertTrue(self.rich('rule family="ipv6" icmp-type name="neighbour-solicitation" drop'))
            self.assertTrue(self.rich('rule protocol value="ipv6-icmp" drop'))

    def test_rich_rule_conflict_prevents_success(self):
        with self.assertRaisesRegex(RuntimeError, '50089'):
            self.check(zone=ZONE_INFO+'    rule port port="50089" protocol="tcp" drop\n')

    def test_host_policy_and_forward_only_policy_have_different_scope(self):
        info = 'test\n  priority: -100\n  target: DROP\n  ingress-zones: home\n  egress-zones: HOST\n  rich rules:\n'
        with self.assertRaisesRegex(RuntimeError, 'host policy'):
            self.check(policies={'test':info})
        self.check(policies={'test':info.replace('egress-zones: HOST', 'egress-zones: work')})
        with self.assertRaisesRegex(RuntimeError, 'host policy'):
            self.check(policies={'test':info.replace('priority: -100', 'priority: 100')})
        late = info.replace('priority: -100', 'priority: 100').replace('target: DROP', 'target: CONTINUE')
        self.check(policies={'test':late+'    rule port port="50089" protocol="tcp" drop\n'})

    def test_host_output_policy_cannot_hide_behind_zone_input_permissions(self):
        info = 'test\n  priority: 100\n  target: DROP\n  ingress-zones: HOST\n  egress-zones: ANY\n  rich rules:\n'
        with self.assertRaisesRegex(RuntimeError, 'host policy'):
            self.check(policies={'test':info})

    def test_direct_and_external_native_hooks_are_ambiguous(self):
        with self.assertRaisesRegex(RuntimeError, 'direct rules'):
            self.check(direct='ipv4 filter INPUT 0 -p tcp --dport 50089 -j DROP')
        with self.assertRaisesRegex(RuntimeError, 'Coexisting nftables'):
            self.check(nft=[{'chain':{'family':'inet','table':'custom','name':'incoming','hook':'input'}}])
        self.check(nft=[{'chain':{'family':'ip','table':'filter','name':'INPUT','hook':'input'}}])

    def test_unreadable_or_incomplete_metadata_cannot_prove_access(self):
        with self.assertRaises(ValueError):
            self.check(zone='running')
        with self.assertRaises(ValueError):
            self.check(policies={'unknown':'unknown\n  target: CONTINUE\n'})


if __name__ == '__main__':
    unittest.main()
