"""Static readiness must explain host incompatibility without authorizing anything."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from monitorize.platform.remote_login_host import host_readiness


class HostReadinessTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)

    def file(self,path,value=b''):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(value);target.chmod(0o644);return target

    def assets(self):
        for path in ('usr/libexec/monitorize/remote-login/entry.py','usr/libexec/monitorize/monitorize-system-setup',
                     'usr/lib/systemd/system/monitorize-vkms-boot-display.service',
                     'usr/lib/monitorize-remote-login/monitorize/remote_login/display_managers.py'):
            self.file(path)

    def host(self):
        for path in ('usr/lib/monitorize-vkms/monitorize_vkms/helper.py',
                     'usr/libexec/monitorize-vkms/monitorize-vkms-helper',
                     'usr/lib/monitorize-vkms/monitorize_vkms/boot_display.py'):
            self.file(path,b'GREETER_ON_DEMAND_PROTOCOL = 1\nIDLE_DISPLAY_PROTOCOL = 1\n' if path.endswith('boot_display.py') else b'BOOT_DISPLAY_PROTOCOL = 1\n')
        self.file('usr/lib/monitorize-vkms/VERSION',b'0.3.0\n')
        self.manifest=dict(schema_version=1,version='0.3.0',remote_login_protocols=dict(
            boot_display=1,greeter_on_demand=1,idle_display=1))
        self.write_manifest()

    def write_manifest(self):
        self.file('usr/share/monitorize-vkms/capabilities.json',json.dumps(self.manifest).encode())

    def result(self):return host_readiness(self.root,owner_uid=os.getuid())

    def live(self):return host_readiness(self.root,owner_uid=os.getuid(),require_live=True)

    def test_live_capability_requires_matching_host_and_installed_app_action(self):
        self.assets();self.host()
        self.file('usr/libexec/monitorize/sunshine',b'\x7fELF\0MONITORIZE_CONTROL_PROTOCOL=1\0MONITORIZE_STRICT_KMS_OUTPUT_MISSING\0')
        self.assertTrue(self.result()['available'])
        self.assertFalse(self.live()['available'])
        self.manifest['remote_login_protocols']['live_enrollment']=1;self.write_manifest()
        self.assertFalse(self.live()['available'], 'a manifest must not invent an implemented host API')
        self.file('usr/lib/monitorize-vkms/monitorize_vkms/boot_display.py',
                  b'GREETER_ON_DEMAND_PROTOCOL = 1\nIDLE_DISPLAY_PROTOCOL = 1\nLIVE_ENROLLMENT_PROTOCOL = 1\n')
        self.assertFalse(self.live()['available'], 'old installed app assets must not enable live setup')
        self.file('usr/lib/monitorize-remote-login/monitorize/remote_login/live_setup.py',b'LIVE_ENROLLMENT_PROTOCOL = 1\n')
        self.file('usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py',b'SETUP_RECOVERY_PROTOCOL = 1\n')
        self.file('usr/libexec/monitorize/remote-login/entry.py',b"action = 'setup-enable-live'\n")
        self.file('usr/libexec/monitorize/monitorize-system-setup',b"action = 'enable-live'\n")
        self.assertTrue(self.live()['available'])
        self.assertTrue(self.result()['available'])
        self.file('usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py',b'SETUP_RECOVERY_PROTOCOL = 0\n')
        self.assertFalse(self.live()['available'],'old setup code must not advertise managed lifecycle recovery')
        self.file('usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py',b'SETUP_RECOVERY_PROTOCOL = 1\n')
        self.file('usr/lib/monitorize-remote-login/monitorize/remote_login/live_setup.py',b'LIVE_ENROLLMENT_PROTOCOL = 0\n')
        self.assertFalse(self.live()['available'])

    def test_explains_missing_assets_host_and_incomplete_installation(self):
        self.assertEqual(self.result()['reason'],'missing_assets')
        self.assets();self.assertEqual(self.result()['reason'],'host_missing')
        self.file('usr/lib/monitorize-vkms/monitorize_vkms/helper.py')
        self.assertEqual(self.result()['reason'],'host_incomplete')

    def test_protocol_floor_engine_and_matching_helpers_are_required(self):
        self.assets();self.host()
        self.manifest['remote_login_protocols']['idle_display']=0;self.write_manifest()
        self.assertEqual(self.result()['reason'],'host_incompatible')
        self.manifest['remote_login_protocols']['idle_display']=1;self.write_manifest()
        self.assertEqual(self.result()['reason'],'engine_incompatible')
        engine=b'\x7fELF\0MONITORIZE_CONTROL_PROTOCOL=1\0MONITORIZE_STRICT_KMS_OUTPUT_MISSING\0'
        self.file('usr/libexec/monitorize/sunshine',engine)
        self.assertEqual(self.result()['reason'],'ready')
        self.file('usr/libexec/monitorize-vkms/monitorize-vkms-helper',b'old code')
        self.assertEqual(self.result()['reason'],'host_incomplete')

    def test_two_and_three_component_release_versions_share_the_same_floor(self):
        self.assets();self.host()
        self.file('usr/libexec/monitorize/sunshine',b'\x7fELF\0MONITORIZE_CONTROL_PROTOCOL=1\0MONITORIZE_STRICT_KMS_OUTPUT_MISSING\0')
        for version, expected in (('1.0', True), ('1.0.0', True), ('0.3', True), ('0.3.0', True),
                                  ('0.2', False), ('1', False), ('1.0.0.0', False), ('1.0rc1', False)):
            with self.subTest(version=version):
                self.file('usr/lib/monitorize-vkms/VERSION', (version+'\n').encode())
                self.manifest['version']=version;self.write_manifest()
                self.assertEqual(self.result()['available'], expected)

    def test_malformed_writable_or_symlinked_manifest_is_unavailable(self):
        self.assets();self.host()
        path=self.file('usr/share/monitorize-vkms/capabilities.json',b'not json')
        self.assertFalse(self.result()['available'])
        self.write_manifest();path.chmod(0o666)
        self.assertFalse(self.result()['available'])
        path.unlink();path.symlink_to(self.root/'elsewhere')
        self.assertFalse(self.result()['available'])

    def test_two_and_three_part_versions_preserve_version_floor(self):
        self.assets();self.host()
        self.file('usr/libexec/monitorize/sunshine',b'\x7fELF\0MONITORIZE_CONTROL_PROTOCOL=1\0MONITORIZE_STRICT_KMS_OUTPUT_MISSING\0')
        for version,expected in (('1.0','ready'),('1.0.0','ready'),('0.3','ready'),
                                 ('0.3.0','ready'),('0.2.9','host_incompatible'),
                                 ('1.0bad','host_incompatible'),('1','host_incompatible'),
                                 ('1.0.0.0','host_incompatible')):
            with self.subTest(version=version):
                self.file('usr/lib/monitorize-vkms/VERSION',(version+'\n').encode())
                self.manifest['version']=version;self.write_manifest()
                self.assertEqual(self.result()['reason'],expected)

    def test_messages_identify_version_metadata_and_missing_protocol_separately(self):
        self.assets();self.host()
        self.manifest['version']='1.0';self.write_manifest()
        self.assertIn('disagree',self.result()['message'])
        self.file('usr/lib/monitorize-vkms/VERSION',b'1.0\n')
        self.manifest['remote_login_protocols']['live_enrollment']=1;self.write_manifest()
        self.assertEqual(self.result()['reason'],'engine_incompatible')
        self.manifest['remote_login_protocols']['idle_display']=True;self.write_manifest()
        self.assertIn('idle_display',self.result()['message'])
        self.assertNotIn('boot_display,',self.result()['message'])
        self.manifest['version']='0.2.9';self.write_manifest()
        self.file('usr/lib/monitorize-vkms/VERSION',b'0.2.9\n')
        self.assertIn("version '0.2.9'",self.result()['message'])

    def test_non_object_manifest_is_unavailable_instead_of_crashing(self):
        self.assets();self.host()
        self.file('usr/share/monitorize-vkms/capabilities.json',b'[]')
        self.assertEqual(self.result()['reason'],'host_incompatible')
