import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from monitorize.remote_login.descriptors import DescriptorStore,read_descriptor


class DescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'runtime'
        self.settings=SimpleNamespace(uid=os.getuid(),gid=os.getgid())
        self.adopt=Mock(return_value={'connector':'Virtual-4'})
        self.store=DescriptorStore(self.settings,lambda:'Virtual-4',self.adopt,root=self.root,owner_uid=os.getuid())
        self.caller={'uid':os.getuid(),'session':'s1'};self.token='a'*32

    def test_desktop_context_is_resolved_by_root_not_client(self):
        self.store.session_environment=lambda uid:('s1',{'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'Hyprland'})
        result=self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})
        self.assertEqual(result['desktop'],'Hyprland')
        with self.assertRaises(ValueError):self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4','desktop':'KDE'})
        self.store.session_environment=lambda uid:('other',{'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'Hyprland'})
        with self.assertRaises(PermissionError):self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})

    def test_cinnamon_handoff_passes_the_verified_session_to_the_runner(self):
        from monitorize.remote_login.runner import desktop_command

        self.settings.directory = self.root
        self.settings.profile = str(self.root)
        self.settings.home = str(self.root)
        self.settings.encoder = 'software'
        self.settings.adapter = ''
        for session_type in ('x11', 'wayland'):
            with self.subTest(session_type=session_type):
                environment = {'XDG_SESSION_TYPE': session_type,
                               'XDG_CURRENT_DESKTOP': 'X-Cinnamon', 'DISPLAY': ':7'}
                self.store.session_environment = lambda uid: ('s1', environment)
                result = self.store.write(self.caller, self.token,
                                          {'capture': 'kms', 'output_name': 'Virtual-4', 'display': ':99'})
                _, runner_environment = desktop_command(self.settings, result)
                self.assertEqual(runner_environment['XDG_SESSION_TYPE'], session_type)
                self.assertEqual(runner_environment['XDG_CURRENT_DESKTOP'], 'X-Cinnamon')
                if session_type == 'x11':
                    self.assertEqual(runner_environment['DISPLAY'], ':7')
                with self.assertRaisesRegex(ValueError, 'requires KMS'):
                    self.store.write(self.caller, self.token, {'capture': 'kwin',
                                     'output_name': 'Virtual-4', 'pipewire_node': 42})

    def test_private_descriptor_is_atomic_and_bound_to_session(self):
        self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})
        path=self.root/str(os.getuid())/'desktop.json'
        result=read_descriptor(path,os.getuid(),owner_uid=os.getuid())
        self.assertEqual(result['session'],'s1');self.assertEqual(result['generation'],self.token)
        self.assertEqual(path.stat().st_mode & 0o777,0o640)
        self.assertEqual(list(path.parent.glob('.desktop.*')),[])

    def test_restrictive_service_umask_still_allows_selected_account_read(self):
        previous=os.umask(0o077)
        try:self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})
        finally:os.umask(previous)
        directory=self.root/str(os.getuid())
        self.assertEqual(directory.stat().st_mode & 0o777,0o750)
        self.assertEqual((directory/'desktop.json').stat().st_mode & 0o777,0o640)

    def test_invalid_capture_never_reaches_compositor(self):
        with self.assertRaises(ValueError):self.store.write(self.caller,self.token,{'capture':'kms','output_name':'eDP-1'})
        self.adopt.assert_not_called();self.assertFalse(self.root.exists())

    def test_symlink_runtime_directory_is_rejected(self):
        self.root.symlink_to(Path(self.temp.name),target_is_directory=True)
        with self.assertRaises(ValueError):self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})

    def test_disconnected_protected_reservation_can_start_listener_after_physical_greeter(self):
        self.store.output_finder=lambda:'Virtual-0'
        lease=dict(schema_version=2,protected=True,owner_kind='remote-login',uid=os.getuid(),
                   phase='desktop',session='s1',state='reserved')
        self.adopt.return_value=lease
        result=self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-0'})
        self.assertEqual(result['output_name'],'Virtual-0')
        self.assertNotIn('connector',lease)
        self.assertTrue((self.root/str(os.getuid())/'desktop.json').is_file())
        # Never accept another account/session, partial or mismatched DRM identity,
        # an unprotected output or an active lease missing its connector identity.
        for changes in ({'uid':os.getuid()+1},{'session':'other'},{'protected':False},
                        {'state':'active'},{'connector':'Virtual-8'}, {'card':'card2'},
                        {'connector_id':3},{'owner_kind':'ordinary'},{'phase':'greeter'}):
            self.adopt.return_value=dict(lease,**changes)
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-0'})

    def test_wrong_output_after_adoption_is_not_published(self):
        self.adopt.return_value={'connector':'Virtual-5'}
        with self.assertRaises(RuntimeError):self.store.write(self.caller,self.token,{'capture':'kms','output_name':'Virtual-4'})
        self.assertFalse(self.root.exists())
