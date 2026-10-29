"""Source rebuilds must not silently leave the privileged engine behind."""
import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

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

    def test_enrolled_source_update_stages_exact_desktop_binary_and_requires_install(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        self.source.write_bytes(b'\x7fELF MONITORIZE_CONTROL_PROTOCOL=1 MONITORIZE_STRICT_KMS_OUTPUT_MISSING new')
        assets = self.root / 'assets'
        (assets / 'web').mkdir(parents=True)
        (assets / 'web/index.html').write_text('web')
        stage = self.root / 'stage'
        self.assertEqual(module.check_update(self.source, assets, settings=settings,
            installed=self.installed, destination=stage), 1)
        self.assertEqual((stage / 'usr/libexec/monitorize/sunshine').read_bytes(), self.source.read_bytes())
        self.assertEqual(self.installed.read_bytes(), b'\x7fELF old engine')
        self.assertEqual(json.loads(settings.read_text())['enabled'], True)

    def test_disabled_or_matching_enrollment_needs_no_stage(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        stage = self.root / 'stage'
        for values in ({'enabled': False}, {'enabled': True, 'uid': -1}):
            settings.write_text(json.dumps(values))
            self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
                installed=self.installed, destination=stage), 0)
            self.assertFalse(stage.exists())
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        self.installed.write_bytes(self.source.read_bytes())
        self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
            installed=self.installed, destination=stage), 0)
        self.assertFalse(stage.exists())

    def test_native_owned_engine_requires_package_upgrade_without_staging(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text(json.dumps({'enabled': True, 'uid': os.getuid()}))
        self.source.write_bytes(b'\x7fELF MONITORIZE_CONTROL_PROTOCOL=1 MONITORIZE_STRICT_KMS_OUTPUT_MISSING')
        assets = self.root / 'assets'
        (assets / 'web').mkdir(parents=True)
        (assets / 'web/index.html').write_text('web')
        stage = self.root / 'stage'
        output = io.StringIO()
        # Only the external package database is simulated; real staging must not run.
        with patch('shutil.which', return_value='/usr/bin/pacman'), \
             patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'monitorize\n', '')), \
             redirect_stdout(output):
            result = module.check_update(self.source, assets, settings=settings,
                installed=self.installed, destination=stage)
        self.assertEqual(result, 1)
        self.assertFalse(stage.exists())
        self.assertIn('package manager', output.getvalue())

    def test_corrupt_enrollment_is_not_silently_reported_as_complete(self):
        spec = importlib.util.spec_from_file_location('source_update', ROOT / 'scripts/check-source-remote-login.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = self.root / 'remote-login.json'
        settings.write_text('{')
        self.assertEqual(module.check_update(self.source, self.root / 'assets', settings=settings,
            installed=self.installed, destination=self.root / 'stage'), 1)
        self.assertFalse((self.root / 'stage').exists())
