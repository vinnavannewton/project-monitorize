"""Agent recovery is session-aware and cannot replay privileged work."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from monitorize.platform import polkit_agent


class PolkitAgentSessionTests(unittest.TestCase):
    def setUp(self):
        state = patch.dict(polkit_agent._STARTED, {}, clear=True)
        state.start()
        self.addCleanup(state.stop)

    def library(self, *, user_manager=False):
        library = Mock()
        library.sd_pid_get_session.return_value = -61 if user_manager else 0
        library.sd_pid_get_owner_uid.return_value = 0
        def get_owner(pid, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_uint))[0] = polkit_agent.os.getuid()
            return 0
        library.sd_pid_get_owner_uid.side_effect = get_owner
        library.sd_uid_get_display.return_value = 0
        library.sd_session_get_uid.side_effect = get_owner
        library.sd_session_is_active.return_value = 1
        library.sd_session_get_type.return_value = 0
        return library

    def test_user_manager_fallback_matches_polkit_display_session(self):
        library = self.library(user_manager=True)
        with patch.object(polkit_agent, 'login_library', return_value=library), \
             patch.object(polkit_agent, '_take_string', side_effect=['desktop', 'wayland']):
            self.assertEqual(polkit_agent.login_session(100), 'desktop')
        library.sd_pid_get_owner_uid.assert_called_once()
        library.sd_uid_get_display.assert_called_once()

    def test_direct_session_does_not_use_posix_sid_or_display_fallback(self):
        library = self.library()
        with patch.object(polkit_agent, 'login_library', return_value=library), \
             patch.object(polkit_agent, '_take_string', side_effect=['desktop', 'x11']), \
             patch.object(polkit_agent.os, 'getsid', side_effect=AssertionError('POSIX session is unrelated')):
            self.assertEqual(polkit_agent.login_session(100), 'desktop')
        library.sd_uid_get_display.assert_not_called()

    def test_inactive_or_non_graphical_sessions_are_not_used(self):
        library = self.library()
        library.sd_session_is_active.return_value = 0
        with patch.object(polkit_agent, 'login_library', return_value=library), \
             patch.object(polkit_agent, '_take_string', return_value='desktop'):
            self.assertIsNone(polkit_agent.login_session())
        library.sd_session_is_active.return_value = 1
        with patch.object(polkit_agent, 'login_library', return_value=library), \
             patch.object(polkit_agent, '_take_string', side_effect=['console', 'tty']):
            self.assertIsNone(polkit_agent.login_session())

    def test_slow_agent_is_not_spawned_twice(self):
        process = Mock()
        process.poll.return_value = None
        polkit_agent._STARTED['desktop'] = process
        with patch.object(polkit_agent.os, 'geteuid', return_value=1000), \
             patch.object(polkit_agent, 'login_session', return_value='desktop'), \
             patch.object(polkit_agent.subprocess, 'Popen') as spawn:
            self.assertTrue(polkit_agent.start_agent())
            self.assertTrue(polkit_agent.start_agent())
        spawn.assert_not_called()

    def test_non_dumpable_agent_is_identified_by_kernel_uid_and_cmdline(self):
        executable = Path('/usr/libexec/kf6/polkit-kde-authentication-agent-1')
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory)/'1234'
            process.mkdir()
            uid = polkit_agent.os.getuid()
            (process/'status').write_text(f'Name:\tagent\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
            (process/'cmdline').write_bytes(str(executable).encode() + b'\0')
            with patch.object(Path, 'glob', return_value=[process]), \
                 patch.object(Path, 'stat', return_value=SimpleNamespace(st_uid=0)), \
                 patch.object(polkit_agent, 'login_session', return_value='desktop'):
                found = list(polkit_agent.known_agent_processes([executable]))
        self.assertEqual(found, [(executable, 'desktop')])

    def test_other_session_agent_does_not_get_replaced(self):
        kde = Path('/usr/libexec/kf6/polkit-kde-authentication-agent-1')
        gnome = Path('/usr/libexec/polkit-gnome-authentication-agent-1')
        info = SimpleNamespace(st_uid=0, st_mode=0o100755)
        with patch.object(polkit_agent.os, 'geteuid', return_value=1000), \
             patch.object(polkit_agent, 'login_session', return_value='desktop'), \
             patch.object(polkit_agent, 'AGENTS', (str(kde), str(gnome))), \
             patch.object(Path, 'stat', return_value=info), \
             patch.object(Path, 'resolve', autospec=True, side_effect=lambda p: p), \
             patch.object(polkit_agent.os, 'access', return_value=True), \
             patch.object(polkit_agent, 'known_agent_processes', return_value=[(kde, 'other')]), \
             patch.object(polkit_agent.subprocess, 'Popen') as spawn:
            self.assertTrue(polkit_agent.start_agent())
        self.assertEqual(spawn.call_args.args[0], [str(gnome)])

    def test_helper_response_and_dismissal_never_trigger_automatic_replay(self):
        cases = [(1, '', 'No authentication agent found'),
                 (126, '', 'No authentication agent found'),
                 (127, '{"success":false}', 'No authentication agent found'),
                 (127, '', 'Not authorized')]
        for code, stdout, stderr in cases:
            with self.subTest(code=code, stdout=stdout), \
                 patch.object(polkit_agent.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as run, \
                 patch.object(polkit_agent, 'start_agent') as start:
                polkit_agent.run_authorized(['pkexec', 'helper'])
            run.assert_called_once()
            start.assert_not_called()

    def test_registration_failure_retries_are_bounded(self):
        missing = subprocess.CompletedProcess([], 127, '', 'No authentication agent found')
        with patch.object(polkit_agent.subprocess, 'run', return_value=missing) as run, \
             patch.object(polkit_agent, 'start_agent', return_value=True), \
             patch.object(polkit_agent.time, 'sleep'):
            self.assertIs(polkit_agent.run_authorized(['pkexec', 'helper']), missing)
        self.assertEqual(run.call_count, 4)
