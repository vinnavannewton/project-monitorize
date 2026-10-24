import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from monitorize.remote_login.wayland_greeter import activate_greeter,greeter_compositor

class GreeterTests(unittest.TestCase):
    def setUp(self):
        self.settings=SimpleNamespace(manager='sddm.service')
        self.context={'session':'c1','greeter_uid':991}
        self.env={'XDG_SESSION_TYPE':'wayland','WAYLAND_DISPLAY':'wayland-0'}
        self.helper=Mock();self.helper._validate_session_socket.return_value=123
        self.output=dict(name='Virtual-1',card='card3',connector_id=49)
    def run_adapter(self,compositor,executable):
        with patch('monitorize.remote_login.wayland_greeter.require_manager',return_value={'manager':'sddm.service','compositor':compositor}), patch('monitorize.remote_login.wayland_greeter.Path.resolve',return_value=__import__('pathlib').Path('/usr/bin/'+executable)):
            return activate_greeter(self.settings,self.context,'c1',self.env,self.output,5,self.helper,Mock())
    def test_kwin_uses_only_greeter_uid(self):
        self.run_adapter('kde','kwin_wayland')
        self.assertEqual(self.helper._run_worker.call_args.args[0],991)
        self.assertEqual(self.helper._run_worker.call_args.args[1]['XDG_CURRENT_DESKTOP'],'KDE')
        self.helper._validate_session_socket.assert_called_once()
    def test_verified_activation_result_reaches_host_mode_check(self):
        self.helper._run_worker.return_value=dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))
        self.assertEqual(self.run_adapter('kde','kwin_wayland'),
                         dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60)))
    def test_hyprland_uses_its_own_session_socket(self):
        self.run_adapter('hyprland','Hyprland')
        self.assertEqual(self.helper._run_worker.call_args.args[1]['XDG_CURRENT_DESKTOP'],'Hyprland')
    def test_wrong_running_compositor_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'does not match'):self.run_adapter('kde','Hyprland')
        self.helper._run_worker.assert_not_called()
    def test_weston_requires_verified_hotplug_not_kde_commands(self):
        with self.assertRaisesRegex(RuntimeError,'Weston did not activate'):self.run_adapter('weston','weston')
        self.helper._run_worker.assert_not_called()
    def test_session_change_prevents_activation(self):
        with patch('monitorize.remote_login.wayland_greeter.require_manager',return_value={'manager':'sddm.service','compositor':'kde'}):
            with self.assertRaises(PermissionError):activate_greeter(self.settings,self.context,'other',self.env,self.output,5,self.helper,Mock())
        self.helper._run_worker.assert_not_called()
    def test_activation_returns_verified_worker_result_to_host_runtime(self):
        result=dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))
        self.helper._run_worker.return_value=result
        self.assertIs(self.run_adapter('kde','kwin_wayland'),result)
