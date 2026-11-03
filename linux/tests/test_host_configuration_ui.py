"""Full QML form + backend transaction, with fake engines and isolated settings."""
import os
import subprocess
import sys
import unittest

PROGRAM=r'''
import copy,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from PyQt6.QtCore import QObject,QUrl,QEventLoop,QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQml import QQmlExpression,qmlContext
from monitorize.config import settings
from monitorize.desktop.backend import MonitorizeBackend
app=QApplication([])
def settle():
    loop=QEventLoop();QTimer.singleShot(80,loop.quit);loop.exec()
with tempfile.TemporaryDirectory() as d:
    settings.CONFIG_DIR=d;settings.CONFIG_FILE=str(Path(d)/'settings.ini')
    settings.save_display_settings(resolution='1920x1080',use_client_display_settings=True)
    settings.save_general_settings(client_listen_on_open=False,client_restart_on_crash=False)
    with patch('monitorize.desktop.remote_login_runtime.SETTINGS',Path(d)/'absent'), \
         patch('monitorize.desktop.backend.get_local_ip',return_value='192.0.2.1'), \
         patch('monitorize.desktop.backend.find_sunshine_command',return_value=['fake-sunshine']), \
         patch('monitorize.desktop.backend.get_system_setup_status',return_value={'available':False}), \
         patch('monitorize.desktop.backend.StreamingController') as factory, \
         patch('monitorize.desktop.backend.is_sunshine_running',return_value=False), \
         patch('monitorize.desktop.backend.MonitorizeVkmsClient') as vkms:
        c=factory.return_value
        c.streaming=False;c.third_streaming=False;c.primary_ready=False;c.third_ready=False
        c._vkms_retiring={};c.de='sway';c.status=''
        backend=MonitorizeBackend('sway')
        runtime=SimpleNamespace(active=False,busy=False,retiring={},cleanup_blocked={},slots={},cards=[],error='',guardian=None)
        def stop():runtime.active=False;runtime.slots.clear();runtime.cards.clear()
        runtime.stop=Mock(side_effect=stop)
        c.stop.side_effect=lambda:setattr(c,'streaming',False)
        backend.session.client_runtime=runtime
        launches=[]
        def launch(**_):
            launches.append(copy.deepcopy(backend.session.runtime_configuration))
            runtime.active=True
            configs=[(1,backend.session.configuration())]
            if backend.session.count==2:configs.append((2,backend.session.second_configuration()))
            runtime.slots={i:SimpleNamespace(status='waiting',config=config,error='') for i,config in configs}
            runtime.cards=[dict(instance=i,status=slot.status,target={},error=slot.error)
                           for i,slot in runtime.slots.items()]
        with patch.object(backend.session,'start',side_effect=launch), patch.object(backend,'ensureNativeCompositor',return_value=True):
            view=QQuickWidget();view.rootContext().setContextProperty('backend',backend)
            view.setSource(QUrl.fromLocalFile(str(Path('linux/monitorize/qml/main.qml').resolve())))
            settle();assert view.status()==QQuickWidget.Status.Ready
            def evaluate(code):
                expression=QQmlExpression(view.rootContext(),view.rootObject(),code)
                result=expression.evaluate();assert not expression.hasError(),expression.error().toString();return result
            backend.startConfiguredListeners();backend.startConfiguredListeners()
            assert len(launches)==1,'Expected one automatic launch despite legacy false flag'
            assert backend.sessionRunning
            evaluate('navigate("DisplaySetupPage.qml")');settle()
            assert view.rootObject().findChild(QObject,'displaySetupPage')
            assert not backend.configurationDirty,'Opening Configure changed the draft'
            # Live changes are draft-only, including modes and Sunshine choices.
            page=view.rootObject().findChild(QObject,'displaySetupPage')
            edit=QQmlExpression(qmlContext(page),page,"updateDisplay(0, Object.assign({}, primaryDisplay(), {resolution: '2560x1440'}))")
            edit.evaluate();assert not edit.hasError(),edit.error().toString()
            choices=dict(sunshine_encoder='Software',sunshine_codec='HEVC',sunshine_capture='auto',streaming_customized=True,sunshine_native_pen_touch=True,enable_audio=False)
            with patch('monitorize.desktop.backend.save_sunshine_config') as write:
                backend.requestSaveSunshineChoices(1,choices);settle();write.assert_not_called()
            assert backend.configurationDirty
            assert settings.load_display_settings()['resolution']=='1920x1080'
            assert backend.session.configuration()['res']=='1920x1080'
            assert len(launches)==1
            apply=view.rootObject().findChild(QObject,'applyConfigurationButton')
            assert apply and apply.property('enabled')
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(apply,'clicked');settle()
            dialog=view.rootObject().findChild(QObject,'applyConfigurationDialog')
            assert dialog and dialog.property('visible')
            before=len(launches)
            QMetaObject.invokeMethod(dialog,'reject');settle()
            assert len(launches)==before and backend.configurationDirty
            QMetaObject.invokeMethod(apply,'clicked');settle()
            QMetaObject.invokeMethod(dialog,'accept')
            assert backend.applyingConfiguration
            backend._restart.tick();backend._restart.tick()
            assert not backend.applyingConfiguration
            assert not backend.configurationDirty
            assert backend.session.configuration()['res']=='2560x1440'
            assert backend.session.configuration()['encoder']=='Software'
            assert settings.load_display_settings()['resolution']=='2560x1440'
            # Saving later persists without stopping or changing the running session.
            before=len(launches)
            backend.saveVirtualDisplaySettings([dict(backend.loadVirtualDisplaySettings()[0],resolution='1920x1080')])
            QMetaObject.invokeMethod(apply,'clicked');settle()
            later=view.rootObject().findChild(QObject,'restartLaterButton')
            assert later is not None
            QMetaObject.invokeMethod(later,'clicked');settle()
            assert len(launches)==before and runtime.active
            assert not backend.configurationDirty and backend.configurationRestartRequired
            assert settings.load_display_settings()['resolution']=='1920x1080'
            assert backend.session.configuration()['res']=='2560x1440'
            backend.restartSession();backend._restart.tick();backend._restart.tick()
            assert backend.session.configuration()['res']=='1920x1080'
            assert not backend.configurationRestartRequired
            backend.saveVirtualDisplaySettings([dict(backend.loadVirtualDisplaySettings()[0],resolution='2560x1440')])
            backend.applyConfiguration();backend._restart.tick();backend._restart.tick()
            # Explicit Restart applies the latest draft in one operation.
            backend.saveVirtualDisplaySettings([dict(backend.loadVirtualDisplaySettings()[0],resolution='1280x720')])
            backend.restartSession();backend._restart.tick();backend._restart.tick()
            assert not backend.configurationDirty
            assert backend.session.configuration()['res']=='1280x720'
            assert settings.load_display_settings()['resolution']=='1280x720'
            backend.stopSession();backend.startConfiguredListeners()
            assert not runtime.active,'Tray reactivation revoked explicit Stop'
            # Apply persists the creator even when authorization rejects restart.
            backend.configuration_state.update('primary',{'virtual_display_creator':'vkms'})
            with patch.object(vkms.return_value,'require_unattended',side_effect=RuntimeError('Authorization unavailable')):
                backend.applyConfiguration()
            assert settings.load_display_settings()['virtual_display_creator']=='vkms'
            assert backend.configuration_state.saved['primary']['virtual_display_creator']=='vkms'
            assert not backend.configurationDirty and not backend.applyingConfiguration
            backend.configuration_state.update('primary',{'virtual_display_creator':'native'})
            backend.applyConfiguration();backend._restart.tick();backend._restart.tick()
            # A failed launch restores only the old runtime; disk retains intent.
            previous_resolution=backend.session.configuration()['res']
            backend.configuration_state.update('primary',{'resolution':'1600x900'})
            backend.applyConfiguration();backend._restart.tick()
            with patch.object(backend._restart,'failure',return_value='encoder startup failed'):
                backend._restart.tick()
            backend._restart.tick();backend._restart.tick()
            assert not backend.applyingConfiguration
            assert settings.load_display_settings()['resolution']=='1600x900'
            assert backend.configuration_state.saved['primary']['resolution']=='1600x900'
            assert backend.session.configuration()['res']==previous_resolution
            assert not backend.configurationDirty and backend.configurationRestartRequired
            assert 'Settings saved' in backend.configurationMessage
            # A failed disk write does not stop or launch anything.
            before=len(launches)
            backend.configuration_state.update('primary',{'resolution':'1920x1080'})
            with patch('monitorize.desktop.configuration_state.save_bundle',side_effect=OSError('disk full')):
                backend.applyConfiguration()
            assert len(launches)==before and not backend.applyingConfiguration
            assert backend.configurationDirty and settings.load_display_settings()['resolution']=='1600x900'
            # Removing a configured second card is draft-only too.
            backend.saveVirtualDisplaySettings([backend.loadVirtualDisplaySettings()[0],dict(id=2,resolution='1920x1080',fps='60',use_client_display_settings=True)])
            assert backend.configuration_state.draft['second']['enabled']
            assert not backend.configuration_state.applied['second']['enabled']
            backend.saveVirtualDisplaySettings([backend.loadVirtualDisplaySettings()[0]])
            assert not backend.configuration_state.draft['second']['enabled']
            # Switching to idle display-only mode applies settings without auto-creation.
            before=len(launches)
            backend.setStreamingBackend('none')
            backend.applyConfiguration();backend._restart.tick();backend._restart.tick()
            assert not backend.applyingConfiguration
            assert backend.appliedStreamingBackend=='none'
            assert len(launches)==before
            assert not runtime.active
            # Partial root setup must refresh ownership and reconcile desktop capture.
            backend._detected_de='gnome'
            backend.configuration_state.update('primary',{'sunshine_capture':'kms','virtual_display_creator':'native'})
            backend.configuration_state.accept(backend.configuration_state.candidate(remote_login=True))
            backend._remote_login.enabled=True
            backend._finish_remote_login_setup({'success':False,'partial':True,'message':'Controller failed'})
            assert not backend.remoteLoginEnabled
            assert backend.configuration_state.saved['primary']['sunshine_capture']=='auto'
            assert settings.load_display_settings()['sunshine_capture']=='auto'
            assert backend._verified_configuration is None
            # Disk failures keep corrected choices dirty, rather than claiming a save.
            backend.configuration_state.update('primary',{'sunshine_capture':'kms'})
            backend.configuration_state.accept(backend.configuration_state.candidate(remote_login=True))
            settings.save_display_settings(resolution='1280x720',sunshine_capture='kms')
            backend._remote_login.enabled=True
            with patch('monitorize.desktop.configuration_state.save_bundle',side_effect=OSError('disk full')):
                backend._finish_remote_login_setup({'success':True})
            assert backend.configuration_state.saved['primary']['sunshine_capture']=='kms'
            assert backend.configuration_state.draft['primary']['sunshine_capture']=='auto'
            assert backend.configurationDirty
            assert settings.load_display_settings()['sunshine_capture']=='kms'
            # Ownership fallback performs the same capture reconciliation.
            backend._remote_login.enabled=True
            backend._desktop_fallback_generation=7
            policy=SimpleNamespace(enabled=True)
            with patch('monitorize.remote_login.settings.read_settings',return_value=policy), \
                 patch('monitorize.platform.remote_login_identity.desktop_fallback_allowed',return_value=True) as allowed:
                backend._finish_desktop_fallback({'success':True,'generation':7,'action':'restart'})
                allowed.assert_called_once_with(policy)
            backend._restart.tick();backend._restart.tick()
            assert not backend.applyingConfiguration
            assert backend.session.configuration()['capture']=='auto'
            assert settings.load_display_settings()['sunshine_capture']=='auto'
            view.setSource(QUrl());backend.close()
print('Automatic launch, live QML draft edits, Apply, Restart and Stop passed')
'''

class HostConfigurationUiTest(unittest.TestCase):
    def test_host_configuration(self):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',PROGRAM],env=env,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
