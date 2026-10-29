import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from monitorize.platform import polkit_agent

ROOT=Path(__file__).resolve().parents[2]


def load(path):
    spec=importlib.util.spec_from_file_location('recovery_test_'+path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class AuthorizationTests(unittest.TestCase):
    def test_only_missing_agent_retries_once(self):
        missing=subprocess.CompletedProcess([],127,'','No authentication agent found')
        ready=subprocess.CompletedProcess([],0,'{}','')
        with patch.object(polkit_agent.subprocess,'run',side_effect=[missing,ready]) as run,patch.object(polkit_agent,'start_agent',return_value=True) as start,patch.object(polkit_agent.time,'sleep'):
            self.assertIs(polkit_agent.run_authorized(['pkexec','fixed']),ready)
            self.assertEqual(run.call_count,2);start.assert_called_once()

    def test_cancel_denial_and_unknown_outcome_never_replay(self):
        for code,detail in ((126,'No authentication agent found'),(1,'Not authorized'),(1,'operation may already have completed')):
            with self.subTest(code=code,detail=detail),patch.object(polkit_agent.subprocess,'run',return_value=subprocess.CompletedProcess([],code,'',detail)) as run,patch.object(polkit_agent,'start_agent') as start:
                polkit_agent.run_authorized(['pkexec','fixed'])
                run.assert_called_once();start.assert_not_called()


class RecoveryAssetsTests(unittest.TestCase):
    def setUp(self):self.module=load(ROOT/'packaging/common/system_recovery.py')

    def test_missing_asset_requires_matching_template_integrity_and_ownership(self):
        m=self.module;data=b'rule';manifest=json.dumps({'uinput.rules':hashlib.sha256(data).hexdigest()}).encode()
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'absent'
            with patch.dict(m.TARGETS,{'uinput.rules':target}),patch.object(m,'read_safe',side_effect=[manifest,data]),patch.object(m,'owner',side_effect=['monitorize','foreign']),patch.object(m,'write_owned') as write:
                with self.assertRaisesRegex(ValueError,'belong'):m.restore_asset('uinput.rules')
                write.assert_not_called()
            with patch.dict(m.TARGETS,{'uinput.rules':target}),patch.object(m,'read_safe',side_effect=[manifest,data]),patch.object(m,'owner',return_value='monitorize'),patch.object(m,'write_owned') as write:
                self.assertTrue(m.restore_asset('uinput.rules'));write.assert_called_once_with(target,data)

    def test_modified_asset_is_preserved(self):
        m=self.module;data=b'rule';manifest=json.dumps({'uinput.rules':hashlib.sha256(data).hexdigest()}).encode()
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'rule';target.write_bytes(b'custom')
            with patch.dict(m.TARGETS,{'uinput.rules':target}),patch.object(m,'read_safe',side_effect=[manifest,data,b'custom']),patch.object(m,'write_owned') as write:
                with self.assertRaisesRegex(ValueError,'modified'):m.restore_asset('uinput.rules')
                write.assert_not_called()

    def test_corrupt_template_and_unknown_target_are_rejected(self):
        m=self.module
        with patch.object(m,'read_safe',side_effect=[b'{"uinput.rules":"wrong"}',b'data']),patch.object(m,'write_owned') as write:
            with self.assertRaisesRegex(ValueError,'integrity'):m.restore_asset('uinput.rules')
            write.assert_not_called()
        with self.assertRaises(ValueError):m.restore_asset('../../etc/passwd')

    def test_network_alternative_or_admin_disable_is_preserved(self):
        m=self.module;run=Mock()
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaisesRegex(ValueError,'Another'):m.start_networkmanager(run)
            run.assert_not_called()
        run=Mock(side_effect=['loaded','disabled'])
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],3)):
            with self.assertRaisesRegex(ValueError,'configured'):m.start_networkmanager(run)
        self.assertFalse(any('start' in call.args[0] for call in run.call_args_list))

    def test_network_start_is_verified(self):
        m=self.module;run=Mock(side_effect=['loaded','enabled','','active'])
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],3)):
            self.assertTrue(m.start_networkmanager(run)['success'])
        self.assertIn('start',run.call_args_list[2].args[0])


class SourceMaintenanceTests(unittest.TestCase):
    def setUp(self):self.module=load(ROOT/'scripts/source-maintenance.py')

    def test_source_read_rejects_symlink_writable_and_hardlinked_files(self):
        m=self.module
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)):
            root=Path(temp);(root/'original').write_bytes(b'original')
            (root/'link').symlink_to(root/'original')
            with self.assertRaises(OSError):m.source_read('link',os.getuid())
            (root/'original').chmod(0o666)
            with self.assertRaises(ValueError):m.source_read('original',os.getuid())
            (root/'original').chmod(0o644);os.link(root/'original',root/'hardlink')
            with self.assertRaises(ValueError):m.source_read('hardlink',os.getuid())

    def test_cache_symlink_never_changes_outside_target(self):
        m=self.module
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)):
            root=Path(temp);cache=root/'external/sunshine/node_modules';cache.mkdir(parents=True)
            outside=root/'outside';outside.write_bytes(b'preserved');outside.chmod(0o600)
            (cache/'symlink').symlink_to(outside)
            with patch.object(m.os,'chown') as chown,patch.object(m.os,'fchown'):
                self.assertTrue(m.repair_cache(os.getuid(),os.getgid())['success'])
                chown.assert_not_called()
            self.assertEqual(outside.read_bytes(),b'preserved');self.assertEqual(outside.stat().st_mode & 0o777,0o600)

    def test_cache_hardlink_is_rejected(self):
        m=self.module
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)),patch.object(m.os,'fchown'):
            root=Path(temp);cache=root/'external/sunshine/node_modules';cache.mkdir(parents=True)
            outside=root/'outside';outside.write_bytes(b'preserved');os.link(outside,cache/'hardlink')
            with self.assertRaisesRegex(ValueError,'Unsafe'):m.repair_cache(os.getuid(),os.getgid())

    def test_bind_mount_boundary_is_rejected_before_ownership_change(self):
        m=self.module
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)):
            (Path(temp)/'external/sunshine/node_modules').mkdir(parents=True)
            with patch.object(m,'mount_id',side_effect=[1,1,1,1,1,2]),patch.object(m.os,'fchown') as change:
                with self.assertRaisesRegex(ValueError,'mount boundary'):m.repair_cache(os.getuid(),os.getgid())
                change.assert_not_called()

    def test_legacy_foreign_or_unknown_files_are_preserved(self):
        m=self.module;provider=Mock();provider.owner.return_value='foreign'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'legacy';path.write_bytes(b'unknown')
            with patch.object(m,'LEGACY',(path,)),patch.object(m,'provider',return_value=provider):
                with self.assertRaisesRegex(ValueError,'package owned'):m.cleanup_legacy()
                self.assertTrue(path.exists())
                provider.owner.return_value=None;provider.read_safe.return_value=b'unknown'
                with self.assertRaisesRegex(ValueError,'provenance'):m.cleanup_legacy()
                self.assertTrue(path.exists());provider.write_owned.assert_not_called()


class ControllerRestoreTests(unittest.TestCase):
    def test_custom_controller_requires_choice_and_keeps_backup(self):
        from monitorize.desktop import remote_login_controller as controller
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);unit=home/'systemd/user'/controller.UNIT;unit.parent.mkdir(parents=True)
            original=b'[Service]\nExecStart=custom\n';unit.write_bytes(original)
            run=Mock()
            with self.assertRaises(controller.ControllerConflict):controller.configure(True,config_home=home,run=run)
            run.assert_not_called();self.assertEqual(unit.read_bytes(),original)
            with patch.object(controller,'controller_snapshot',return_value={}):
                controller.restore_managed(True,config_home=home,run=run)
            self.assertTrue(unit.read_bytes().startswith(controller.MARKER.encode()))
            self.assertEqual(next((home/'monitorize/controller-recovery').rglob(controller.UNIT)).read_bytes(),original)


class RecoveryDeliveryTests(unittest.TestCase):
    def test_shared_native_stage_includes_module_templates_and_matching_hashes(self):
        module=load(ROOT/'packaging/remote-login/install-assets.py')
        with tempfile.TemporaryDirectory() as temp:
            stage=Path(temp);module.stage(stage)
            self.assertEqual((stage/'usr/libexec/monitorize/system_recovery.py').read_bytes(),(ROOT/'packaging/common/system_recovery.py').read_bytes())
            recovery=stage/'usr/share/monitorize/recovery';manifest=json.loads((recovery/'manifest.json').read_text())
            self.assertEqual(set(manifest),{'uinput.rules','firewalld.xml','ufw.profile'})
            for name,digest in manifest.items():self.assertEqual(hashlib.sha256((recovery/name).read_bytes()).hexdigest(),digest)
        for name in ('rpm','tumbleweed'):
            files=(ROOT/'packaging'/name/'monitorize.spec').read_text().split('%files',1)[1]
            self.assertIn('%{_libexecdir}/monitorize/system_recovery.py',files)
            self.assertIn('%{_datadir}/monitorize/recovery/',files)

    def test_native_owned_helper_cannot_be_source_bootstrapped(self):
        from monitorize.platform import system_setup
        with patch('monitorize.platform.remote_login_repair.owning_package_manager',return_value='pacman'),patch.object(system_setup.shutil,'which',return_value='/usr/bin/pkexec'):
            self.assertIsNone(system_setup.source_bootstrap_command(['--input']))


class SourceAuthorizationLauncherTests(unittest.TestCase):
    def test_launcher_uses_fixed_source_action_and_agent_retry_wrapper(self):
        module=load(ROOT/'scripts/authorize-source-maintenance.py')
        with patch('sys.argv',['launcher','cache']),patch.object(module.shutil,'which',return_value='/usr/bin/pkexec'),patch.object(module,'run_authorized',return_value=SimpleNamespace(returncode=126,stdout='',stderr='')) as authorize:
            self.assertEqual(module.main(),126)
            self.assertEqual(authorize.call_args.args[0],['/usr/bin/pkexec','/usr/bin/python3','-I',str(ROOT/'scripts/source-maintenance.py'),'cache'])
