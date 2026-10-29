"""Repair authorization, untrusted inputs and lifecycle failure boundaries."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import remote_login_repair as client

ROOT = Path(__file__).resolve().parents[2]


def helper():
    spec = importlib.util.spec_from_file_location('repair_fixture', ROOT / 'packaging/remote-login/repair.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.helper = helper()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_snapshot_rejects_symlinks_nonregular_files_and_writable_inputs(self):
        source = self.root / 'source'
        source.write_text('data')
        target = self.root / 'copy'
        link = self.root / 'link'
        link.symlink_to(source)
        with self.assertRaises(OSError):
            self.helper.snapshot_file(link, target, os.getuid())
        source.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.helper.snapshot_file(source, target, os.getuid())
        self.assertFalse(target.exists())
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            self.helper.snapshot_file(fifo, target, os.getuid())

    def test_snapshot_is_private_and_bounded(self):
        source = self.root / 'source'
        source.write_bytes(b'abc')
        target = self.root / 'copy'
        self.assertEqual(self.helper.snapshot_file(source, target, os.getuid()), 3)
        self.assertEqual(target.read_bytes(), b'abc')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(ValueError):
            self.helper.snapshot_file(source, self.root / 'small', os.getuid(), limit=2)

    def test_source_stage_cannot_touch_host_or_follow_directory_symlinks(self):
        stage = self.root / 'stage'
        stage.mkdir()
        (stage / 'link').symlink_to(self.root, target_is_directory=True)
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.helper.snapshot_stage(stage, self.root / 'copy', os.getuid(), deployer)
        (stage / 'link').unlink()
        (stage / 'host.py').write_text('untrusted')
        deployer.allowed.return_value = True
        deployer.HOST_FILES = {'host.py'}
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            self.helper.snapshot_stage(stage, self.root / 'copy', os.getuid(), deployer)
        deployer.validate_bundle.assert_not_called()

    def test_complete_bundle_survives_snapshot_and_transaction_without_changing_enrollment(self):
        from linux.tests.test_remote_login_deployment import DeploymentTests
        fixture = DeploymentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.complete_bundle()
        # Host support is already installed independently of the app stage.
        for source in list(fixture.stage.rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(fixture.stage).as_posix()
            if relative in fixture.module.HOST_FILES or relative.startswith('usr/lib/monitorize-vkms/'):
                target = fixture.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
        settings = fixture.root / 'etc/monitorize/remote-login.json'
        settings.parent.mkdir(parents=True)
        original = json.dumps({'enabled': True, 'uid': os.getuid(), 'pairing': 'fixture'})
        settings.write_text(original)
        validate = fixture.module.validate_bundle
        with patch.object(fixture.module, 'validate_bundle', side_effect=lambda stage: validate(stage, host_root=fixture.root)), \
             patch.object(fixture.module, 'package_owner', return_value=None):
            snapshot = self.helper.snapshot_stage(fixture.stage, self.root / 'snapshot', os.getuid(), fixture.module)
            backup = fixture.installer.install(snapshot)
        self.assertTrue((backup / 'manifest.json').is_file())
        self.assertEqual(settings.read_text(), original)
        self.assertEqual((fixture.root / fixture.module.ENGINE).read_bytes(), (fixture.stage / fixture.module.ENGINE).read_bytes())
        repair = fixture.root / 'usr/libexec/monitorize/remote-login/repair.py'
        self.assertEqual(stat.S_IMODE(repair.stat().st_mode), 0o755)

    def test_source_repair_refuses_package_owned_files_before_pausing(self):
        stage = self.root / 'stage'
        stage.mkdir()
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        deployer.Installer.return_value.assert_unmanaged.side_effect = ValueError('package owned')
        with patch.object(self.helper, 'guard') as guard:
            with self.assertRaisesRegex(ValueError, 'package owned'):
                self.helper.repair_source(stage, deployer)
        guard.assert_not_called()
        deployer.Installer.return_value.install.assert_not_called()

    def test_source_failure_resumes_existing_services(self):
        stage = self.root / 'stage'
        stage.mkdir()
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        deployer.Installer.return_value.install.side_effect = RuntimeError('rolled back')
        with patch.object(self.helper, 'guard') as guard, patch.object(self.helper, 'run', return_value='loaded'), \
             patch.object(self.helper, 'setup_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(RuntimeError, 'rolled back'):
                self.helper.repair_source(stage, deployer)
        self.assertEqual([call.args for call in guard.call_args_list], [('--prepare', 'upgrade'), ('--resume',)])

    def test_failed_rollback_leaves_services_paused(self):
        stage = self.root / 'stage'
        stage.mkdir()
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        error = RuntimeError('rollback needs attention; backup retained')
        error.rollback_failed = True
        deployer.Installer.return_value.install.side_effect = error
        with patch.object(self.helper, 'guard') as guard, patch.object(self.helper, 'run', return_value='loaded'), \
             patch.object(self.helper, 'setup_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(RuntimeError, 'Services remain paused'):
                self.helper.repair_source(stage, deployer)
        guard.assert_called_once_with('--prepare', 'upgrade')

    def test_individually_owned_asset_is_not_replaced_by_source_repair(self):
        stage = self.root / 'stage'
        stage.mkdir()
        (stage / 'asset').write_text('source')
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        deployer.package_owner.return_value = 'another-package'
        with patch.object(self.helper, 'guard') as guard:
            with self.assertRaisesRegex(ValueError, 'belongs to a package'):
                self.helper.repair_source(stage, deployer)
        guard.assert_not_called()

    def test_connected_client_rejection_does_not_install_or_force_disconnect(self):
        stage = self.root / 'stage'
        stage.mkdir()
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        with patch.object(self.helper, 'guard', side_effect=RuntimeError('client connected')) as guard:
            with self.assertRaisesRegex(RuntimeError, 'client connected'):
                self.helper.repair_source(stage, deployer)
        guard.assert_called_once_with('--prepare', 'upgrade')
        deployer.Installer.return_value.install.assert_not_called()

    def test_success_returns_backup_and_resumes(self):
        stage = self.root / 'stage'
        stage.mkdir()
        deployer = Mock()
        deployer.Installer.return_value = Mock(spec=['assert_unmanaged', 'install'])
        deployer.Installer.return_value.install.return_value = '/var/lib/monitorize/backup'
        with patch.object(self.helper, 'guard') as guard, patch.object(self.helper, 'run', return_value='loaded'), \
             patch.object(self.helper, 'setup_lock', return_value=nullcontext()):
            result = self.helper.repair_source(stage, deployer)
        self.assertTrue(result['success'])
        self.assertEqual(result['backup'], '/var/lib/monitorize/backup')
        self.assertEqual(guard.call_args.args, ('--resume',))

    def test_package_identity_is_checked_before_a_transaction(self):
        for manager in ('pacman', 'deb', 'rpm'):
            with self.subTest(manager=manager), patch.object(self.helper, 'run', return_value='other-package'), \
                 patch.object(self.helper, 'guard') as guard:
                with self.assertRaises(ValueError):
                    self.helper.repair_package(self.root / 'package', manager)
                guard.assert_not_called()

    def test_arch_package_uses_manager_without_overwrite_or_shell(self):
        package = self.root / 'package'
        with patch.object(self.helper, 'run', return_value='monitorize'):
            command = self.helper.package_command(package, 'pacman')
        self.assertEqual(command, ['/usr/bin/pacman', '-U', '--noconfirm', str(package)])

    def test_package_failure_still_attempts_resume(self):
        with patch.object(self.helper, 'package_command', return_value=['manager']), \
             patch.object(self.helper, 'guard') as guard, patch.object(self.helper, 'run', side_effect=RuntimeError('transaction failed')):
            with self.assertRaisesRegex(RuntimeError, 'transaction failed'):
                self.helper.repair_package(self.root / 'package', 'pacman')
        self.assertEqual([call.args for call in guard.call_args_list], [('--prepare', 'upgrade'), ('--resume',)])

    def test_cancelling_package_selection_does_not_launch_polkit(self):
        with patch.object(client, 'repair_options', return_value={'repair_kind': 'pacman'}), \
             patch.object(client.subprocess, 'run') as run:
            result = client.repair_remote_login('')
        self.assertTrue(result['cancelled'])
        run.assert_not_called()

    def test_rpm_ownership_is_found_when_debian_tools_are_also_installed(self):
        def query(command, **kwargs):
            return subprocess.CompletedProcess(command, 0 if command[0] == '/usr/bin/rpm' else 1, '', '')
        with patch.object(client.Path, 'is_file', return_value=True), \
             patch.object(client.subprocess, 'run', side_effect=query):
            self.assertEqual(client.owning_package_manager(Path('/usr/libexec/monitorize/sunshine')), 'rpm')

    def test_source_launch_with_packaged_assets_uses_native_repair(self):
        with patch.object(client, 'owning_package_manager', return_value='pacman'), \
             patch.object(client, 'package_manager', return_value='pacman'), \
             patch.object(client.Path, 'exists', return_value=True), \
             patch.object(client.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch('monitorize.remote_login.capability.source_engine_update_error', return_value='mismatch'):
            result = client.repair_options(repository=ROOT)
        self.assertEqual(result['repair_kind'], 'pacman')
        self.assertTrue(result['repair_needed'])

    def test_source_unowned_install_supports_bootstrapping_without_installed_helper(self):
        with patch.object(client, 'owning_package_manager', return_value=''), \
             patch.object(client.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch('monitorize.remote_login.capability.source_engine_update_error', return_value='mismatch'):
            result = client.repair_options(repository=ROOT)
        self.assertEqual(result['repair_kind'], 'source')

    def test_polkit_cancellation_is_a_failure_and_does_not_report_success(self):
        with patch.object(client, 'repair_options', return_value={'repair_kind': 'pacman'}), \
             patch.object(client.subprocess, 'run', return_value=subprocess.CompletedProcess([], 126, '', '')) as run:
            package = self.root / 'monitorize.pkg.tar.zst'
            package.touch()
            result = client.repair_remote_login(str(package))
        self.assertFalse(result['success'])
        self.assertIn('--kind', run.call_args.args[0])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_source_repair_stages_selected_engine_and_uses_polkit_without_terminal(self):
        repository = self.root / 'repository'
        (repository / 'scripts').mkdir(parents=True)
        (repository / 'scripts/stage-remote-login.py').symlink_to(ROOT / 'scripts/stage-remote-login.py')
        binary = repository / 'external/sunshine/build/sunshine'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'\x7fELF MONITORIZE_CONTROL_PROTOCOL=1 MONITORIZE_STRICT_KMS_OUTPUT_MISSING')
        assets = repository / 'assets'
        (assets / 'web').mkdir(parents=True)
        (assets / 'web/index.html').write_text('web')
        absent = self.root / 'absent-helper'
        seen = []
        def launch(command, **kwargs):
            stage = Path(command[command.index('--input') + 1])
            seen.append(stage)
            self.assertEqual((stage / 'usr/libexec/monitorize/sunshine').read_bytes(), binary.read_bytes())
            self.assertTrue((stage / 'usr/libexec/monitorize/remote-login/repair.py').is_file())
            self.assertEqual(command[:3], ['/usr/bin/pkexec', '/usr/bin/python3', '-I'])
            self.assertEqual(command[command.index('--kind') + 1], 'source')
            return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'message': 'Repaired'}), '')
        with patch.object(client, 'HELPER', absent), \
             patch.object(client, 'repair_options', return_value={'repair_kind': 'source'}), \
             patch.object(client.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch('monitorize.platform.sunshine_service.get_sunshine_candidates', return_value=[[str(binary)]]), \
             patch('monitorize.platform.sunshine_service._sunshine_bundles', return_value=[(str(binary), str(assets))]), \
             patch.object(client.subprocess, 'run', side_effect=launch):
            result = client.repair_remote_login(repository=repository)
        self.assertTrue(result['success'])
        self.assertFalse(seen[0].exists())

    def test_failed_preparation_cancels_only_its_own_new_receipt(self):
        with patch.object(self.helper.Path, 'exists', side_effect=[False, True]), \
             patch.object(self.helper, 'guard', side_effect=[RuntimeError('pause failed'), None]) as guard:
            with self.assertRaisesRegex(RuntimeError, 'pause failed'):
                self.helper.prepare_upgrade()
        self.assertEqual([call.args for call in guard.call_args_list], [('--prepare', 'upgrade'), ('--resume',)])

    def test_missing_polkit_disables_repair_with_explanation(self):
        with patch.object(client, 'owning_package_manager', return_value=''), \
             patch.object(client.shutil, 'which', return_value=None), \
             patch('monitorize.remote_login.capability.source_engine_update_error', return_value=''):
            self.assertFalse(client.repair_options(repository=ROOT)['repair_kind'])


if __name__ == '__main__':
    unittest.main()
