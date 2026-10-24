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

    def test_malformed_writable_or_symlinked_manifest_is_unavailable(self):
        self.assets();self.host()
        path=self.file('usr/share/monitorize-vkms/capabilities.json',b'not json')
        self.assertFalse(self.result()['available'])
        self.write_manifest();path.chmod(0o666)
        self.assertFalse(self.result()['available'])
        path.unlink();path.symlink_to(self.root/'elsewhere')
        self.assertFalse(self.result()['available'])
