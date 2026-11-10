import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from monitorize.remote_login.settings import read_settings, validate_descriptor


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'settings.json'
        self.values=dict(schema_version=1,enabled=True,uid=1000,gid=1000,username='account',home='/home/account',
                         profile='/home/account/.config/monitorize/sunshine-1',manager='plasmalogin.service',encoder='software',adapter='')
        self.account=SimpleNamespace(pw_uid=1000,pw_gid=1000,pw_name='account',pw_dir='/home/account')
    def load(self):
        self.path.write_text(json.dumps(self.values));self.path.chmod(0o600)
        return read_settings(self.path,owner_uid=os.getuid(),account_lookup=lambda uid:self.account)

    def test_display_timeout_defaults_round_trips_and_rejects_invalid_values(self):
        self.assertEqual(self.load().display_idle_timeout_seconds,20)
        for value in (1,60,600):
            self.values['display_idle_timeout_seconds']=value
            self.assertEqual(self.load().display_idle_timeout_seconds,value)
        for value in (0,601,True,20.0,'20',None):
            self.values['display_idle_timeout_seconds']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.load()

    def test_account_binding_and_fixed_paths(self):
        settings=self.load()
        self.assertEqual(settings.directory,Path('/var/lib/monitorize/remote-login/1000'))
        self.assertEqual(settings.runtime,Path('/run/monitorize-remote-login/1000'))

    def test_sddm_manager_is_retained(self):
        self.values['manager']='sddm.service'
        self.assertEqual(self.load().manager,'sddm.service')

    def test_gdm_manager_is_retained(self):
        self.values['manager']='gdm.service'
        self.assertEqual(self.load().manager,'gdm.service')

    def test_uid_reuse_and_unsupported_manager_rejected(self):
        for field,value in (('username','other'),('home','/home/other'),('uid',True),('gid',123),('manager','xdm.service')):
            original=self.values[field];self.values[field]=value
            with self.assertRaises(ValueError):self.load()
            self.values[field]=original

    def test_unsafe_settings_file_rejected(self):
        self.load();self.path.chmod(0o666)
        with self.assertRaises(ValueError):read_settings(self.path,owner_uid=os.getuid(),account_lookup=lambda uid:self.account)
        self.path.unlink();self.path.symlink_to('/missing')
        with self.assertRaises(ValueError):read_settings(self.path,owner_uid=os.getuid(),account_lookup=lambda uid:self.account)

    def test_descriptor_has_only_bounded_typed_capture_values(self):
        settings=self.load()
        descriptor=validate_descriptor({'capture':'kms','output_name':'Virtual-4'},settings,'Virtual-4')
        self.assertEqual(descriptor['capture'],'kms')
        for descriptor in ({'capture':'portal','output_name':'Virtual-4'},
                           {'capture':'kms','output_name':'eDP-1'},
                           {'capture':'kms','output_name':'Virtual-4','command':'evil'},
                           {'capture':'kwin','output_name':'Virtual-4','pipewire_node':True}):
            with self.assertRaises(ValueError):validate_descriptor(descriptor,settings,'Virtual-4')

    def test_kwin_node_and_session_environment_are_constrained(self):
        settings=self.load()
        result=validate_descriptor({'capture':'kwin','output_name':'Virtual-4','pipewire_node':42,'wayland_display':'wayland-0'},settings,'Virtual-4')
        self.assertEqual(result['pipewire_node'],42)
        for name in ('../../other','wayland-0\nLD_PRELOAD=evil'):
            with self.assertRaises(ValueError):validate_descriptor({'capture':'kms','output_name':'Virtual-4','wayland_display':name},settings,'Virtual-4')



    def test_retired_experimental_enrollment_restores_original_encoder(self):
        self.values.update(transition_mode='seamless-experimental', encoder='software',
                           reconnect_encoder='vaapi', reconnect_adapter='/dev/dri/renderD128')
        result=self.load()
        self.assertEqual((result.encoder,result.adapter),('vaapi','/dev/dri/renderD128'))
        self.assertFalse(hasattr(result,'transition_mode'))
        self.values['reconnect_adapter']='../../unsafe'
        with self.assertRaises(ValueError):self.load()

    def test_stream_options_are_whitelisted_and_typed(self):
        settings=self.load()
        base={'capture':'kms','output_name':'Virtual-4'}
        options={'encoder':'nvenc','hevc_mode':'0','av1_mode':'0','adapter_name':'/dev/dri/renderD128','stream_audio':'enabled'}
        result=validate_descriptor(dict(base,stream_options=options),settings,'Virtual-4')
        self.assertEqual(result['stream_options'],options)
        for invalid in ({'command':'evil'},{'encoder':'nvenc\nfile_state=evil'},
                        {'adapter_name':'../../other'},{'hevc_mode':True}, {'stream_audio':'maybe'}):
            with self.assertRaises(ValueError):validate_descriptor(dict(base,stream_options=invalid),settings,'Virtual-4')

    def test_login_virtual_defaults_off_and_accepts_only_booleans(self):
        self.assertFalse(self.load().create_login_virtual_display)
        self.values['manager']='gdm.service'
        self.values['create_login_virtual_display']=True
        self.assertTrue(self.load().create_login_virtual_display)
        for value in (1,0,'true',None,[],{}):
            self.values['create_login_virtual_display']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.load()
