import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[2]


class PackageGuardTests(unittest.TestCase):
    def test_enabled_enrollment_and_protected_lease_block_package_change(self):
        path=ROOT/'packaging/remote-login/package-guard.py'
        spec=importlib.util.spec_from_file_location('remote_guard',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            module.assert_update_safe(root,owner_uid=os.getuid())
            config=root/'etc/monitorize/remote-login.json';config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'enabled':True}));config.chmod(0o600)
            with self.assertRaises(RuntimeError):module.assert_update_safe(root,owner_uid=os.getuid())
            config.write_text(json.dumps({'enabled':False}))
            module.assert_update_safe(root,owner_uid=os.getuid())
            state=root/'run/monitorize-vkms/primary.json';state.parent.mkdir(parents=True)
            state.write_text(json.dumps({'protected':True}));state.chmod(0o600)
            with self.assertRaises(RuntimeError):module.assert_update_safe(root,owner_uid=os.getuid())
            state.write_text('not json')
            with self.assertRaises(ValueError):module.assert_update_safe(root,owner_uid=os.getuid())
