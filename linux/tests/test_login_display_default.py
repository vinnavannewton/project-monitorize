"""Physical-login default, positive opt-in and authenticated KDE output selection."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from monitorize.remote_login import login_primary as primary,wayland_greeter,enrollment
from monitorize.remote_login.root_files import RootFiles
from monitorize.remote_login.settings import read_settings


def output(name='eDP-1',priority=1,**extra):
    return dict(name=name,priority=priority,enabled=True,connected=True,currentModeId='m',
                modes=[dict(id='m',size=dict(width=2560,height=1600),refreshRate=165.0)],**extra)


class PhysicalLoginTests(unittest.TestCase):
    def test_old_preferences_migrate_to_physical_and_positive_choice_persists(self):
        account=SimpleNamespace(pw_uid=1000,pw_gid=1000,pw_name='user',pw_dir='/home/user')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            values=dict(schema_version=1,uid=1000,gid=1000,username='user',home='/home/user',profile='/home/user/profile',
                enabled=True,manager='plasmalogin.service',encoder='software',adapter='')
            for legacy in (None,False,True):
                if legacy is not None:values['login_primary_display']=legacy
                path.write_text(json.dumps(values))
                settings=read_settings(path,owner_uid=os.getuid(),account_lookup=lambda uid:account)
                self.assertFalse(settings.create_login_virtual_display)
            values['create_login_virtual_display']=True;path.write_text(json.dumps(values))
            self.assertTrue(read_settings(path,owner_uid=os.getuid(),account_lookup=lambda uid:account).create_login_virtual_display)
            for bad in (1,None,'false'):
                values['create_login_virtual_display']=bad;path.write_text(json.dumps(values))
                with self.assertRaises(ValueError):read_settings(path,owner_uid=os.getuid(),account_lookup=lambda uid:account)
    def test_default_routes_only_to_existing_display_and_opt_in_routes_virtual(self):
        for settings in (SimpleNamespace(),SimpleNamespace(create_login_virtual_display=False)):
            for action in ('prepare','idle'):
                with patch.object(primary,'operation',return_value={'existing':True}) as physical:
                    virtual=Mock();self.assertEqual(wayland_greeter.capture_operation(settings,{},action,Mock(),Mock(),virtual),{'existing':True})
                    physical.assert_called_once();virtual.assert_not_called()
        virtual=Mock(return_value={'virtual':True})
        with patch.object(primary,'operation') as physical:
            self.assertEqual(wayland_greeter.capture_operation(SimpleNamespace(create_login_virtual_display=True),{},'prepare',Mock(),Mock(),virtual),{'virtual':True})
            physical.assert_not_called()
    def test_no_physical_display_does_not_fall_back_to_virtual(self):
        with patch.object(primary,'operation',side_effect=ValueError('No existing login display')):
            virtual=Mock()
            with self.assertRaises(ValueError):wayland_greeter.capture_operation(SimpleNamespace(),{},'prepare',Mock(),Mock(),virtual)
            virtual.assert_not_called()
    def test_kwin_selects_unique_enabled_primary_and_its_actual_mode(self):
        other=output('HDMI-A-1',2)
        self.assertEqual(primary.select_kwin_primary(dict(outputs=[other,output()])),
            dict(output_name='eDP-1',width=2560,height=1600,fps=165.0,capture='kms'))
        self.assertEqual(primary.select_kwin_primary(dict(outputs=[output(priority=0)]))['output_name'],'eDP-1')
    def test_kwin_rejects_ambiguous_missing_disabled_and_invalid_targets(self):
        disabled=output();disabled['enabled']=False
        invalid=output();invalid['modes'][0]['refreshRate']=float('nan')
        for outputs in ([],[disabled],[output(),output('HDMI-A-1')],[output(priority=2),output('HDMI-A-1',3)],[invalid]):
            with self.subTest(outputs=outputs),self.assertRaises(ValueError):primary.select_kwin_primary(dict(outputs=outputs))
    def test_positive_setting_preserves_enrollment_and_never_starts_services(self):
        with TemporaryDirectory() as directory:
            files=RootFiles(Path(directory),owner_uid=os.getuid())
            original=dict(schema_version=1,uid=1000,enabled=True,manager='plasmalogin.service',login_primary_display=False,encoder='software')
            files.write('etc/monitorize/remote-login.json',json.dumps(original))
            with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000,enabled=True,manager='plasmalogin.service')),patch.object(enrollment,'RootFiles',return_value=files),patch.object(enrollment,'Commands') as commands:
                result=enrollment.configure_login_virtual_display(1000,True)
                saved=json.loads((Path(directory)/'etc/monitorize/remote-login.json').read_text())
                self.assertTrue(saved['create_login_virtual_display']);self.assertNotIn('login_primary_display',saved)
                self.assertTrue(saved['enabled']);self.assertTrue(result['reboot_required']);commands.assert_not_called()


class KwinAuthenticatedCaptureTests(unittest.TestCase):
    def prepare(self,manager='plasmalogin.service',peers=(123,123)):
        uid=os.getuid();environment=dict(XDG_SESSION_TYPE='wayland',XDG_RUNTIME_DIR='/run/user/'+str(uid),WAYLAND_DISPLAY='wayland-0')
        target=dict(output_name='eDP-1',capture='kms',width=2560,height=1600,fps=165.0)
        response=SimpleNamespace(returncode=0,stdout=json.dumps(dict(pid=123,target=target)))
        helper=Mock();check=Mock()
        with patch('monitorize.remote_login.kwin_context.verify',return_value=environment) as verify, \
             patch('monitorize.remote_login.gdm_context.socket_owner',side_effect=peers), \
             patch('monitorize.remote_login.display_managers.require_manager',return_value=dict(compositor='kde')), \
             patch('monitorize.remote_login.daemon.require_installed') as trusted, \
             patch.object(primary.subprocess,'run',return_value=response) as run, \
             patch.object(primary,'verify_drm',return_value=Path('/sys/class/drm/card0-eDP-1')):
            actual=primary.operation(SimpleNamespace(manager=manager),dict(session='g1',greeter_uid=uid),'prepare',helper,check)
            self.assertEqual(actual,target);self.assertEqual(verify.call_count,2)
            self.assertEqual(run.call_args.args[0][-2:],['query','kde'])
            self.assertEqual(run.call_args.kwargs['user'],uid)
            trusted.assert_called_once_with(Path('/usr/bin/kscreen-doctor'))
            self.assertEqual(helper.mock_calls,[])
    def test_plasma_capture_rechecks_compositor_and_never_changes_outputs(self):self.prepare()
    def test_sddm_kwin_uses_authenticated_primary_capture(self):self.prepare('sddm.service')
    def test_socket_replacement_rejects_capture(self):
        with self.assertRaises(PermissionError):self.prepare(peers=(123,456))
    def test_other_sddm_compositor_has_no_silent_virtual_fallback(self):
        with patch('monitorize.remote_login.display_managers.require_manager',return_value=dict(compositor='hyprland')):
            with self.assertRaisesRegex(ValueError,'Enable virtual-display login'):
                primary.operation(SimpleNamespace(manager='sddm.service'),dict(session='g1',greeter_uid=os.getuid()),'prepare',Mock(),Mock())
    def test_kde_idle_never_calls_virtual_host_or_changes_physical_output(self):
        for manager in ('plasmalogin.service','sddm.service'):
            helper=Mock()
            self.assertEqual(primary.operation(SimpleNamespace(manager=manager),{},'idle',helper,Mock()),{})
            self.assertEqual(helper.mock_calls,[])


class LoginDisplayCapabilityTests(unittest.TestCase):
    def test_capability_requires_both_modules_and_the_fixed_installed_actions(self):
        from monitorize.platform import system_setup
        sources={
            '/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py':'LOGIN_DISPLAY_POLICY_PROTOCOL = 1',
            '/usr/lib/monitorize-remote-login/monitorize/remote_login/login_primary.py':'LOGIN_DISPLAY_POLICY_PROTOCOL = 1',
            str(system_setup.HELPER):'login-virtual-on',
            '/usr/libexec/monitorize/remote-login/entry.py':'setup-login-virtual-on'}
        with patch.object(Path,'read_text',autospec=True,side_effect=lambda path:sources[str(path)]):
            self.assertTrue(system_setup.login_display_policy_available())
            for path in sources:
                previous=sources[path];sources[path]='LOGIN_DISPLAY_POLICY_PROTOCOL = 0'
                self.assertFalse(system_setup.login_display_policy_available())
                sources[path]=previous

    def test_fixed_actions_are_gated_by_matching_installed_policy(self):
        from monitorize.platform import system_setup
        for available in (True,False):
            with patch.object(system_setup,'remote_login_setup_status',return_value=dict(available=True,enabled=True)), \
                 patch.object(system_setup,'login_display_policy_available',return_value=available), \
                 patch.object(system_setup.shutil,'which',return_value='/usr/bin/pkexec'), \
                 patch.object(system_setup.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"success":true}',stderr='')) as run:
                self.assertEqual(system_setup.apply_login_virtual_display(True)['success'],available)
                if available:self.assertEqual(run.call_args.args[0][-1],'login-virtual-on')
                else:run.assert_not_called()
