"""Package recovery uses the guarded companion API at a live greeter."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from monitorize.remote_login.package_lifecycle import InstalledBackend


class GreeterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = InstalledBackend()
        self.host = Mock(LIVE_GREETER_RESERVATION_PROTOCOL=1)
        self.runtime = Mock()
        helper = SimpleNamespace(STATE_DIRECTORY=Path('/run/monitorize-test'))
        self.runtime.local_sessions.return_value = [
            dict(seat='seat0', uid=120, session='g1', **{'class': 'greeter'})]
        for context in (
            patch('monitorize.remote_login.settings.read_settings',
                  return_value=SimpleNamespace(uid=1000, enabled=True)),
            patch.object(self.backend, 'active', return_value=False),
            patch.object(self.backend, 'host', return_value=(self.host, self.runtime, helper)),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_greeter_uses_guarded_api_for_absent_or_existing_reservation(self):
        for existing in (None, dict(phase='greeter')):
            with self.subTest(existing=existing):
                self.host.reset_mock()
                self.host.boot_output_status.return_value = existing
                self.assertTrue(self.backend.restore(1000, restart=True))
                self.host.reserve_greeter_installed.assert_called_once_with(1000)
                self.host.main.assert_not_called()

    def test_old_companion_requires_update_without_bypassing_seat_checks(self):
        self.host.LIVE_GREETER_RESERVATION_PROTOCOL = 0
        with self.assertRaisesRegex(RuntimeError, 'Update the Monitorize VKMS'):
            self.backend.restore(1000, restart=True)
        self.host.main.assert_not_called()
        self.host.reserve_greeter_installed.assert_not_called()

    def test_missing_companion_api_requires_update(self):
        self.host.reserve_greeter_installed = None
        with self.assertRaisesRegex(RuntimeError, 'Update the Monitorize VKMS'):
            self.backend.restore(1000, restart=True)
        self.host.main.assert_not_called()

    def test_desktop_is_never_reclassified_as_greeter(self):
        self.runtime.local_sessions.return_value.append(
            dict(seat='seat0', uid=1000, session='3', **{'class': 'user'}))
        self.host.boot_output_status.return_value = None
        self.backend.restore(1000, restart=True)
        self.host.reserve_greeter_installed.assert_not_called()
        self.host.reserve_desktop_installed.assert_called_once()
