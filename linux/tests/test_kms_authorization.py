"""KMS repair scope, asynchronous cancellation and privileged file validation."""
import copy
import ctypes
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
from monitorize.desktop import kms_authorization as kms
from monitorize.platform import sunshine_service as service

ROOT = Path(__file__).resolve().parents[2]


def bundle(second=False):
    return {"streaming_backend": "sunshine", "primary": {
        "display_type": "Extend", "virtual_display_creator": "native", "sunshine_capture": "kms"},
        "second": {"enabled": second, "sunshine_capture": "kms"}}


class KmsStatusTests(unittest.TestCase):
    def test_inheritable_only_capability_does_not_pass_permission_verification(self):
        with patch.object(service.shutil, 'which', return_value='/usr/sbin/getcap'), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/test/sunshine cap_sys_admin=i', '')):
            self.assertEqual(service.get_sunshine_kms_setup_status(binary='/test/sunshine')['code'], 'missing_capability')

    def test_failed_getcap_is_not_a_missing_capability(self):
        with patch.object(service.shutil, 'which', return_value='/usr/sbin/getcap'), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'read denied')):
            result = service.get_sunshine_kms_setup_status(binary='/test/sunshine')
        self.assertEqual(result['code'], 'check_failed')
        self.assertIn('read denied', result['message'])

    def test_auto_kms_mirror_display_only_and_remote_scope(self):
        raw = bundle(True)
        with patch('monitorize.desktop.streaming_controller._sunshine_capture_method', return_value='kms'):
            raw['primary']['sunshine_capture'] = 'auto'
            self.assertEqual(kms.capture_requirements(raw, 'niri'), [(1, None), (2, None)])
            raw['primary']['display_type'] = 'Mirror'
            self.assertEqual(kms.capture_requirements(raw, 'niri'), [(1, None)])
        self.assertEqual(kms.capture_requirements(raw, 'kde', True), [(1, '/usr/libexec/monitorize/sunshine')])
        raw['streaming_backend'] = 'none'
        self.assertEqual(kms.capture_requirements(raw, 'kde'), [])


class KmsHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'linux/venv/bin/sunshine'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'\x7fELF' + b'MONITORIZE_CODEC_PROBE_FAILED')
        self.binary.chmod(0o755)
        source = self.root / 'linux/monitorize/platform/sunshine_service.py'
        source.parent.mkdir(parents=True)
        source.touch()
        (self.root / '.gitmodules').touch()
        self.grant = runpy.run_path(str(ROOT / 'packaging/common/monitorize-system-setup'))['grant_kms_capability']
        self.env = patch.dict(os.environ, PKEXEC_UID=str(os.getuid()))
        self.env.start(); self.addCleanup(self.env.stop)

    def apply(self, **overrides):
        info = self.binary.stat()
        return self.grant(str(self.binary), overrides.get('device', info.st_dev), overrides.get('inode', info.st_ino))

    def test_adds_only_permitted_sys_admin_using_the_open_file_descriptor(self):
        lib = Mock()
        lib.cap_get_fd.side_effect = [123, 456]
        lib.cap_set_flag.return_value = lib.cap_set_fd.return_value = 0
        def read_flag(_caps, number, kind, flag):
            self.assertEqual((number, kind), (21, 1))
            flag._obj.value = 1
            return 0
        lib.cap_get_flag.side_effect = read_flag
        with patch.object(ctypes, 'CDLL', return_value=lib), patch.object(subprocess, 'run') as execute:
            self.assertTrue(self.apply()['success'])
        args = lib.cap_set_flag.call_args.args
        self.assertEqual((args[1], args[2], args[3]._obj.value, args[4]), (1, 1, 21, 1))
        self.assertEqual(lib.cap_set_fd.call_args.args[1], 123)
        self.assertEqual(lib.cap_free.call_count, 2)
        execute.assert_not_called()

    def test_stale_inode_world_writable_and_non_elf_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.apply(inode=self.binary.stat().st_ino + 1)
        self.binary.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            self.apply()
        self.binary.chmod(0o755)
        self.binary.write_text('#!/bin/sh\nMONITORIZE_CODEC_PROBE_FAILED')
        with self.assertRaisesRegex(RuntimeError, 'ELF'):
            self.apply()

    def test_arbitrary_files_symlinks_and_hardlinks_are_rejected(self):
        other = self.root / 'other'
        other.write_bytes(self.binary.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'only a Monitorize'):
            self.grant(str(other), other.stat().st_dev, other.stat().st_ino)
        alias = self.binary.with_name('alias')
        alias.symlink_to(self.binary)
        with self.assertRaisesRegex(RuntimeError, 'symlinks'):
            self.grant(str(alias), self.binary.stat().st_dev, self.binary.stat().st_ino)
        alias.unlink(); os.link(self.binary, alias)
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            self.apply()


class KmsAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / 'sunshine'
        self.binary.write_text('test')
        self.auth = kms.KmsAuthorization()
        self.addCleanup(self.auth.cancel)
        self.results = []
        self.calls = 0

    def status(self, *_args):
        self.calls += 1
        return {'code': 'missing_capability' if self.calls == 1 else 'ready',
                'binary': str(self.binary), 'message': 'KMS permission missing' if self.calls == 1 else ''}

    def command(self, path, success=True):
        info = Path(path).stat()
        script = "import time;time.sleep(0.06);print(" + repr(json.dumps({'success': success, 'message': 'Authorization was cancelled.'})) + ")"
        return [sys.executable, '-c', script, '--kms-device', str(info.st_dev), '--kms-inode', str(info.st_ino)], None

    def settle(self):
        loop = QEventLoop(); QTimer.singleShot(180, loop.quit); loop.exec()

    def test_success_verifies_permission_and_resumes_once_without_blocking_qt(self):
        tick = []
        with patch.object(kms, 'get_sunshine_kms_setup_status', side_effect=self.status), \
             patch.object(kms, 'kms_setup_command', side_effect=self.command) as commands:
            self.assertFalse(self.auth.ensure(bundle(), 'kde', False, lambda *args: self.results.append(args)))
            self.assertTrue(self.auth.busy)
            QTimer.singleShot(0, lambda: tick.append(True))
            self.settle()
        self.assertTrue(tick)
        self.assertEqual(self.results, [(True, '', 1)])
        self.assertEqual(commands.call_count, 1)

    def test_cancellation_does_not_retry_or_prompt_again(self):
        with patch.object(kms, 'get_sunshine_kms_setup_status', side_effect=self.status), \
             patch.object(kms, 'kms_setup_command', side_effect=lambda p: self.command(p, False)) as commands:
            self.auth.ensure(bundle(), 'kde', False, lambda *args: self.results.append(args))
            self.settle()
        self.assertEqual(self.results, [(False, 'Authorization was cancelled.', 1)])
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(self.calls, 1)

    def test_stop_during_authentication_never_resumes(self):
        with patch.object(kms, 'get_sunshine_kms_setup_status', side_effect=self.status), \
             patch.object(kms, 'kms_setup_command', side_effect=self.command):
            self.auth.ensure(bundle(), 'kde', False, lambda *args: self.results.append(args))
            self.auth.cancel()
            self.settle()
        self.assertFalse(self.results)
        self.assertFalse(self.auth.busy)

    def test_replacing_the_binary_during_authorization_requires_a_new_start(self):
        with patch.object(kms, 'get_sunshine_kms_setup_status', side_effect=self.status), \
             patch.object(kms, 'kms_setup_command', side_effect=self.command):
            self.auth.ensure(bundle(), 'kde', False, lambda *args: self.results.append(args))
            self.binary.rename(self.binary.with_suffix('.old'))
            self.binary.write_text('replacement')
            self.settle()
        self.assertFalse(self.results[0][0])
        self.assertIn('changed', self.results[0][1])

    def test_shared_binary_for_two_displays_is_authorized_once(self):
        def status(*args):
            result = self.status(*args)
            result['code'] = 'missing_capability' if self.calls <= 2 else 'ready'
            return result
        with patch.object(kms, 'get_sunshine_kms_setup_status', side_effect=status), \
             patch.object(kms, 'kms_setup_command', side_effect=self.command) as commands:
            self.auth.ensure(bundle(True), 'kde', False, lambda *args: self.results.append(args))
            self.settle()
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(self.results, [(True, '', 1)])

    def test_failed_verification_stops_without_reauthorizing(self):
        with patch.object(kms, 'get_sunshine_kms_setup_status', return_value={
                'code': 'missing_capability', 'binary': str(self.binary), 'message': 'Still missing permission'}), \
             patch.object(kms, 'kms_setup_command', side_effect=self.command) as commands:
            self.auth.ensure(bundle(), 'kde', False, lambda *args: self.results.append(args))
            self.settle()
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(self.results, [(False, 'Still missing permission', 1)])


class KmsStartIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        from linux.tests.test_ui_async import UiAsyncTest
        UiAsyncTest.setUp(self)
        self.backend = UiAsyncTest.make_backend(self)
        self.backend.configuration_state.applied['streaming_backend'] = 'sunshine'

    def test_explicit_start_waits_for_authorization_then_retries_once(self):
        backend = self.backend
        with patch.object(backend._kms_authorization, 'ensure', return_value=False) as ensure, \
             patch.object(backend, '_start_session_checked') as launch:
            backend.startSession()
            launch.assert_not_called()
            completed = ensure.call_args.args[-1]
            completed(True, '', 1)
            launch.assert_called_once_with()

    def test_stop_and_configuration_changes_invalidate_pending_start(self):
        backend = self.backend
        with patch.object(backend._kms_authorization, 'ensure', return_value=False) as ensure, \
             patch.object(backend, '_start_session_checked') as launch:
            backend.startSession()
            completed = ensure.call_args.args[-1]
            backend.stopSession()
            completed(True, '', 1)
            launch.assert_not_called()
            backend.startSession()
            backend.configuration_state.applied['primary']['sunshine_capture'] = 'portal'
            ensure.call_args.args[-1](True, '', 1)
            launch.assert_not_called()
            self.assertIn('Configuration changed', backend.displayError(1))

    def test_background_listener_never_enters_authorization(self):
        backend = self.backend
        backend._streaming_backend = 'sunshine'
        with patch.object(backend.session, 'has_client_cards', return_value=True), \
             patch.object(backend, '_start_session_checked'), \
             patch.object(backend._kms_authorization, 'ensure') as ensure:
            backend.startConfiguredListeners()
        ensure.assert_not_called()
