from pathlib import Path
import unittest
from unittest.mock import Mock
from monitorize.remote_login.boot_host import BootHost
from monitorize.platform.remote_login_identity import identity_paths


class BootHostTests(unittest.TestCase):
    def setUp(self):
        self.engine=Mock(running=False);self.engine.reap.return_value=None
        self.host=BootHost(self.engine,identity_paths(Path('/private')),Path('/fixed/sunshine'),Path('/fixed/boot.conf'),Path('/fixed/assets'))

    def test_greeter_start_has_exact_output_identity_and_no_desktop_environment(self):
        self.host.step('greeter','Virtual-4',0)
        argv,environment=self.engine.start.call_args.args
        self.assertIn('output_name=Virtual-4',argv)
        self.assertIn('port=50089',argv)
        self.assertIn('file_state=/private/identity/sunshine_state.json',argv)
        self.assertIn('credentials_file=/private/identity/sunshine_state.json',argv)
        for name in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','LD_LIBRARY_PATH'):
            self.assertNotIn(name,environment)
        self.assertEqual(environment['MONITORIZE_REQUIRE_EXACT_OUTPUT'],'1')
        for override in ('capture=kms','stream_audio=disabled','controller=disabled'):
            self.assertIn(override,argv)

    def test_login_stops_before_any_restart(self):
        self.engine.running=True
        self.assertFalse(self.host.step('user','Virtual-4',0))
        self.engine.stop.assert_called_once();self.engine.start.assert_not_called()

    def test_missing_or_changed_output_never_falls_back(self):
        self.host.step('greeter',None,0);self.engine.start.assert_not_called()
        self.host.step('greeter','Virtual-4',0)
        self.engine.running=True
        self.host.step('greeter','Virtual-5',1)
        self.engine.stop.assert_called_once()
        self.assertEqual(self.host.state,'output_changed')

    def test_engine_failure_uses_backoff_not_restart_loop(self):
        self.host.step('greeter','Virtual-4',0)
        self.engine.reap.return_value=1
        self.host.step('greeter','Virtual-4',1)
        self.assertEqual(self.engine.start.call_count,1)
        self.engine.reap.return_value=None
        self.host.step('greeter','Virtual-4',2)
        self.assertEqual(self.engine.start.call_count,1)
        self.host.step('greeter','Virtual-4',3)
        self.assertEqual(self.engine.start.call_count,2)

    def test_output_names_cannot_inject_config(self):
        for output in ('Virtual-4\ncapture=portal','eDP-1','Virtual-4 encoder=other'):
            with self.assertRaises(ValueError):self.host.step('greeter',output,0)
        self.engine.start.assert_not_called()

    def test_start_failure_is_exposed_without_private_exception_text(self):
        self.engine.start.side_effect=OSError('private-token-fixture')
        self.host.step('greeter','Virtual-4',0)
        self.assertIn('OSError',self.host.startup_error)
        self.assertNotIn('private-token-fixture',self.host.startup_error)

    def test_listener_timeout_stops_and_retries_instead_of_waiting_forever(self):
        self.host.step('greeter','Virtual-4',0);self.engine.running=True
        self.host.observe_listener(False,46)
        self.engine.stop.assert_called_once()
        self.assertIn('45 seconds',self.host.startup_error)
        self.assertEqual(self.host.state,'retry_wait')

    def test_listener_recovery_clears_startup_failure(self):
        self.engine.reap.return_value=1
        self.host.step('greeter','Virtual-4',0)
        self.assertIn('status 1',self.host.startup_error)
        self.host.observe_listener(True,2)
        self.assertEqual(self.host.startup_error,'')

    def test_stop_cancels_retries(self):
        self.host.close();self.assertFalse(self.host.step('greeter','Virtual-4',100))
        self.engine.start.assert_not_called()


    def test_managed_listener_starts_without_output_and_does_not_capture_placeholder(self):
        self.host.control_environment={'MONITORIZE_CONTROL_SOCKET':'/private/control.sock','MONITORIZE_CONTROL_TOKEN':'secret','MONITORIZE_CONTROL_INSTANCE':'1'}
        self.host.step('greeter',None,0)
        argv,env=self.engine.start.call_args.args
        self.assertIn('output_name=Virtual-0',argv)
        self.assertEqual(env['MONITORIZE_CONTROL_INSTANCE'],'1')
        self.assertNotIn('MONITORIZE_CAPTURE_OUTPUT',env)
