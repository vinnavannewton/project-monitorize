"""Actual QML navigation and backend persistence with isolated/fake engines."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

spec=importlib.util.spec_from_file_location('host_ui_fixture',Path(__file__).with_name('test_host_configuration_ui.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
PREFIX=fixture.PROGRAM.split('            apply=view.rootObject().findChild')[0]
SCENARIOS=r'''
            from PyQt6.QtCore import QMetaObject
            before=len(launches)
            evaluate('navigate("StreamingPage.qml")');settle()
            dialog=view.rootObject().findChild(QObject,'configureUnsavedDialog')
            assert dialog and dialog.property('visible')
            assert evaluate('selectedPage')[0]=='DisplaySetupPage.qml'
            keep=view.rootObject().findChild(QObject,'keepConfigurationEditingButton')
            QMetaObject.invokeMethod(keep,'clicked');settle()
            assert not dialog.property('visible') and backend.configurationDirty
            assert len(launches)==before
            evaluate('navigate("SettingsPage.qml")');settle()
            save=view.rootObject().findChild(QObject,'saveConfigurationChangesButton')
            QMetaObject.invokeMethod(save,'clicked');settle();settle()
            assert evaluate('selectedPage')[0]=='SettingsPage.qml'
            assert not backend.configurationDirty and backend.configurationRestartRequired
            assert settings.load_display_settings()['resolution']=='2560x1440'
            assert backend.session.configuration()['res']=='1920x1080'
            assert len(launches)==before and runtime.active
            evaluate('navigate("StreamingPage.qml")');settle();settle()
            assert not dialog.property('visible')
            assert view.rootObject().findChild(QObject,'savedConfigurationRestartNotice').property('visible')
            evaluate('navigate("DisplaySetupPage.qml")');settle();settle()
            page=view.rootObject().findChild(QObject,'displaySetupPage')
            def edit_resolution(res):
                expression=QQmlExpression(qmlContext(page),page,"updateDisplay(0, Object.assign({}, primaryDisplay(), {resolution: '"+res+"'}))")
                expression.evaluate();assert not expression.hasError(),expression.error().toString()
            edit_resolution('1280x720')
            evaluate('navigate("StreamingPage.qml")');settle()
            discard=view.rootObject().findChild(QObject,'discardConfigurationChangesButton')
            QMetaObject.invokeMethod(discard,'clicked');settle();settle()
            assert evaluate('selectedPage')[0]=='StreamingPage.qml'
            assert not backend.configurationDirty
            assert backend.configuration_state.draft['primary']['resolution'].split()[0]=='2560x1440'
            assert backend.session.configuration()['res']=='1920x1080'
            assert settings.load_display_settings()['resolution']=='2560x1440'
            assert len(launches)==before
            evaluate('navigate("DisplaySetupPage.qml")');settle();settle()
            page=view.rootObject().findChild(QObject,'displaySetupPage')
            edit_resolution('1280x720')
            evaluate('navigate("SettingsPage.qml")');settle()
            with patch('monitorize.desktop.configuration_state.save_bundle',side_effect=OSError('disk full')):
                QMetaObject.invokeMethod(save,'clicked');settle()
            assert dialog.property('visible') and backend.configurationDirty
            assert evaluate('selectedPage')[0]=='DisplaySetupPage.qml'
            assert 'disk full' in evaluate('configurationNavigationError')[0]
            QMetaObject.invokeMethod(keep,'clicked');settle()
            expression=QQmlExpression(qmlContext(page),page,"updateDisplay(0, Object.assign({}, primaryDisplay(), {resolution: 'Custom...', custom_w: '1', custom_h: '1', use_client_display_settings: false}))")
            expression.evaluate();assert not expression.hasError(),expression.error().toString()
            evaluate('navigate("SettingsPage.qml")');settle()
            assert dialog.property('visible') and not save.property('enabled')
            QMetaObject.invokeMethod(discard,'clicked');settle();settle()
            assert evaluate('selectedPage')[0]=='SettingsPage.qml'
            assert len(launches)==before
            view.setSource(QUrl());backend.close()
print('Save/Discard/Keep editing, failed save, invalid draft and unchanged live stream pass')
'''

class ConfigureNavigationTests(unittest.TestCase):
 def test_navigation_choices_preserve_running_stream(self):
  env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
  result=subprocess.run([sys.executable,'-c',PREFIX+SCENARIOS],env=env,capture_output=True,text=True,timeout=30)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)

 def test_leaving_unchanged_configure_does_not_prompt(self):
  program=PREFIX.split("            page=view.rootObject().findChild(QObject,'displaySetupPage')")[0]+r'''
            evaluate('navigate("SettingsPage.qml")');settle();settle()
            assert evaluate('selectedPage')[0]=='SettingsPage.qml'
            assert not view.rootObject().findChild(QObject,'configureUnsavedDialog').property('visible')
            assert not backend.configurationDirty
            view.setSource(QUrl());backend.close()
'''
  env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
  result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=30)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
