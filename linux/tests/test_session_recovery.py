"""Session actions resolve a verified graphical logind subject."""
import os
import sys
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import session_recovery


class SessionRecoveryTests(unittest.TestCase):
    def test_user_manager_uses_verified_session_without_environment_id(self):
        manager = Mock()
        dbus = Mock()
        dbus.Interface.return_value = manager
        with patch.dict(os.environ, {}, clear=True), \
             patch.dict(sys.modules, dbus=dbus), \
             patch.object(session_recovery, 'login_session', return_value='93'):
            self.assertTrue(session_recovery.end_session('logout')['success'])
        manager.TerminateSession.assert_called_once_with('93')

    def test_forged_environment_cannot_choose_another_session(self):
        manager = Mock()
        dbus = Mock()
        dbus.Interface.return_value = manager
        with patch.dict(os.environ, XDG_SESSION_ID='foreign'), \
             patch.dict(sys.modules, dbus=dbus), \
             patch.object(session_recovery, 'login_session', return_value='93'):
            self.assertTrue(session_recovery.end_session('logout')['success'])
        manager.TerminateSession.assert_called_once_with('93')

    def test_no_verified_session_cannot_log_out_a_different_user(self):
        manager = Mock()
        dbus = Mock()
        dbus.Interface.return_value = manager
        with patch.dict(sys.modules, dbus=dbus), \
             patch.object(session_recovery, 'login_session', return_value=None):
            self.assertFalse(session_recovery.end_session('logout')['success'])
        manager.TerminateSession.assert_not_called()
