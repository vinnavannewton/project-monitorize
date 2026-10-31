"""Actual Qt shutdown/menu regression in a subprocess; no backend or service runs."""
import os
import subprocess
import sys
import unittest

PROGRAM = r'''
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow
from monitorize.desktop.main_window import MonitorizeWindow
from monitorize.desktop.tray_agent import TrayAgent
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
class Window(MonitorizeWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self._quitting=False
        self.backend=SimpleNamespace(_vkms_management=None,configurationDirty=False,
            streaming=SimpleNamespace(_vkms_retiring={}),session=SimpleNamespace(retiring=False),
            close=Mock(),set_ui_visible=Mock(),should_minimize_to_tray=Mock(return_value=True),
            isStreaming=False,remoteLoginEnabled=False)
        self._setup_tray()
with patch('monitorize.desktop.main_window.QProcess.startDetached') as launch, \
     patch('monitorize.desktop.main_window.app_log.write'), \
     patch('monitorize.desktop.main_window.app_log.close'), \
     patch('monitorize.desktop.main_window.QSystemTrayIcon.isSystemTrayAvailable',return_value=True):
    window=Window();window.show()
    actions=[a for a in window.tray.contextMenu().actions() if not a.isSeparator()]
    assert [a.text() for a in actions]==['Open','Quit']
    agent=TrayAgent()
    assert [a.text() for a in agent.menu.actions() if not a.isSeparator()]==['Open','Quit']
    QTimer.singleShot(0,actions[-1].trigger)
    QTimer.singleShot(2000,lambda:app.exit(99))
    assert app.exec()==0,'Quit did not finish on first click'
    assert window._quitting
    assert not window.isVisible()
    window.backend.close.assert_called_once()
    launch.assert_not_called()
    # Duplicate close/activation while shutdown completes must not reopen UI.
    assert window._quit_app()
    window._restore_from_tray()
    assert not window.isVisible()
    window.backend.close.assert_called_once()
    window.tray.hide()
'''


class TrayQuitUiTest(unittest.TestCase):
    def test_first_quit_exits_without_recreating_tray_and_menus_have_two_actions(self):
        result=subprocess.run([sys.executable,'-c',PROGRAM],capture_output=True,text=True,
            timeout=10,env={**os.environ,'PYTHONPATH':'linux','QT_QPA_PLATFORM':'offscreen'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
