"""Isolated Settings UI tests; no live setup, service, or display operations."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROGRAM = r'''
import os,tempfile
from unittest.mock import patch
from PyQt6.QtCore import QObject,QUrl,QEventLoop,QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQml import QQmlExpression,qmlContext
from PyQt6.QtQuickWidgets import QQuickWidget
from monitorize.desktop.backend import MonitorizeBackend
app=QApplication([])
def settle():
    loop=QEventLoop();QTimer.singleShot(100,loop.quit);loop.exec()
with tempfile.TemporaryDirectory() as d:
    os.environ['XDG_CONFIG_HOME']=d
    import monitorize.config.settings as isolated_settings
    isolated_settings.CONFIG_DIR=d
    isolated_settings.CONFIG_FILE=str(__import__('pathlib').Path(d)/'settings.ini')
    with patch('monitorize.desktop.remote_login_runtime.SETTINGS',__import__('pathlib').Path(d)/'absent'), \
         patch('monitorize.desktop.backend.get_local_ip',return_value='192.0.2.1'), \
         patch('monitorize.desktop.backend.find_sunshine_command',return_value=None), \
         patch('monitorize.desktop.backend.get_system_setup_status',return_value={'available':False}), \
         patch('monitorize.desktop.backend.load_general_settings',return_value={'system_setup_decided':True}), \
         patch('monitorize.desktop.backend.MonitorizeVkmsClient') as client, \
         patch('monitorize.platform.system_setup.remote_login_setup_status') as status:
        status.return_value={'available':True,'enabled':False,'supported':True}
        client.return_value.get_authorization_state.return_value={'success':True,'unattended_enabled':True}
        login_state=[False]
        def set_login(enabled):login_state[0]=enabled;return ''
        login_read=patch('monitorize.desktop.backend.autostart.is_enabled',side_effect=lambda:login_state[0]);login_read.start()
        login_write=patch('monitorize.desktop.backend.autostart.set_enabled',side_effect=set_login);login_write.start()
        backend=MonitorizeBackend('sway');view=QQuickWidget()
        view.rootContext().setContextProperty('backend',backend)
        try:
            view.setSource(QUrl.fromLocalFile(str(__import__('pathlib').Path('linux/monitorize/qml/main.qml').resolve())))
            def evaluate(code):
                e=QQmlExpression(view.rootContext(),view.rootObject(),code)
                value=e.evaluate();assert not e.hasError(),e.error().toString();return value
            settle();evaluate('navigate("SettingsPage.qml")');settle();settle()
            assert view.status()==QQuickWidget.Status.Ready
            # Leaving a Settings subpage must discard its navigation history.
            nav=view.rootObject().findChild(QObject,'mainStack')
            expression=QQmlExpression(view.rootContext(),nav,'push("BootWifiSetupPage.qml")')
            expression.evaluate();assert not expression.hasError(),expression.error().toString()
            settle();settle()
            evaluate('navigate("DisplaySetupPage.qml")');settle();settle()
            assert nav.property('depth') == 1
            assert not view.rootObject().findChild(QObject,'globalBackButton').property('visible')
            evaluate('navigate("SettingsPage.qml")');settle();settle()
            nav=view.rootObject().findChild(QObject,'mainStack')
            expression=QQmlExpression(view.rootContext(),nav,'push("BootWifiSetupPage.qml")')
            expression.evaluate();assert not expression.hasError(),expression.error().toString()
            settle();settle()
            evaluate('navigate("SettingsPage.qml")');settle();settle()
            assert nav.property('depth') == 1
            # Accent selection is opt-in, live, and independent of light/dark.
            import monitorize.config.settings as appearance_settings
            from PyQt6.QtGui import QColor
            with patch.object(appearance_settings,'CONFIG_DIR',d), patch.object(appearance_settings,'CONFIG_FILE',str(__import__('pathlib').Path(d)/'theme.ini')), patch('monitorize.desktop.backend.load_general_settings',side_effect=appearance_settings.load_general_settings), patch.object(backend._accent_source,'refresh'):
                appearance=view.rootObject().findChild(QObject,'appearanceTheme')
                assert appearance
                assert not backend.useSystemAccent
                backend._accent_source.color=QColor('#bb2244')
                backend.setUseSystemAccent(True);settle()
                assert backend.useSystemAccent
                assert appearance.property('buttonBackground').name()=='#bb2244'
                backend.setThemeMode('light');settle()
                assert appearance.property('buttonBackground').name()=='#bb2244'
                backend._accent_source.color=QColor('#228844')
                backend._accent_source.changed.emit();settle()
                assert appearance.property('buttonBackground').name()=='#228844'
                backend.setUseSystemAccent(False);settle()
                assert appearance.property('buttonBackground').name()=='#1676d2'
            page=view.rootObject().findChild(QObject,'startupSettingsPage');assert page
            def prop(code):
                e=QQmlExpression(view.rootContext(),page,code);value=e.evaluate();assert not e.hasError(),e.error().toString();return value[0]
            assert prop('vkmsAuthorization.success')
            assert not prop('vkmsChecking')
            backend.vkmsAuthorizationReady.emit({'success':False,'message':'Unavailable fixture'});settle()
            assert not prop('vkmsAuthorization.success')
            assert not prop('vkmsAuthorization.unattended_enabled === false')
            primary=page.findChild(QObject,'loginPrimaryDisplayCheck');assert primary
            assert not primary.property('enabled') and not primary.property('checked')
            status.return_value={'available':True,'enabled':True,'supported':True,'manager':'gdm.service','login_primary_display':False}
            backend.remoteLoginSetupFinished.emit({'success':True,'message':'Reboot after setup.'});settle()
            assert prop('bootStatus.enabled')
            login=page.findChild(QObject,'loginStartupCheck')
            boot=page.findChild(QObject,'bootStartupCheck')
            assert login.property('checked') and login.property('enabled')
            assert boot.property('checked')
            nested=page.findChild(QObject,'bootStartupOptions')
            assert not nested.property('visible')
            e=QQmlExpression(qmlContext(page),page,'bootOptionsExpanded = true');e.evaluate();settle()
            assert nested.property('visible')
            assert primary.property('enabled')
            # Saving this boot-only preference must not replace the live runtime.
            runtime=backend._remote_login
            with patch('monitorize.platform.system_setup.apply_login_primary_display',return_value={'success':False,'message':'Cancelled'}) as save:
                primary.setProperty('checked',True)
                e=QQmlExpression(qmlContext(page),page,'loginPrimaryCheck.clicked()');e.evaluate();assert not e.hasError(),e.error().toString()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                save.assert_called_once_with(True)
                assert not primary.property('checked')
                assert backend._remote_login is runtime
            def saved_primary(enabled):
                status.return_value={**status.return_value,'login_primary_display':enabled}
                return {'success':True,'reboot_required':True}
            with patch('monitorize.platform.system_setup.apply_login_primary_display',side_effect=saved_primary) as save:
                primary.setProperty('checked',True)
                e=QQmlExpression(qmlContext(page),page,'loginPrimaryCheck.clicked()');e.evaluate();assert not e.hasError(),e.error().toString()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                save.assert_called_once_with(True)
                assert primary.property('checked') and backend._remote_login is runtime
            timeout=page.findChild(QObject,'displayIdleTimeoutSeconds');assert timeout
            save_timeout=page.findChild(QObject,'saveDisplayIdleTimeout');assert save_timeout
            assert timeout.property('value')==20 and timeout.property('enabled')
            timeout.setProperty('value',60);assert save_timeout.property('enabled')
            def saved_timeout(seconds):
                status.return_value={**status.return_value,'display_idle_timeout_seconds':seconds}
                return {'success':True,'message':'Display timeout saved.'}
            with patch('monitorize.platform.system_setup.apply_display_idle_timeout',side_effect=saved_timeout) as save:
                e=QQmlExpression(qmlContext(page),page,'saveDisplayTimeout.clicked()');e.evaluate();assert not e.hasError(),e.error().toString()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                save.assert_called_once_with(60)
                assert timeout.property('value')==60 and backend._remote_login is runtime
                assert not save_timeout.property('enabled')
            timeout.setProperty('value',5)
            with patch('monitorize.platform.system_setup.apply_display_idle_timeout',return_value={'success':False,'message':'Cancelled'}) as save:
                backend.configureDisplayIdleTimeout(5)
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                save.assert_called_once_with(5)
                assert timeout.property('value')==60 and prop('settingsError')=='Cancelled'
                assert backend._remote_login is runtime
            # Cancelling parent disable keeps both options selected.
            with patch('monitorize.platform.system_setup.apply_remote_login_setup',return_value={'success':False,'message':'Cancelled'}) as disable:
                login.setProperty('checked',False)
                e=QQmlExpression(qmlContext(page),page,'autostartCheck.clicked()');e.evaluate()
                assert not e.hasError(),e.error().toString()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                disable.assert_called_once_with(False)
                assert login.property('checked') and boot.property('checked')
                assert not login_state[0]
            # Successful parent disable clears login only after boot is disabled.
            page.setProperty('loginAfterBootDisable',0)
            status.return_value={'available':True,'enabled':False,'supported':True}
            login_state[0]=True
            backend.remoteLoginSetupFinished.emit({'success':True});settle()
            assert not login_state[0] and not login.property('checked')
            assert not primary.property('enabled')
            assert nested.property('visible')
            assert not boot.property('enabled')
            # Disabling just boot preserves login startup.
            page.setProperty('loginAfterBootDisable',1)
            backend.remoteLoginSetupFinished.emit({'success':True});settle()
            assert login_state[0] and login.property('checked') and nested.property('visible')
            # Failed/cancelled authorization restores actual enrollment state.
            status.return_value={'available':True,'enabled':False,'supported':True}
            backend.remoteLoginSetupFinished.emit({'success':False,'message':'Cancelled'});settle()
            assert not boot.property('checked') and login.property('enabled')
            assert prop('settingsError')=='Cancelled'
            for accepted_capture in ('kms','auto'):
                with patch('monitorize.desktop.backend.load_display_settings',return_value={'sunshine_capture':accepted_capture}), patch('monitorize.platform.system_setup.apply_remote_login_setup',return_value={'success':False,'message':'Cancelled'}) as configure:
                    boot.setProperty('checked',True)
                    e=QQmlExpression(qmlContext(page),page,'bootCheck.clicked()');e.evaluate()
                    assert not e.hasError(),e.error().toString()
                    for _ in range(10):
                        settle()
                        if not backend.remoteLoginSetupBusy:break
                    configure.assert_called_once_with(True)
            for capture in ('kwin','portal','wlr','x11','nvfbc','pipewire_node'):
                with patch('monitorize.desktop.backend.load_display_settings',return_value={'sunshine_capture':capture}), patch('monitorize.platform.system_setup.apply_remote_login_setup') as configure:
                    boot.setProperty('checked',True)
                    backend.configureRemoteLogin(True);settle()
                    configure.assert_not_called()
                    assert not boot.property('checked')
                    assert 'KMS' in view.rootObject().property('startFailureMessage')
            # Transient status failure retries without requesting authorization.
            from monitorize.platform.monitorize_vkms_dbus import VkmsServiceUnavailableError
            client.return_value.get_authorization_state.reset_mock()
            client.return_value.get_authorization_state.side_effect=[
                VkmsServiceUnavailableError('Service starting'),
                {'success':True,'unattended_enabled':True}]
            backend.refreshVkmsAuthorization()
            for _ in range(15):
                settle()
                if not backend._vkms_authorization_busy:break
            assert prop('vkmsAuthorization.unattended_enabled')
            assert client.return_value.get_authorization_state.call_count==2
            assert client.return_value.close.called
            client.return_value.get_authorization_state.side_effect=VkmsServiceUnavailableError('Still unavailable')
            backend.refreshVkmsAuthorization()
            for _ in range(25):
                settle()
                if not backend._vkms_authorization_busy:break
            assert not prop('vkmsAuthorization.success')
            assert not prop('vkmsAuthorization.unattended_enabled === false')
            client.return_value.get_authorization_state.side_effect=None
            client.return_value.get_authorization_state.return_value={'success':True,'unattended_enabled':False}
            backend.refreshVkmsAuthorization();settle()
            assert prop('vkmsAuthorization.success') and not prop('vkmsAuthorization.unattended_enabled')
            print('Settings startup and authorization states passed')
        finally:
            view.setSource(QUrl());view.close();backend.close();app.processEvents()
            login_read.stop();login_write.stop()
'''

class StartupSettingsTests(unittest.TestCase):
    def test_settings_states(self):
        result=subprocess.run([sys.executable,'-c',PROGRAM],cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
