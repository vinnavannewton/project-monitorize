"""Offscreen desktop integration; fake remote service and no live display changes."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROGRAM=r'''
import tempfile,os
from unittest.mock import Mock,patch
from PyQt6.QtCore import QObject,pyqtSignal,QUrl,QEventLoop,QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQml import QQmlExpression
from monitorize.desktop.backend import MonitorizeBackend
from monitorize.desktop.remote_login_runtime import RemoteLoginRuntime as RealRuntime
app=QApplication([])
class Runtime(QObject):
    changed=pyqtSignal()
    def __init__(self,parent):
        super().__init__(parent);self.enabled=True;self.busy=False;self.calls=[]
        self.status={'state':'desktop_available','capture_ready':True}
    @property
    def active(self):
        return RealRuntime.active.fget(self)
    @property
    def message(self):return 'Fixture desktop available'
    def start(self):self.calls.append('start')
    def stop(self):self.calls.append('stop');self.status={'state':'stopped','capture_ready':False};self.changed.emit()
    def close(self):self.calls.append('close')
def settle():
    loop=QEventLoop();QTimer.singleShot(100,loop.quit);loop.exec()
with tempfile.TemporaryDirectory() as d:
    os.environ['XDG_CONFIG_HOME']=d
    from monitorize.config.settings import load_display_settings
    auto_settings=dict(load_display_settings(),sunshine_capture='auto')
    with patch('monitorize.platform.remote_login_identity.desktop_fallback_allowed',return_value=False), \
         patch('monitorize.desktop.remote_login_runtime.RemoteLoginRuntime',Runtime), \
         patch('monitorize.desktop.backend.load_display_settings',return_value=auto_settings), \
         patch('monitorize.desktop.backend.get_local_ip',return_value='192.0.2.1'), \
         patch('monitorize.desktop.backend.find_sunshine_command',return_value=['fixture']), \
         patch('monitorize.desktop.backend.get_system_setup_status',return_value={'available':False}), \
         patch('monitorize.desktop.backend.load_general_settings',return_value={'system_setup_decided':True}), \
         patch('monitorize.desktop.backend.MonitorizeVkmsClient'):
        backend=MonitorizeBackend('kde');view=QQuickWidget();view.resize(1000,900)
        try:
            view.rootContext().setContextProperty('backend',backend)
            view.setSource(QUrl.fromLocalFile(str(__import__('pathlib').Path('linux/monitorize/qml/main.qml').resolve())))
            view.show();settle();assert view.status()==QQuickWidget.Status.Ready
            expression=QQmlExpression(view.rootContext(),view.rootObject(),'navigate("StreamingPage.qml")')
            expression.evaluate();assert not expression.hasError();settle()
            assert backend.remoteLoginEnabled and backend.isStreaming and backend.sessionRunning
            assert backend.loadDisplaySettings()['sunshine_capture']=='auto', 'Remote KMS must preserve the saved Auto choice'
            with patch.object(backend.session,'start') as normal:
                backend.startConfiguredListeners();backend.startSession()
                normal.assert_not_called()
            assert backend._remote_login.calls==['start']
            with patch('monitorize.desktop.backend.start_sunshine') as launch, \
                 patch('monitorize.desktop.backend.QDesktopServices.openUrl'):
                backend.openSunshineWebUi(1);launch.assert_not_called()
            backend.stopSession();assert not backend.isStreaming and not backend.sessionRunning
            assert backend._remote_login.calls==['start','stop']
            backend._remote_login.status={'state':'unavailable','capture_ready':False}
            backend._remote_login.changed.emit();settle()
            assert backend.remoteLoginEnabled and not backend.isStreaming and not backend.sessionRunning and not backend.sessionBusy
            print('Remote desktop Start/Stop, QML, and duplicate-launch guards passed')
        finally:
            view.setSource(QUrl());view.close();backend.close();app.processEvents()
'''


class BackendTests(unittest.TestCase):
    def test_qml_and_session_actions_use_remote_owner(self):
        result=subprocess.run([sys.executable,'-c',PROGRAM],cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software'),
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('duplicate-launch guards passed',result.stdout)
