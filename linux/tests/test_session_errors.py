"""Actionable failures survive toast expiry, cleanup and Session page recreation."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from linux.tests.test_host_configuration_ui import PROGRAM as CONFIGURATION_PROGRAM

ROOT = Path(__file__).resolve().parents[2]
PREFIX = CONFIGURATION_PROGRAM.split('            # Live changes')[0]
SCENARIOS = r'''
            from PyQt6.QtCore import QMetaObject
            command='sudo setcap cap_sys_admin+p /test/monitorize/sunshine'
            error='KMS capture requires cap_sys_admin. Run: '+command
            backend.configuration_state.update('primary',{'sunshine_capture':'kms'})
            with patch('monitorize.desktop.kms_authorization.get_sunshine_kms_setup_status',return_value={'code':'check_failed','binary':'/test/monitorize/sunshine','message':error}):
                backend.applyConfiguration()
            assert error in backend.sessionError
            original_failure=backend.sessionError
            assert not backend.applyingConfiguration
            # Apply's preflight fails without setting the streaming status.
            assert c.status==''
            loop=QEventLoop();QTimer.singleShot(5200,loop.quit);loop.exec()
            backend.stopSession()
            evaluate('navigate("StreamingPage.qml")');settle();settle()
            assert view.rootObject().findChild(QObject,'sessionErrorCard') is None
            assert view.rootObject().findChild(QObject,'dismissSessionErrorButton') is None
            text=view.rootObject().findChild(QObject,'displayErrorText1')
            assert error in text.property('text')
            assert text.property('readOnly') and text.property('selectByMouse')
            copy=view.rootObject().findChild(QObject,'copyDisplayError1')
            QMetaObject.invokeMethod(copy,'clicked')
            assert app.clipboard().text()==backend.sessionError
            assert command in app.clipboard().text()
            # Recreating Session must not lose details held by the backend.
            evaluate('navigate("SettingsPage.qml")');settle()
            evaluate('navigate("StreamingPage.qml")');settle();settle()
            text=view.rootObject().findChild(QObject,'displayErrorText1')
            assert error in text.property('text')
            c.status='VA-API encoder unavailable. Select Software or install the driver.'
            backend.streamingStartFailed.emit()
            c.status='Session stopped.'
            backend.streamingStartFailed.emit()
            assert error in backend.sessionError
            assert 'VA-API encoder unavailable' in backend.sessionError
            # A failed secondary listener and its cleanup cannot erase a command.
            secondary='Second display: Run: sudo setcap cap_sys_admin+p /test/second-sunshine'
            runtime.slots={2:SimpleNamespace(status='error',error=secondary)}
            backend.session.changed.emit()
            runtime.slots.clear();backend.session.changed.emit()
            assert secondary in backend.sessionError
            runtime.slots={1:SimpleNamespace(status='recovering',error='Encoder crashed; reconnect after recovery.')}
            backend.session.changed.emit()
            runtime.slots.clear();backend.session.changed.emit()
            assert 'Encoder crashed; reconnect after recovery.' in backend.sessionError
            backend.virtualDisplayCleanupFinished.emit(False,'Display cleanup needs administrator repair.')
            backend.remoteLoginSetupBlocked.emit('Select KMS capture before remote-login setup.')
            c.status='Select HEVC in Moonlight. Sunshine reported: MONITORIZE_STRICT_CODEC_REJECTED'
            backend.streamingCodecMismatch.emit('Select HEVC in Moonlight')
            assert 'MONITORIZE_STRICT_CODEC_REJECTED' in backend.sessionError
            # Repeated events are deduplicated and successful saves do not erase failures.
            before=backend.sessionError
            backend.configurationApplyFinished.emit(False,original_failure)
            backend.configurationApplyFinished.emit(True,'Settings saved.')
            assert backend.sessionError==before
            # Each display retains its own latest error with a small copy action.
            assert backend.displayError(2)==secondary
            assert backend.copyDisplayError(2)
            assert app.clipboard().text()==secondary
            assert backend.displayError(1)!=secondary
            assert view.rootObject().findChild(QObject,'dismissSessionErrorButton') is None
            assert view.rootObject().findChild(QObject,'copySessionErrorButton') is None
            # Clear card errors on a new user attempt, not on Stop/navigation.
            backend._clear_display_errors()
            assert not backend.displayError(1) and not backend.displayError(2)
            backend.sunshineChoicesFinished.emit(2,False,secondary)
            assert backend.displayError(2)==secondary
            view.setSource(QUrl());backend.close()
print('Persistent Session errors, full command copying, navigation, cleanup and per-display copying passed')
'''


class SessionErrorsTests(unittest.TestCase):
    def test_errors_remain_selectable_and_copyable_after_transient_notifications(self):
        result = subprocess.run(
            [sys.executable, '-c', PREFIX + SCENARIOS], cwd=ROOT,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                     PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_kms_repair_command_quotes_the_selected_engine_path(self):
        from monitorize.platform import sunshine_service
        binary = "/tmp/Monitorize user's build/sunshine"
        with patch.object(sunshine_service, 'find_sunshine_command', return_value=[binary]), \
             patch.object(sunshine_service.shutil, 'which', return_value='/usr/bin/getcap'), \
             patch.object(sunshine_service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            message = sunshine_service.get_sunshine_kms_setup_error()
        self.assertEqual(shlex.split(message.split('Run: ', 1)[1]),
                         ['sudo', 'setcap', 'cap_sys_admin+p', binary])
