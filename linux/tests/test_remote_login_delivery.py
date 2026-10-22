import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


class DeliveryTests(unittest.TestCase):
    def test_private_service_bundle_units_and_policy_are_staged_without_enabling(self):
        path=ROOT/'packaging/remote-login/install-assets.py'
        spec=importlib.util.spec_from_file_location('remote_assets',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            previous=os.umask(0o077)
            try:module.stage(Path(d))
            finally:os.umask(previous)
            root=Path(d)
            for relative in ('usr/lib/monitorize-remote-login/monitorize/remote_login/gdm_context.py',
                'usr/lib/monitorize-remote-login/monitorize/remote_login/gdm_probe.py',
                'usr/lib/monitorize-remote-login/monitorize/remote_login/daemon.py',
                'usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py',
                'usr/libexec/monitorize/remote-login/entry.py',
                'usr/lib/systemd/system/monitorize-vkms-boot-display.service',
                'usr/lib/systemd/system/monitorize-remote-login.service',
                'usr/share/dbus-1/system.d/io.github.vinnavannewton.MonitorizeRemoteLogin1.conf'):
                self.assertTrue((root/relative).is_file(),relative)
            self.assertFalse((root/'etc').exists())
            self.assertFalse(list(root.rglob('*.wants')))
            self.assertFalse(list(root.rglob('__pycache__')))
            for path in root.rglob('*.py'):compile(path.read_text(),str(path),'exec')
            self.assertEqual((root/'usr').stat().st_mode & 0o777,0o755)
            self.assertFalse((root/'usr/lib/monitorize-remote-login/monitorize/desktop').exists())

    def test_every_native_builder_stages_same_assets(self):
        for name in ('packaging/rpm/monitorize.spec','packaging/tumbleweed/monitorize.spec',
                     'packaging/arch/PKGBUILD','packaging/deb/common/rules.mk'):
            self.assertIn('packaging/remote-login/install-assets.py --destdir',(ROOT/name).read_text(),name)
        for phase in ('boot','desktop'):
            unit=(ROOT/f'packaging/remote-login/monitorize-remote-login-{phase}@.service').read_text()
            self.assertIn('User=%i',unit);self.assertIn('KillMode=control-group',unit)
            self.assertIn('Restart=no',unit)
        self.assertIn('--remote-login',(ROOT/'packaging/common/monitorize-system-setup').read_text())

    def test_rpm_service_manifest_is_only_in_files_section(self):
        for name in ('packaging/rpm/monitorize.spec','packaging/tumbleweed/monitorize.spec'):
            content=(ROOT/name).read_text();install=content.split('%install',1)[1].split('%check',1)[0]
            self.assertNotIn('\n%{_unitdir}/monitorize-remote-login.service\n',install)
            files=content.split('\n%files',1)[1]
            self.assertIn('%{_unitdir}/monitorize-remote-login.service',files)

    def test_source_staging_validates_engine_before_creating_output(self):
        path=ROOT/'scripts/stage-remote-login.py'
        spec=importlib.util.spec_from_file_location('source_remote_assets',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'engine';binary.write_bytes(b'not the engine');assets=root/'assets';assets.mkdir()
            output=root/'stage'
            with patch.object(module.os,'geteuid',return_value=1000):
                with self.assertRaises(ValueError):module.prepare(output,binary,assets)
            self.assertFalse(output.exists())

    def test_source_staging_can_include_host_python_without_rebuilding_module(self):
        path=ROOT/'scripts/stage-remote-login.py'
        spec=importlib.util.spec_from_file_location('source_remote_assets',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'engine';binary.write_bytes(b'engine')
            assets=root/'assets';(assets/'web').mkdir(parents=True)
            host=root/'host/monitorize_vkms';host.mkdir(parents=True)
            (host/'helper.py').write_text('BOOT_DISPLAY_PROTOCOL = 1\n')
            (host/'boot_display.py').write_text('GREETER_ON_DEMAND_PROTOCOL = 1\nIDLE_DISPLAY_PROTOCOL = 1\n')
            (host.parent/'VERSION').write_text('0.3.0\n')
            with patch.object(module.os,'geteuid',return_value=1000),patch.object(module,'exact_kms_engine',return_value=True):
                module.prepare(root/'stage',binary,assets,vkms_source=host.parent)
            self.assertTrue((root/'stage/usr/lib/monitorize-vkms/monitorize_vkms/boot_display.py').is_file())
            helper=root/'stage/usr/libexec/monitorize-vkms/monitorize-vkms-helper'
            self.assertEqual(helper.read_text(),'BOOT_DISPLAY_PROTOCOL = 1\n')
            self.assertEqual(helper.stat().st_mode & 0o777,0o755)
            self.assertFalse(list((root/'stage').rglob('*.ko')))
            self.assertEqual((root/'stage/usr/lib/firewalld/services/monitorize.xml').read_bytes(),
                             (ROOT/'packaging/fedora/monitorize.xml').read_bytes())
            self.assertTrue((root/'stage/etc/ufw/applications.d/monitorize').is_file())

    def test_source_staging_rejects_missing_idle_protocol_before_creating_output(self):
        path=ROOT/'scripts/stage-remote-login.py'
        spec=importlib.util.spec_from_file_location('source_remote_assets',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'engine';binary.write_bytes(b'engine')
            assets=root/'assets';(assets/'web').mkdir(parents=True)
            host=root/'host/monitorize_vkms';host.mkdir(parents=True)
            (host/'helper.py').write_text('BOOT_DISPLAY_PROTOCOL = 1\n')
            (host/'boot_display.py').write_text('GREETER_ON_DEMAND_PROTOCOL = 1\n')
            (host.parent/'VERSION').write_text('0.3.0\n')
            with patch.object(module.os,'geteuid',return_value=1000),patch.object(module,'exact_kms_engine',return_value=True):
                with self.assertRaisesRegex(ValueError,'idle'):
                    module.prepare(root/'stage',binary,assets,vkms_source=host.parent)
            self.assertFalse((root/'stage').exists())
