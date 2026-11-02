"""Reachability must respect UFW order, custom chains and address families."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from monitorize.platform import firewall_policy as policy
from monitorize.platform.firewall_scope import PORTS


def rules(before='', user='-A ufw-user-input -p tcp --dport 50089 -j ACCEPT', after='', output='ACCEPT'):
    return f'''*filter
:INPUT DROP [0:0]
:OUTPUT {output} [0:0]
:ufw-before-input - [0:0]
:ufw-user-input - [0:0]
:ufw-after-input - [0:0]
-A INPUT -j ufw-before-input
-A INPUT -j ufw-user-input
-A INPUT -j ufw-after-input
-A ufw-before-input -i lo -j ACCEPT
-A ufw-before-input -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A ufw-before-input -m conntrack --ctstate INVALID -j DROP
{before}
{user}
{after}
-A ufw-user-input -p udp --dport 5353 -j ACCEPT
COMMIT
'''


class FirewallPolicyTests(unittest.TestCase):
    def test_reported_fedora_connmark_rules_do_not_block_setup(self):
        directory = Path(__file__).parent/'fixtures/firewall'
        mangle = (directory/'fedora44-connmark-mangle.rules').read_text()
        allow = '*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
        self.assertTrue(policy.verify_save(mangle + allow, 4, PORTS))
        # Restoring a mark must not end traversal before a real drop.
        blocked = mangle.replace('COMMIT', '-A PREROUTING -p tcp --dport 50084 -j DROP\nCOMMIT')
        with self.assertRaisesRegex(RuntimeError, 'mangle/PREROUTING.*50084/tcp'):
            policy.verify_save(blocked + allow, 4, PORTS)

    def marking_rules(self, following=''):
        return '''*mangle
:PREROUTING ACCEPT [0:0]
:marking - [0:0]
-A PREROUTING -m conntrack --ctstate RELATED,ESTABLISHED -j marking
-A marking -j CONNMARK --restore-mark --nfmask 0xffffffff --ctmask 0xffffffff
-A marking -j MARK --set-xmark 0x1/0xff
-A marking -j CONNMARK --save-mark --nfmask 0xff --ctmask 0xff
''' + following + '\nCOMMIT\n' + rules()

    def test_marking_targets_continue_without_denying_streaming(self):
        for family in (4, 6):
            with self.subTest(family=family):
                self.assertTrue(self.verify(self.marking_rules(), family))

    def test_marking_targets_do_not_hide_a_later_drop(self):
        for family in (4, 6):
            with self.subTest(family=family), self.assertRaisesRegex(RuntimeError, 'mangle/PREROUTING'):
                self.verify(self.marking_rules('-A marking -p tcp --dport 50089 -j DROP'), family)

    def test_mark_dependent_filtering_remains_unverified(self):
        text = self.marking_rules('-A marking -m mark --mark 0x1/0xff -j DROP')
        with self.assertRaisesRegex(RuntimeError, 'mangle/PREROUTING'):
            self.verify(text)

    def test_ipv6_interface_override_is_not_treated_as_kernel_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('all', 'default', 'eth0'):
                (root/name).mkdir()
                (root/name/'disable_ipv6').write_text('1\n')
            self.assertFalse(policy.ipv6_enabled(root=root))
            (root/'eth0/disable_ipv6').write_text('0\n')
            self.assertTrue(policy.ipv6_enabled(root=root))

    def test_kernel_disabled_ipv6_does_not_require_ipv6_rules(self):
        commands = []
        def execute(command):
            commands.append(command)
            return '{"nftables":[]}' if command[-1] == 'ruleset' else '*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
        with patch.object(policy, 'ipv6_enabled', return_value=False):
            message = policy.verify_ufw(lambda *paths:None if 'legacy' in paths[0] else paths[0], execute, PORTS)
        self.assertIn('IPv6 disabled', message)
        self.assertFalse(any('ip6tables' in command[0] for command in commands))

    def test_live_fedora_ufw_exports(self):
        directory = Path(__file__).parent/'fixtures/firewall'
        for family in (4, 6):
            text = (directory/f'ufw-fedora44-ipv{family}.rules').read_text()
            self.assertTrue(policy.verify_save(text, family, PORTS))
            # Actual before chains differ between families and include rt/hl,
            # addrtype, conntrack, logging, comments and multiport extensions.
            before = 'ufw-before-input' if family == 4 else 'ufw6-before-input'
            # iptables-save exports ordered -A entries, never -I commands.
            blocked = text.replace(f'-A {before} ', f'-A {before} -p tcp --dport 50089 -j DROP\n-A {before} ', 1)
            with self.assertRaisesRegex(RuntimeError, f'IPv{family}'):
                policy.verify_save(blocked, family, PORTS)

    def verify(self, text, family=4):
        return policy.verify_save(text, family, ['50089/tcp', '5353/udp'])

    def test_standard_ufw_graph_accepts_ipv4_and_ipv6(self):
        for family in (4, 6):
            self.assertTrue(self.verify(rules(), family))

    def test_earlier_deny_blocks_later_allow(self):
        with self.assertRaisesRegex(RuntimeError, '50089/tcp'):
            self.verify(rules(before='-A ufw-before-input -p tcp --dport 50089 -j DROP'))

    def test_unrelated_deny_is_not_a_conflict(self):
        self.assertTrue(self.verify(rules(before='-A ufw-before-input -p tcp --dport 22 -j REJECT')))

    def test_later_deny_does_not_override_earlier_accept(self):
        self.assertTrue(self.verify(rules(after='-A ufw-after-input -p tcp --dport 50089 -j DROP')))

    def test_specific_source_allow_does_not_prove_general_coverage(self):
        with self.assertRaises(RuntimeError):
            self.verify(rules(user='-A ufw-user-input -s 192.0.2.0/24 -p tcp --dport 50089 -j ACCEPT'))

    def test_restricted_interface_deny_can_block_a_client(self):
        with self.assertRaises(RuntimeError):
            self.verify(rules(before='-A ufw-before-input -i eth0 -p tcp --dport 50089 -j DROP'))

    def test_named_user_chains_and_return_preserve_order(self):
        text = rules().replace(':ufw-before-input', ':custom - [0:0]\n:ufw-before-input', 1)
        text = text.replace('-A INPUT -j ufw-before-input', '-A INPUT -j custom\n-A INPUT -j ufw-before-input')
        self.assertTrue(self.verify(text.replace('COMMIT', '-A custom -j RETURN\nCOMMIT')))
        with self.assertRaises(RuntimeError):
            self.verify(text.replace('COMMIT', '-A custom -p tcp --dport 50089 -j DROP\nCOMMIT'))

    def test_rule_presence_does_not_prove_ipv6(self):
        with self.assertRaisesRegex(RuntimeError, 'IPv6'):
            self.verify(rules(user=''), 6)

    def test_output_policy_can_block_replies_or_discovery(self):
        with self.assertRaisesRegex(RuntimeError, 'OUTPUT'):
            self.verify(rules(output='DROP'))

    def test_mdns_multicast_custom_drop_is_detected(self):
        for family, address in ((4, '224.0.0.251'), (6, 'ff02::fb')):
            with self.assertRaisesRegex(RuntimeError, '5353/udp'):
                self.verify(rules(before=f'-A ufw-before-input -d {address} -p udp --dport 5353 -j DROP'), family)

    def test_unknown_extension_never_proves_acceptance(self):
        with self.assertRaises(RuntimeError):
            self.verify(rules(user='-A ufw-user-input -p tcp --dport 50089 -m owner --uid-owner 1000 -j ACCEPT'))

    def test_empty_or_truncated_exports_cannot_succeed(self):
        for text in ('', '*filter\nCOMMIT', rules().replace('COMMIT', '')):
            with self.assertRaises(ValueError):
                self.verify(text)

    def test_numeric_protocols_multiports_and_inversion(self):
        self.assertTrue(self.verify(rules(user='-A ufw-user-input -p 6 -m multiport --dports 50084,50089:50090 -j ACCEPT')))
        with self.assertRaises(RuntimeError):
            self.verify(rules(user='-A ufw-user-input -p tcp ! --dport 50089 -j ACCEPT'))


if __name__ == '__main__':
    unittest.main()
