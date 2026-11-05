import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from monitorize.config import settings
from monitorize.desktop.configuration_state import ConfigurationState,save_bundle


class ConfigurationStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches=[patch.object(settings,'CONFIG_DIR',self.tmp.name),patch.object(settings,'CONFIG_FILE',str(Path(self.tmp.name)/'settings.ini'))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.model=ConfigurationState(settings.load_display_settings(),settings.load_second_display_settings(),settings.load_general_settings())

    def test_kms_rejects_each_compositor_virtual_creator(self):
        from monitorize.desktop.configuration_state import capture_compatibility_error
        for desktop in ('kde', 'gnome', 'hyprland', 'sway'):
            self.assertIn('Auto', capture_compatibility_error(desktop, 'Extend', 'native', 'kms'))
            self.assertEqual(capture_compatibility_error(desktop, 'Extend', 'vkms', 'kms'), '')
            self.assertEqual(capture_compatibility_error(desktop, 'Mirror', 'native', 'kms'), '')

    def test_draft_has_no_persistent_or_applied_effect(self):
        old=copy.deepcopy(self.model.applied)
        self.model.update('primary',{'resolution':'2560x1440'})
        self.assertTrue(self.model.dirty)
        self.assertEqual(self.model.applied,old)
        self.assertEqual(settings.load_display_settings()['resolution'],old['primary']['resolution'])
        self.model.update('primary',old['primary'])
        self.assertFalse(self.model.dirty)

    def test_complete_bundle_and_general_settings_preserved(self):
        settings.save_general_settings(theme_mode='dark',minimize_to_tray=True)
        self.model.update('primary',{'resolution':'2560x1440'})
        self.model.update('second',{'enabled':True,'resolution':'1920x1200'})
        target=self.model.candidate();save_bundle(target);self.model.accept(target)
        self.assertFalse(self.model.dirty)
        self.assertEqual(settings.load_display_settings()['resolution'],'2560x1440')
        self.assertTrue(settings.load_second_display_settings()['enabled'])
        self.assertEqual(settings.load_general_settings()['theme_mode'],'dark')
        self.assertTrue(settings.load_general_settings()['minimize_to_tray'])

    def test_invalid_fixed_mode_rejected(self):
        self.model.update('primary',{'resolution':'Custom...','custom_w':'','custom_h':''})
        with self.assertRaises(ValueError):self.model.candidate()

    def test_requested_mode_does_not_require_fixed_resolution(self):
        self.model.update('primary',{'resolution':'','use_client_display_settings':True})
        self.assertTrue(self.model.candidate()['primary']['use_client_display_settings'])

    def test_mirror_requires_existing_output(self):
        self.model.update('primary',{'display_type':'Mirror','mirror_output':''})
        with self.assertRaisesRegex(ValueError,'existing screen'):self.model.candidate()
        self.model.update('primary',{'mirror_output':'eDP-1'})
        self.model.candidate()

    def test_candidate_is_not_mutable_draft(self):
        candidate=self.model.candidate();candidate['primary']['resolution']='800x600'
        self.assertNotEqual(candidate,self.model.draft)

    def test_native_gnome_extend_rejects_kms_before_starting(self):
        self.model.update('primary', {'sunshine_capture':'kms','virtual_display_creator':'native'})
        with self.assertRaisesRegex(ValueError, 'KMS.*VKMS'):
            self.model.candidate(desktop='gnome')

    def test_kms_is_valid_for_reserved_remote_output_and_mirror(self):
        self.model.update('primary', {'sunshine_capture':'kms','virtual_display_creator':'native'})
        self.model.candidate(desktop='gnome', remote_login=True)
        self.model.update('primary', {'display_type':'Mirror','mirror_output':'DP-1'})
        self.model.candidate(desktop='gnome')

    def test_second_native_gnome_display_rejects_kms(self):
        self.model.update('second', {'enabled':True,'sunshine_capture':'kms','virtual_display_creator':'native'})
        with self.assertRaisesRegex(ValueError, 'Display 2'):
            self.model.candidate(desktop='gnome')

    def test_leaving_remote_login_reconciles_capture_without_losing_draft_edits(self):
        self.model.update('primary', {'sunshine_capture':'kms','virtual_display_creator':'native'})
        self.model.accept(self.model.candidate(remote_login=True))
        self.model.update('primary', {'resolution':'2560x1440','sunshine_codec':'HEVC'})
        self.model.reconcile_desktop_capture('gnome')
        self.assertEqual(self.model.saved['primary']['sunshine_capture'],'auto')
        self.assertEqual(self.model.draft['primary']['sunshine_capture'],'auto')
        self.assertEqual(self.model.draft['primary']['resolution'],'2560x1440')
        self.assertEqual(self.model.draft['primary']['sunshine_codec'],'HEVC')
        self.assertTrue(self.model.dirty)

    def test_leaving_remote_login_preserves_vkms_and_mirror_kms(self):
        for values in ({'virtual_display_creator':'vkms'}, {'display_type':'Mirror','mirror_output':'DP-1'}):
            self.model.update('primary', dict(values,sunshine_capture='kms'))
            self.model.accept(self.model.candidate())
            self.model.reconcile_desktop_capture('gnome')
            self.assertEqual(self.model.saved['primary']['sunshine_capture'],'kms')

    def test_saved_later_is_distinct_from_running_configuration(self):
        old=self.model.applied['primary']['resolution']
        self.model.update('primary',{'resolution':'2560x1440'})
        candidate=self.model.candidate()
        self.model.save_later(candidate)
        self.assertFalse(self.model.dirty)
        self.assertTrue(self.model.restart_required)
        self.assertEqual(self.model.applied['primary']['resolution'],old)
        self.assertEqual(self.model.saved['primary']['resolution'],'2560x1440')
        self.model.accept(candidate)
        self.assertFalse(self.model.restart_required)
