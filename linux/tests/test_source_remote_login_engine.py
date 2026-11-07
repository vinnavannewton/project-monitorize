"""Source rebuilds must not silently leave the privileged engine behind."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class SourceEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'linux/venv/bin/sunshine'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'\x7fELF new engine')
        self.source.chmod(0o755)
        from monitorize.platform import sunshine_service
        finder=patch.object(sunshine_service,'__file__',str(self.root/'linux/monitorize/platform/sunshine_service.py'))
        finder.start();self.addCleanup(finder.stop)
        installer = self.root / 'linux/scripts/install.sh'
        installer.parent.mkdir(parents=True)
        installer.touch()
        self.installed = self.root / 'installed'
        self.installed.write_bytes(b'\x7fELF old engine')

    def test_same_size_engine_change_is_detected_and_repair_clears_error(self):
        from monitorize.remote_login.capability import source_engine_update_error
        error = source_engine_update_error(repository=self.root, installed=self.installed)
        self.assertIn('remote-login', error)
        self.assertIn('Repair remote login', error)
        self.installed.write_bytes(self.source.read_bytes())
        self.assertEqual(source_engine_update_error(repository=self.root, installed=self.installed), '')
        self.source.write_bytes(b'\x7fELF next build')
        self.assertTrue(source_engine_update_error(repository=self.root, installed=self.installed))

    def test_native_install_does_not_require_a_source_build(self):
        from monitorize.remote_login.capability import source_engine_update_error
        self.source.unlink()
        self.assertEqual(source_engine_update_error(repository=self.root, installed=self.installed), '')

    def test_alternate_selected_build_is_compared_instead_of_matching_venv(self):
        from monitorize.remote_login.capability import source_engine_update_error
        self.installed.write_bytes(self.source.read_bytes())
        (self.root/'external/sunshine').mkdir(parents=True)
        alternate=self.root/'sunshine-build/sunshine'
        alternate.parent.mkdir();alternate.write_bytes(b'\x7fELF alternate');alternate.chmod(0o755)
        error=source_engine_update_error(repository=self.root,installed=self.installed)
        self.assertIn(str(alternate),error)
        self.source.unlink();alternate.unlink()
        fallback=self.root/'external/sunshine/build/sunshine'
        fallback.parent.mkdir();fallback.write_bytes(b'\x7fELF fallback');fallback.chmod(0o755)
        self.assertIn(str(fallback),source_engine_update_error(repository=self.root,installed=self.installed))

    def test_missing_privileged_engine_has_actionable_source_repair(self):
        from monitorize.remote_login.capability import source_engine_update_error
        self.installed.unlink()
        self.assertIn('Repair remote login', source_engine_update_error(repository=self.root, installed=self.installed))

    def test_enrolled_source_update_deploys_exact_build_and_checks_result(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        assets = self.root / 'assets'
        with patch('monitorize.platform.remote_login_repair.repair_remote_login',
                   return_value={'success': True, 'message': 'Repaired'}) as repair:
            self.assertEqual(module.check_update(self.source, assets, settings=settings, installed=self.installed), 1)
            repair.assert_called_once_with(repository=ROOT, terminal=True, bundle=(self.source, assets))
            def deploy(**kwargs):
                binary, _ = kwargs['bundle']
                self.installed.write_bytes(binary.read_bytes())
                return {'success': True, 'message': 'Repaired'}

            repair.reset_mock(side_effect=True)
            repair.side_effect = deploy
            self.assertEqual(module.check_update(self.source, assets, settings=settings, installed=self.installed), 0)
            self.assertEqual(module.check_update(self.source, assets, settings=settings, installed=self.installed), 0)
            repair.assert_called_once()
        self.assertTrue(json.loads(settings.read_text())['enabled'])

    def test_disabled_or_matching_enrollment_needs_no_stage(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        for values in ({'enabled': False}, {'enabled': True, 'uid': -1}):
            settings.write_text(json.dumps(values))
            self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
                installed=self.installed), 0)
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        self.installed.write_bytes(self.source.read_bytes())
        self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
            installed=self.installed), 0)

    def test_authorization_failure_is_reported_without_claiming_success(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        with patch('monitorize.platform.remote_login_repair.repair_remote_login',
                   return_value={'success': False, 'message': 'Administrator authorization failed.'}), \
             patch('sys.stderr', new_callable=io.StringIO) as errors:
            result = module.check_update(self.source, self.root / 'assets', settings=settings,
                                         installed=self.installed)
        self.assertEqual(result, 1)
        self.assertIn('Administrator authorization failed', errors.getvalue())
        self.assertEqual(self.installed.read_bytes(), b'\x7fELF old engine')

    def test_corrupt_enrollment_is_not_silently_reported_as_complete(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text('{')
        self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
            installed=self.installed), 1)
