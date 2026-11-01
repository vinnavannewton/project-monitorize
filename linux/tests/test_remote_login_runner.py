from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from monitorize.remote_login.runner import desktop_command,own_session


class RunnerTests(unittest.TestCase):
    def test_desktop_uses_shared_identity_and_explicit_encoder_without_boot_audio_flags(self):
        settings=SimpleNamespace(profile='/home/account/.config/monitorize/sunshine-1',directory=Path('/private'),
            encoder='software',adapter='',home='/home/account',uid=1000)
        argv,env=desktop_command(settings,{'capture':'kms','output_name':'Virtual-4'})
        self.assertIn('encoder=software',argv)
        self.assertIn('port=50089',argv)
        self.assertNotIn('adapter_name=',argv)
        self.assertIn('file_state=/private/identity/sunshine_state.json',argv)
        self.assertNotIn('stream_audio=disabled',argv)
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],'unix:path=/run/user/1000/bus')
        self.assertNotIn('LD_PRELOAD',env)
        settings.adapter='/dev/dri/renderD128'
        selected,_=desktop_command(settings,{'capture':'kms','output_name':'Virtual-4'})
        self.assertIn('adapter_name=/dev/dri/renderD128',selected)

    def test_desktop_options_override_enrolled_encoder_but_not_identity(self):
        settings=SimpleNamespace(profile='/home/account/.config/monitorize/sunshine-1',directory=Path('/private'),
            encoder='software',adapter='/dev/dri/renderD128',home='/home/account',uid=1000)
        argv,env=desktop_command(settings,{'capture':'kms','output_name':'Virtual-4',
            'stream_options':{'encoder':'nvenc','hevc_mode':'2','av1_mode':'1','adapter_name':''}})
        self.assertEqual([v for v in argv if v.startswith('encoder=')][-1],'encoder=nvenc')
        self.assertFalse(any(v.endswith('=') for v in argv))
        self.assertIn('hevc_mode=2',argv)
        with self.assertRaises(ValueError):desktop_command(settings,{'capture':'kms','output_name':'Virtual-4',
            'stream_options':{'file_state':'/untrusted'}})

    def test_auto_settings_clear_profile_without_empty_cli_values(self):
        import tempfile
        from monitorize.remote_login.runner import write_desktop_config
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);profile=root/'profile';profile.mkdir();(root/'runtime').mkdir()
            (profile/'sunshine.conf').write_text('encoder = nvenc\nadapter_name = /dev/dri/renderD129\nhevc_mode = 2\n# untouched\nfoo = keep\n')
            settings=SimpleNamespace(profile=str(profile),directory=root,encoder='software',adapter='/dev/dri/renderD128',home=str(root),uid=1000)
            descriptor=dict(capture='kms',output_name='Virtual-1',stream_options={'encoder':'','adapter_name':'','hevc_mode':'0','av1_mode':'0'})
            write_desktop_config(settings,descriptor)
            argv,_=desktop_command(settings,descriptor)
            self.assertFalse(any(v.endswith('=') for v in argv))
            self.assertFalse(any(v.startswith('encoder=') or v.startswith('adapter_name=') for v in argv))
            text=Path(argv[1]).read_text()
            self.assertNotIn('encoder = nvenc',text);self.assertNotIn('adapter_name =',text)
            self.assertIn('foo = keep',text);self.assertIn('hevc_mode=0',argv)
            self.assertIn('encoder = nvenc',(profile/'sunshine.conf').read_text())

    def test_trusted_hyprland_context_replaces_kde_environment(self):
        settings=SimpleNamespace(profile='/home/account/config',directory=Path('/private'),encoder='software',adapter='',home='/home/account',uid=1000)
        _,environment=desktop_command(settings,dict(capture='kms',output_name='Virtual-1',desktop='Hyprland'))
        self.assertEqual(environment['XDG_CURRENT_DESKTOP'],'Hyprland')
        with self.assertRaises(ValueError):desktop_command(settings,dict(capture='kms',output_name='Virtual-1',desktop='LD_PRELOAD=evil'))

    def test_session_check_rejects_logout_other_user_remote_or_wrong_seat(self):
        values='User=1000\nActive=yes\nRemote=no\nType=wayland\nClass=user\nSeat=seat0\n'
        with patch('monitorize.remote_login.runner.subprocess.run',return_value=Mock(stdout=values)):
            self.assertTrue(own_session('s1',1000))
        for old,new in [('User=1000','User=1001'),('Active=yes','Active=no'),('Remote=no','Remote=yes'),('Seat=seat0','Seat=seat1')]:
            with patch('monitorize.remote_login.runner.subprocess.run',return_value=Mock(stdout=values.replace(old,new))):
                self.assertFalse(own_session('s1',1000))

    def test_boot_listener_does_not_prepare_a_display_without_a_client(self):
        import json,tempfile
        from contextlib import ExitStack
        from monitorize.remote_login.runner import boot_main
        from monitorize.platform.remote_login_identity import identity_paths
        with tempfile.TemporaryDirectory() as d,ExitStack() as stack:
            settings=SimpleNamespace(directory=Path(d),uid=1000,display_idle_timeout_seconds=20)
            owner=Mock(running=False);owner.paths=identity_paths(Path(d));owner.process=None;owner.reap.return_value=None
            def start(*args,**kwargs):
                owner.running=True;owner.process=Mock();owner.process.poll.return_value=None
            owner.start.side_effect=start
            stop=Mock();stop.is_set.side_effect=[False,True]
            interface=Mock();interface.BootContext.return_value=json.dumps(dict(generation='g',session='g1',connected=False))
            interface.EngineDisplay.return_value='{}'
            for target,value in (
                ('monitorize.remote_login.runner.preflight',Mock(return_value=settings)),
                ('monitorize.remote_login.runner.require_installed',Mock()),
                ('monitorize.remote_login.runner.EngineOwner',Mock(return_value=owner)),
                ('monitorize.remote_login.runner._stopping',Mock(return_value=stop)),
                ('monitorize.remote_login.runner.read_phase',Mock(return_value='greeter')),
                ('monitorize.remote_login.runner.write_health',Mock()),
                ('monitorize.remote_login.control.ControlServer',Mock()),
                ('monitorize.remote_login.runner.threading.Thread',Mock()),
                ('dbus.SystemBus',Mock()),('dbus.Interface',Mock(return_value=interface))):
                stack.enter_context(patch(target,value))
            self.assertEqual(boot_main(),0)
            owner.start.assert_called_once()
            self.assertEqual([c.args[1] for c in interface.EngineDisplay.call_args_list],['check'])
            self.assertIn('MONITORIZE_CONTROL_SOCKET',owner.start.call_args.args[1])
            owner.stop.assert_called_once()

    def test_desktop_early_exit_is_published_in_health(self):
        import tempfile
        from contextlib import ExitStack
        from monitorize.remote_login.runner import desktop_main
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            settings=SimpleNamespace(directory=Path(directory),uid=1000,runtime=Path(directory),display_idle_timeout_seconds=20)
            owner=Mock(running=False);owner.process=None;owner.reap.return_value=0
            stop=Mock();stop.is_set.side_effect=[False,True]
            health=Mock()
            for target,value in (
                ('monitorize.remote_login.runner.preflight',Mock(return_value=settings)),
                ('monitorize.remote_login.runner.read_descriptor',Mock(return_value={'session':'s1','generation':'g'})),
                ('monitorize.remote_login.runner.write_desktop_config',Mock()),
                ('monitorize.remote_login.runner.desktop_command',Mock(return_value=(['engine'],{}))),
                ('monitorize.remote_login.runner.EngineOwner',Mock(return_value=owner)),
                ('monitorize.remote_login.runner._stopping',Mock(return_value=stop)),
                ('monitorize.remote_login.runner.own_session',Mock(return_value=True)),
                ('monitorize.remote_login.runner.write_health',health),
                ('monitorize.remote_login.control.ControlServer',Mock())):
                stack.enter_context(patch(target,value))
            self.assertEqual(desktop_main(),0)
            self.assertIn('Sunshine exited (status 0)',health.call_args.args[1]['startup_error'])
            self.assertFalse(health.call_args.args[1]['listening'])
            owner.start.assert_not_called()  # Backoff remains active.
