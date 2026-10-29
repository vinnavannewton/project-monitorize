import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from monitorize.config import settings
from monitorize.platform import system_setup

class GeneralIdleTimeoutTests(unittest.TestCase):
 def test_local_timeout_round_trip_without_startup_or_admin(self):
  with TemporaryDirectory() as d,patch.object(settings,'CONFIG_DIR',d),patch.object(settings,'CONFIG_FILE',str(Path(d)/'settings.ini')),patch.object(system_setup,'remote_login_setup_status',return_value=dict(enabled=False)),patch.object(system_setup,'apply_display_idle_timeout') as admin:
   result=system_setup.apply_general_display_idle_timeout(45)
   self.assertTrue(result['success']);self.assertEqual(settings.load_display_idle_timeout(),45)
   admin.assert_not_called()
 def test_enabled_remote_mode_must_sync_before_local_save(self):
  with patch.object(system_setup,'remote_login_setup_status',return_value=dict(enabled=True)),patch.object(system_setup,'apply_display_idle_timeout',return_value=dict(success=False,message='Cancelled')),patch.object(settings,'save_display_idle_timeout') as save:
   self.assertFalse(system_setup.apply_general_display_idle_timeout(60)['success']);save.assert_not_called()
 def test_invalid_values_are_rejected_without_changes(self):
  with patch.object(system_setup,'apply_display_idle_timeout') as admin:
   for v in (0,601,True,'20'):
    self.assertFalse(system_setup.apply_general_display_idle_timeout(v)['success'])
   admin.assert_not_called()
 def test_existing_remote_timeout_is_retained_until_general_preference_is_saved(self):
  from types import SimpleNamespace
  with TemporaryDirectory() as d,patch.object(settings,'CONFIG_DIR',d),patch.object(settings,'CONFIG_FILE',str(Path(d)/'settings.ini')),patch('monitorize.remote_login.settings.read_settings',return_value=SimpleNamespace(uid=os.getuid(),display_idle_timeout_seconds=75)):
   self.assertEqual(settings.load_display_idle_timeout(),75)
   settings.save_display_idle_timeout(30)
   self.assertEqual(settings.load_display_idle_timeout(),30)
 def test_reenabling_remote_login_synchronizes_explicit_general_preference(self):
  from monitorize.desktop import remote_login_setup as setup
  with patch.object(setup,'controller_snapshot',return_value={}),patch.object(setup,'configure'),patch.object(setup,'apply_remote_login_setup',return_value=dict(success=True,configured=True)),patch.object(settings,'saved_display_idle_timeout',return_value=47),patch.object(setup,'remote_login_setup_status',return_value=dict(display_idle_timeout_seconds=20)),patch.object(system_setup,'apply_display_idle_timeout',return_value=dict(success=True)) as sync:
   from types import SimpleNamespace
   state=SimpleNamespace(request=SimpleNamespace(enabled=True),_restore_controller=False,_root_applied=None)
   result=setup.RemoteLoginSetupCoordinator._work(state)
   self.assertTrue(result['success']);sync.assert_called_once_with(47)
