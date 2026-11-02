"""First-run audit regressions: independent actions and truthful completion."""
import contextlib
import io
import json
import runpy
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from monitorize.platform import system_setup, setup_state
try:
    from .firewall_fixtures import firewall_response
except ImportError:
    from firewall_fixtures import firewall_response

ROOT = Path(__file__).resolve().parents[2]

class AuditRegressions(unittest.TestCase):
    def helper(self):
        return runpy.run_path(str(ROOT/'packaging/common/monitorize-system-setup'))

    def test_unmanaged_legacy_rules_are_not_mistaken_for_no_firewall(self):
        function = self.helper()['enable_firewall']; glob = function.__globals__
        for code, output in ((0, '*filter\n:INPUT DROP [0:0]\nCOMMIT\n'), (1, '')):
            with patch.dict(glob, command_path=lambda *paths:paths[0] if paths[0].endswith('/iptables-legacy-save') else None), \
                 patch.object(glob['subprocess'], 'run', return_value=subprocess.CompletedProcess([],code,output,'')):
                with self.assertRaisesRegex(RuntimeError, 'unmanaged iptables'):
                    function()

    def test_failed_input_does_not_skip_firewall(self):
        function = self.helper()['main']; glob = function.__globals__
        output = io.StringIO(); calls = []
        state = SimpleNamespace(PROTOCOL=setup_state.PROTOCOL, read_receipt=lambda uid: None, new_receipt=setup_state.new_receipt)
        def input_failure(user): raise RuntimeError('Conflicting udev rule')
        with patch.dict(glob, first_run_state=lambda:state, write_setup_receipt=lambda *a:None, invoking_user=lambda:'sample', enable_input=input_failure,
                        enable_firewall=lambda *args:calls.append('firewall') or 'Firewall verified'), \
             patch.object(glob['os'], 'geteuid', return_value=0), \
             patch.object(glob['os'], 'open', return_value=12345), \
             patch.object(glob['os'], 'close'), patch.object(glob['fcntl'], 'flock'), \
             patch.object(glob['pwd'],'getpwnam',return_value=SimpleNamespace(pw_uid=1000)), patch('sys.argv', ['helper','--input','--firewall']), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit): function()
        result = json.loads(output.getvalue())
        self.assertEqual(calls, ['firewall'])
        self.assertFalse(result['actions']['input']['success'])
        self.assertTrue(result['actions']['firewall']['success'])

    def test_ufw_deny_is_preserved_and_reported(self):
        function = self.helper()['enable_firewall']; glob = function.__globals__
        calls=[]
        def execute(command, **kwargs):
            calls.append(command)
            metadata = firewall_response(command)
            if metadata is not None:return subprocess.CompletedProcess(command,0,metadata,'')
            output = ('*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\n-A INPUT -p tcp --dport 50089 -j DROP\n-A INPUT -j ACCEPT\nCOMMIT\n'
                      if command[0].endswith('tables-save') else 'Status: active\n50089/tcp DENY Anywhere\nMonitorize ALLOW Anywhere\n')
            return subprocess.CompletedProcess(command,0,output,'')
        with patch.dict(glob,command_path=lambda *a:a[0] if any(name in a[0] for name in ('/ufw','/iptables-save','/ip6tables-save')) else None,
                        recovery_module=lambda:SimpleNamespace(restore_asset=lambda _:None)), \
             patch.object(glob['subprocess'],'run',side_effect=execute):
            message = function()
            self.assertIn('Connection verification needs a Moonlight test', message)
            self.assertIn('50089/tcp', message)
        self.assertFalse(any('delete' in command or 'reset' in command for command in calls))

    def test_missing_device_does_not_suggest_logout_as_repair(self):
        with patch.object(system_setup.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=['sample'])), \
             patch.object(system_setup.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='sample',pw_gid=1000)), \
             patch.object(system_setup.os,'getgid',return_value=1000), \
             patch.object(system_setup.os,'getgroups',return_value=[1000]), \
             patch.object(system_setup.os,'stat',side_effect=FileNotFoundError):
            status = system_setup._input_status()
        self.assertFalse(status['logout_required'])
        self.assertFalse(status['input_ready'])

    def test_all_active_managers_are_checked(self):
        function = self.helper()['enable_firewall']; glob = function.__globals__; calls=[]
        def execute(command, **kwargs):
            calls.append(command)
            metadata = firewall_response(command)
            if metadata is not None:return subprocess.CompletedProcess(command,0,metadata,'')
            if command[-1]=='status': output='Status: active\nMonitorize ALLOW Anywhere\n'
            elif command[0].endswith('tables-save'): output='*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
            elif command[-1]=='ruleset': output='{"nftables":[]}'
            elif command[-1]=='--get-active-zones': output='home\n  interfaces: test0'
            elif any(arg.startswith('--query-port=') for arg in command): output='yes'
            else: output='running'
            return subprocess.CompletedProcess(command,0,output,'')
        with patch.dict(glob,command_path=lambda *a:None if 'legacy' in a[0] else a[0],recovery_module=lambda:SimpleNamespace(restore_asset=lambda _:None)), \
             patch.object(glob['subprocess'],'run',side_effect=execute):
            message = function()
        self.assertIn('Firewalld', message)
        self.assertIn('UFW', message)
        self.assertTrue(any(c[-1]=='status' for c in calls))

    def test_only_exact_unowned_legacy_rule_is_migrated(self):
        import tempfile
        function = self.helper()['ensure_input_assets']; glob = function.__globals__
        original = b'KERNEL=="uinput", GROUP="input", MODE="0660", TAG+="uaccess"\n'
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory)/'99-monitorize-uinput.rules'
            recovery = SimpleNamespace(restore_asset=lambda _:None, read_safe=lambda p:p.read_bytes(), owner=lambda _:None)
            with patch.dict(glob, Path=lambda _:legacy, recovery_module=lambda:recovery), patch.object(glob['grp'],'getgrnam',return_value=object()):
                legacy.write_bytes(original + b'# customized\n')
                function()
                self.assertTrue(legacy.exists())
                legacy.write_bytes(original)
                function()
                self.assertFalse(legacy.exists())
                self.assertEqual(legacy.with_suffix('.rules.monitorize-backup').read_bytes(), original)

    def test_receipt_uses_invoking_account_and_root_owned_writer(self):
        from unittest.mock import Mock
        function = self.helper()['write_setup_receipt']; glob = function.__globals__
        writer = Mock()
        with patch.dict(glob,recovery_module=lambda:SimpleNamespace(write_owned=writer)), \
             patch.object(glob['pwd'],'getpwnam',return_value=SimpleNamespace(pw_uid=1234)):
            function('sample', {'actions': {'input': {'success': True}}, 'running': False})
        args, kwargs = writer.call_args
        self.assertEqual(args[0], Path('/var/lib/monitorize/setup/1234.json'))
        self.assertFalse(json.loads(args[1])['running'])
        self.assertEqual(kwargs['mode'], 0o644)
