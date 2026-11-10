"""The temporary boot output experiment refuses active ownership."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('boot_probe',
    Path(__file__).resolve().parents[2] / 'scripts/remote-login-probe/enable-boot-output.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class SessionProbeTest(unittest.TestCase):
    def test_systemd_255_greeter_phase_uses_session_properties(self):
        from monitorize.platform import login_capture_probe
        replies = [
            Mock(stdout='c1 113 lightdm seat0 tty7 active no -\n'),
            Mock(stdout='Id=c1\nUser=113\nSeat=seat0\nClass=greeter\n'),
        ]
        with patch.object(login_capture_probe.subprocess, 'run', side_effect=replies) as run:
            self.assertEqual(login_capture_probe.read_phase(), 'greeter')
        self.assertIn('--no-legend', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ['--', 'c1'])

    def test_missing_session_properties_fail_closed(self):
        from monitorize.platform import login_capture_probe
        replies = [Mock(stdout='c1 113 lightdm seat0\n'), Mock(stdout='')]
        with patch.object(login_capture_probe.subprocess, 'run', side_effect=replies):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                login_capture_probe.read_phase()


class BootProbeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.connector = self.root / 'connector0'
        self.connector.mkdir()
        for name, value in [('status', '2'), ('enabled', '1'), ('dynamic', '1'),
                            ('edid', ''), ('edid_enabled', '0')]:
            (self.connector / name).write_text(value)
        self.edid = b'\x00\xff\xff\xff\xff\xff\xff\x00' + bytes(119) + b'\x06'
        self.generate = Mock(return_value=self.edid)

    def enable(self, sessions=None):
        return probe.enable_output(self.connector, self.root, sessions or [], self.generate)

    def test_boot_connects_only_fixed_mon1(self):
        result = self.enable()
        self.assertTrue(result['connected'])
        self.generate.assert_called_once_with(1920, 1080, 60, 'mon1')
        self.assertEqual((self.connector / 'status').read_text(), '1')
        self.assertEqual((self.connector / 'edid').read_bytes(), self.edid)

    def test_user_or_greeter_session_refuses_before_any_writes(self):
        for role in ('user', 'greeter', 'background', None):
            with self.subTest(role=role), self.assertRaises(RuntimeError):
                self.enable([{'class': role}])
        self.assertEqual((self.connector / 'edid').read_bytes(), b'')
        self.generate.assert_not_called()

    def test_existing_lease_or_connected_output_refuses(self):
        lease = self.root / 'primary.json'
        lease.write_text('{}')
        with self.assertRaises(RuntimeError):
            self.enable()
        lease.unlink()
        (self.connector / 'status').write_text('1')
        with self.assertRaises(RuntimeError):
            self.enable()
        self.generate.assert_not_called()

    def test_symlinked_attribute_refuses(self):
        (self.connector / 'edid').unlink()
        (self.connector / 'edid').symlink_to(self.root / 'outside')
        with self.assertRaises(RuntimeError):
            self.enable()
        self.assertFalse((self.root / 'outside').exists())

    def test_invalid_edid_never_connects(self):
        self.generate.return_value = b'bad'
        with self.assertRaises(RuntimeError):
            self.enable()
        self.assertEqual((self.connector / 'status').read_text(), '2')
