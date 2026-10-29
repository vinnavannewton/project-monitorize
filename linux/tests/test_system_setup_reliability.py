"""Setup failures must not become successful readiness/completion reports."""
import contextlib
import io
import json
import runpy
import stat
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monitorize.platform import system_setup

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'packaging/common/monitorize-system-setup'


class SetupReliabilityTests(unittest.TestCase):
    def helper(self):
        namespace=runpy.run_path(str(HELPER))
        namespace['main'].__globals__.update(ensure_input_assets=lambda:None,
            recovery_module=lambda:SimpleNamespace(restore_asset=lambda name:None))
        return namespace

    def test_firewalld_targets_and_verifies_active_zones_without_final_reload(self):
        function = self.helper()['enable_firewall']
        commands = []
        def execute(command, **kwargs):
            commands.append(command)
            args = command[1:]
            if args == ['--state']: output = 'running'
            elif args == ['--permanent', '--get-services']: output = 'ssh monitorize'
            elif args == ['--get-active-zones']: output = 'home\n  interfaces: wlan0\nwork\n  interfaces: enp1s0\n'
            elif '--query-service=monitorize' in args: output = 'yes'
            else: output = 'success'
            return subprocess.CompletedProcess(command, 0, output, '')
        with patch.dict(function.__globals__, command_path=lambda *a:'/usr/bin/firewall-cmd'), patch.object(subprocess, 'run', side_effect=execute):
            message = function()
        for zone in ('home', 'work'):
            self.assertTrue(any('--zone='+zone in c and '--add-service=monitorize' in c and '--permanent' in c for c in commands))
            self.assertTrue(any('--zone='+zone in c and '--add-service=monitorize' in c and '--permanent' not in c for c in commands))
            self.assertTrue(any('--zone='+zone in c and '--query-service=monitorize' in c and '--permanent' in c for c in commands))
            self.assertTrue(any('--zone='+zone in c and '--query-service=monitorize' in c and '--permanent' not in c for c in commands))
            self.assertIn(zone, message)
        self.assertFalse(any('--reload' in c for c in commands))

    def test_firewalld_readback_failure_is_not_success(self):
        function = self.helper()['enable_firewall']
        def execute(command, **kwargs):
            output = ('monitorize' if '--get-services' in command else 'home\n  interfaces: wlan0' if '--get-active-zones' in command else 'no' if '--query-service=monitorize' in command else 'running')
            return subprocess.CompletedProcess(command, 0, output, '')
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/bin/firewall-cmd'),patch.object(subprocess,'run',side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, 'verif'):
                function()

    def test_firewalld_status_failure_is_not_inactive(self):
        function = self.helper()['enable_firewall']
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/bin/firewall-cmd'),patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','D-Bus failure')):
            with self.assertRaisesRegex(RuntimeError, 'D-Bus failure'):
                function()

    def test_ufw_status_failure_is_not_inactive(self):
        function = self.helper()['enable_firewall']
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/sbin/ufw' if 'ufw' in a[0] else None),patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','backend failure')):
            with self.assertRaisesRegex(RuntimeError, 'backend failure'):
                function()

    def test_ufw_status_uses_c_locale_and_verifies_profile_rule(self):
        function = self.helper()['enable_firewall']
        calls=[]
        def execute(command, **kwargs):
            calls.append(command)
            if command[-1] == 'status':
                output='Status: active\nMonitorize ALLOW Anywhere\n' if kwargs.get('env',{}).get('LC_ALL')=='C' else 'Estado: activo'
            else: output='Rules updated'
            return subprocess.CompletedProcess(command,0,output,'')
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/sbin/ufw' if 'ufw' in a[0] else None),patch.object(subprocess,'run',side_effect=execute):
            self.assertIn('allows Monitorize',function())
        self.assertIn(['/usr/sbin/ufw','allow','Monitorize'],calls)
        self.assertGreaterEqual(sum(c[-1]=='status' for c in calls),2)

    def test_ufw_unknown_status_is_not_inactive(self):
        function=self.helper()['enable_firewall']
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/sbin/ufw' if 'ufw' in a[0] else None),patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0,'unexpected response','')):
            with self.assertRaisesRegex(RuntimeError,'status'):
                function()

    def test_inactive_firewalls_do_not_need_rules(self):
        function=self.helper()['enable_firewall']
        def execute(command,**kwargs):
            if command[-1]=='--state':return subprocess.CompletedProcess(command,252,'not running','')
            return subprocess.CompletedProcess(command,0,'Status: inactive','')
        with patch.dict(function.__globals__,command_path=lambda *a:a[0]),patch.object(subprocess,'run',side_effect=execute):
            self.assertIn('No active supported firewall',function())

    def test_unenforced_firewall_rule_is_not_success(self):
        function=self.helper()['enable_firewall']
        def execute(command,**kwargs):
            return subprocess.CompletedProcess(command,0,'Status: active' if command[-1]=='status' else 'Rules updated','')
        with patch.dict(function.__globals__,command_path=lambda *a:'/usr/sbin/ufw' if 'ufw' in a[0] else None),patch.object(subprocess,'run',side_effect=execute):
            with self.assertRaisesRegex(RuntimeError,'verif'):
                function()

    def test_enrolled_user_without_current_group_is_not_ready(self):
        with patch.object(system_setup.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=['sample'])),patch.object(system_setup.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='sample')),patch.object(system_setup.os,'getgid',return_value=1000),patch.object(system_setup.os,'getgroups',return_value=[1000]),patch.object(system_setup.os,'access',return_value=False):
            self.assertFalse(system_setup._input_ready())
            status=system_setup.get_system_setup_status()
        self.assertTrue(status.get('input_configured'))
        self.assertTrue(status.get('logout_required'))

    def test_current_group_without_device_access_is_not_ready(self):
        with patch.object(system_setup.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=['sample'])),patch.object(system_setup.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='sample')),patch.object(system_setup.os,'getgid',return_value=1000),patch.object(system_setup.os,'getgroups',return_value=[1000,4321]),patch.object(system_setup.os,'access',return_value=False):
            status=system_setup.get_system_setup_status()
        self.assertFalse(status['input_ready'])
        self.assertFalse(status.get('logout_required',True))
        self.assertIn('uinput',status.get('input_message',''))

    def test_retry_enrollment_does_not_claim_input_is_already_usable(self):
        function=self.helper()['enable_input']
        with patch.object(function.__globals__['grp'],'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=['sample'])),patch.object(function.__globals__['pwd'],'getpwnam',return_value=SimpleNamespace(pw_gid=1000)),patch.dict(function.__globals__,prepare_input_device=lambda:None):
            _,message=function('sample')
        self.assertNotIn('already enabled',message)
        self.assertIn('Log out',message)

    def test_input_device_repair_reports_failed_module_load(self):
        function=self.helper()['enable_input']
        with patch.object(function.__globals__['grp'],'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=[])),patch.object(function.__globals__['pwd'],'getpwnam',return_value=SimpleNamespace(pw_gid=1000)),patch.object(function.__globals__['Path'],'exists',return_value=False),patch.dict(function.__globals__,command_path=lambda *a:a[0]),patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','uinput module unavailable')):
            with self.assertRaisesRegex(RuntimeError,'uinput module unavailable'):
                function('sample')

    def test_accessible_static_node_with_unavailable_driver_is_not_ready(self):
        with patch.object(system_setup.os,'stat',return_value=SimpleNamespace(st_mode=stat.S_IFCHR|0o660)),patch.object(system_setup.os,'access',return_value=True),patch.object(system_setup.os,'open',side_effect=OSError('No such device')):
            self.assertFalse(system_setup._input_ready())

    def test_static_node_does_not_skip_failed_driver_load(self):
        function=self.helper()['prepare_input_device']
        node=SimpleNamespace(st_mode=stat.S_IFCHR|0o660,st_gid=4321)
        with patch.object(function.__globals__['Path'],'exists',return_value=True),patch.object(function.__globals__['Path'],'stat',return_value=node),patch.object(function.__globals__['grp'],'getgrnam',return_value=SimpleNamespace(gr_gid=4321)),patch.dict(function.__globals__,command_path=lambda *a:a[0]),patch.object(subprocess,'run',side_effect=lambda command,**kwargs:subprocess.CompletedProcess(command,1,'','uinput module unavailable') if command[-1]=='uinput' else subprocess.CompletedProcess(command,0,'','')):
            with self.assertRaisesRegex(RuntimeError,'uinput module unavailable'):
                function()

    def test_current_user_with_access_to_character_device_is_ready(self):
        with patch.object(system_setup.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=4321,gr_mem=['sample'])),patch.object(system_setup.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='sample')),patch.object(system_setup.os,'getgid',return_value=1000),patch.object(system_setup.os,'getgroups',return_value=[1000,4321]),patch.object(system_setup.os,'access',return_value=True),patch.object(system_setup.os,'stat',return_value=SimpleNamespace(st_mode=stat.S_IFCHR|0o660)),patch.object(system_setup.os,'open',return_value=12345),patch.object(system_setup.os,'close'):
            status=system_setup.get_system_setup_status()
        self.assertTrue(status['input_ready'])
        self.assertFalse(status['logout_required'])

    def test_repair_refreshes_only_uinput_and_verifies_group_permissions(self):
        function=self.helper()['prepare_input_device']
        commands=[]
        node=SimpleNamespace(st_mode=stat.S_IFCHR|0o660,st_gid=4321)
        with patch.object(function.__globals__['Path'],'exists',return_value=True),patch.object(function.__globals__['Path'],'stat',return_value=node),patch.object(function.__globals__['grp'],'getgrnam',return_value=SimpleNamespace(gr_gid=4321)),patch.dict(function.__globals__,command_path=lambda *a:a[0],run=lambda c:commands.append(c)),patch.object(function.__globals__['os'],'open',return_value=12345),patch.object(function.__globals__['os'],'close'):
            function()
        self.assertTrue(any('--sysname-match=uinput' in c for c in commands))
        self.assertFalse(any('chmod' in c or 'chown' in c for c in commands))

    def test_repair_refuses_conflicting_device_group_instead_of_broadening_permissions(self):
        function=self.helper()['prepare_input_device']
        with patch.object(function.__globals__['Path'],'exists',return_value=True),patch.object(function.__globals__['Path'],'stat',return_value=SimpleNamespace(st_mode=stat.S_IFCHR|0o660,st_gid=9999)),patch.object(function.__globals__['grp'],'getgrnam',return_value=SimpleNamespace(gr_gid=4321)),patch.dict(function.__globals__,command_path=lambda *a:a[0],run=lambda c:None):
            with self.assertRaisesRegex(RuntimeError,'conflicting udev'):
                function()

    def test_partial_failure_preserves_completed_actions_and_logout(self):
        payload={'success':False,'message':'Input configured. Firewall failed.','logout_required':True,'actions':{'input':{'success':True,'message':'Input configured.'},'firewall':{'success':False,'message':'Firewall failed.'}}}
        result=system_setup.parse_system_setup_result(1,json.dumps(payload),'Firewall failed.')
        self.assertTrue(result.get('logout_required'))
        self.assertEqual(result.get('actions'),payload['actions'])
        self.assertIn('Input configured',result['message'])

    def test_main_reports_input_success_when_firewall_fails(self):
        function=self.helper()['main']; output=io.StringIO()
        def input_action(user):return True,'Input configured. Log out and back in.'
        with patch.dict(function.__globals__,invoking_user=lambda:'sample',enable_input=input_action,enable_firewall=lambda:(_ for _ in ()).throw(RuntimeError('Firewall failed.'))),patch.object(function.__globals__['os'],'geteuid',return_value=0),patch('sys.argv',['helper','--input','--firewall']),contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):function()
        result=json.loads(output.getvalue())
        self.assertTrue(result['logout_required'])
        self.assertTrue(result.get('actions',{}).get('input',{}).get('success'))
        self.assertFalse(result['actions']['firewall']['success'])


if __name__=='__main__':unittest.main()
