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
    # The user's installed remote-login timeout can differ from this fixture.
    isolated_settings.save_display_idle_timeout(20)
    with patch('monitorize.desktop.remote_login_runtime.SETTINGS',__import__('pathlib').Path(d)/'absent'), \
         patch('monitorize.desktop.backend.get_local_ip',return_value='192.0.2.1'), \
         patch('monitorize.desktop.backend.find_sunshine_command',return_value=None), \
         patch('monitorize.desktop.backend.get_system_setup_status',return_value={'available':False}), \
         patch('monitorize.desktop.backend.load_general_settings',return_value={'system_setup_decided':True}), \
         patch('monitorize.desktop.backend.MonitorizeVkmsClient') as client, \
         patch('monitorize.platform.system_setup.remote_login_setup_status') as status:
        status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True}
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
            # Settings diagnostics are selectable plain text with one-click copy.
            page.setProperty('settingsError', 'Run command <literal> safely')
            settle()
            error=page.findChild(QObject,'startupErrorMessageBody');assert error
            assert error.property('readOnly') and error.property('selectByMouse')
            assert error.property('text')=='Run command <literal> safely'
            e=QQmlExpression(qmlContext(page),page,'backend.copySettingsMessage(page.settingsError)');e.evaluate()
            assert app.clipboard().text()=='Run command <literal> safely'
            assert page.findChild(QObject,'startupErrorMessageCopy')
            # Cancelling repair retains the authoritative checkbox state.
            backend.remoteLoginSetupFinished.emit({'success':False,'repair':True,'cancelled':True,'message':'Cancelled'})
            settle()
            assert not page.findChild(QObject,'bootStartupCheck').property('checked')
            page.setProperty('settingsError','')
            # Active remote capture asks before stopping anything; cancel retains the saved state.
            previous_status=backend._remote_login.status
            backend._remote_login.status={'capture_ready':True,'state':'desktop_available'}
            with patch('monitorize.desktop.remote_login_setup.is_sunshine_running',return_value=False), patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup') as admin:
                backend.configureRemoteLogin(False);settle()
                dialog=view.rootObject().findChild(QObject,'recoveryDecisionDialog');assert dialog
                assert dialog.property('visible') and backend.remoteLoginSetupBusy
                admin.assert_not_called()
                e=QQmlExpression(qmlContext(dialog),dialog,'reject()');e.evaluate()
                assert not e.hasError(),e.error().toString()
                settle()
                assert not backend.remoteLoginSetupBusy
                assert not dialog.property('visible')
                assert prop('pendingBootAction')==-1
                admin.assert_not_called()
            backend._remote_login.status=previous_status
            page.setProperty('settingsError','')
            backend._remote_login.status={'capture_ready':True,'state':'desktop_available'}
            with patch('monitorize.desktop.remote_login_setup.is_sunshine_running',return_value=False), patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup',return_value={'success':True,'configured':False,'message':'Disabled'}) as admin, patch('monitorize.desktop.remote_login_setup.configure'), patch.object(backend,'_stop_owned_session') as stop_owned, patch.object(backend,'_owned_session_stopped',return_value=True):
                backend.configureRemoteLogin(False);settle()
                dialog=view.rootObject().findChild(QObject,'recoveryDecisionDialog')
                e=QQmlExpression(qmlContext(dialog),dialog,'accept()');e.evaluate()
                for _ in range(20):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                stop_owned.assert_called_once()
                admin.assert_called_once_with(False)
                assert not backend.remoteLoginSetupBusy
                assert not page.findChild(QObject,'bootStartupCheck').property('checked')
            backend._remote_login.status=previous_status
            # A pending disable is recovered directly, without first enabling hosting.
            page.setProperty('bootOptionsExpanded',True)
            page.setProperty('pendingBootAction',0)
            status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True,'maintenance_pending':True,'recovery_available':True}
            e=QQmlExpression(qmlContext(page),page,'page.refreshStartup()');e.evaluate();settle()
            resume=page.findChild(QObject,'resumeRemoteLoginMaintenance');assert resume.property('visible')
            with patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup',return_value={'success':True,'configured':False,'message':'Recovered'}) as recover, patch('monitorize.platform.sunshine_service.is_sunshine_running',return_value=False):
                backend.recoverRemoteLogin(True)
                for _ in range(20):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                recover.assert_called_once_with(False)
                assert not backend.remoteLoginSetupBusy
                assert prop('pendingBootAction')==-1
                assert not page.findChild(QObject,'bootStartupCheck').property('checked')
            page.setProperty('bootOptionsExpanded',False)
            status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True}
            # Source repair runs asynchronously through the coordinator; no live helper is invoked.
            with patch('monitorize.platform.remote_login_repair.repair_options',return_value={'repair_kind':'source','repair_message':'Repair','repair_needed':True}), patch('monitorize.platform.remote_login_repair.repair_remote_login',return_value={'success':True,'message':'Repaired'}) as repair, patch('monitorize.platform.sunshine_service.is_sunshine_running',return_value=False):
                backend.repairRemoteLogin()
                for _ in range(20):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                repair.assert_called_once_with()
                assert not backend.remoteLoginSetupBusy
                assert prop('startupNotice')=='Repaired'
                assert not page.findChild(QObject,'bootStartupCheck').property('checked')
            with patch('monitorize.platform.remote_login_repair.repair_options',return_value={'repair_kind':'','repair_message':'Package managed'}), patch('monitorize.platform.remote_login_repair.repair_remote_login') as repair:
                page.setProperty('bootOptionsExpanded',True)
                page.setProperty('settingsError','Package managed')
                e=QQmlExpression(qmlContext(page),page,'page.refreshStartup()');e.evaluate();settle()
                assert not page.findChild(QObject,'repairRemoteLoginButton').property('visible')
                backend.repairRemoteLogin();settle()
                repair.assert_not_called()
                assert not backend.remoteLoginSetupBusy
            page.setProperty('settingsError','')
            page.setProperty('bootOptionsExpanded',False)
            backend.vkmsAuthorizationReady.emit({'success':False,'message':'Unavailable fixture'});settle()
            assert not prop('vkmsAuthorization.success')
            assert not prop('vkmsAuthorization.unattended_enabled === false')
            primary=page.findChild(QObject,'loginPrimaryDisplayCheck');assert primary
            assert not primary.property('enabled') and not primary.property('checked')
            status.return_value={'available':True,'enabled':True,'supported':True,'manager':'gdm.service','create_login_virtual_display':False,'login_display_policy_available':True}
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
            # Live enrollment must never inherit a generic reboot hint.
            backend.remoteLoginSetupFinished.emit({'success':True,'activated':True,'reboot_required':True,'message':'Old generic reboot hint'})
            settle()
            assert prop('startupNotice') == 'Remote login enabled. Available now and after future boots.'
            backend._remote_setup_busy=True;backend.remoteLoginSetupChanged.emit();settle()
            notice=page.findChild(QObject,'startupNoticeText');assert notice
            assert notice.property('text') == 'Applying startup settings…'
            backend._remote_setup_busy=False;backend.remoteLoginSetupChanged.emit();settle()
            backend.remoteLoginSetupFinished.emit({'success':True,'primary_display_setting':True,'reboot_required':True})
            settle()
            assert prop('startupNotice') == 'Login-screen display choice saved for the next boot. Desktop streaming is unchanged.'
            # Saving this boot-only preference must not replace the live runtime.
            runtime=backend._remote_login
            with patch('monitorize.platform.system_setup.apply_login_virtual_display',return_value={'success':False,'message':'Cancelled'}) as save:
                primary.setProperty('checked',True)
                e=QQmlExpression(qmlContext(page),page,'loginPrimaryCheck.clicked()');e.evaluate();assert not e.hasError(),e.error().toString()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                save.assert_called_once_with(True)
                assert not primary.property('checked')
                assert backend._remote_login is runtime
            def saved_primary(enabled):
                status.return_value={**status.return_value,'create_login_virtual_display':enabled}
                return {'success':True,'reboot_required':True}
            with patch('monitorize.platform.system_setup.apply_login_virtual_display',side_effect=saved_primary) as save:
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
                assert timeout.property('value')==60 and prop('timeoutNotice')=='Cancelled'
                assert backend._remote_login is runtime
            # An enrolled boot option remains switchable with an incompatible host
            # and even when normal login startup is currently unchecked.
            previous_status=dict(status.return_value);previous_login=login_state[0]
            status.return_value={'available':False,'live_available':False,'enabled':True,'supported':False,'message':'Upgrade host'}
            login_state[0]=False
            backend.remoteLoginSetupChanged.emit();settle()
            assert boot.property('checked') and boot.property('enabled')
            status.return_value=previous_status
            login_state[0]=previous_login
            backend.remoteLoginSetupChanged.emit();settle()
            # Cancelling parent disable keeps both options selected.
            with patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup',return_value={'success':False,'message':'Cancelled'}) as disable:
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
            status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True}
            login_state[0]=True
            backend.remoteLoginSetupFinished.emit({'success':True});settle()
            assert not login_state[0] and not login.property('checked')
            assert not primary.property('enabled')
            assert nested.property('visible')
            assert not boot.property('enabled')
            explanation=page.findChild(QObject,'bootStartupStatusMessage')
            assert explanation.property('visible')
            assert 'after login first' in explanation.property('text')
            # The general timer remains visible/editable without either startup option.
            page.setProperty('bootOptionsExpanded',False);settle()
            assert page.findChild(QObject,'displayIdleTimeoutRow').property('visible')
            assert timeout.property('enabled')
            timeout.setProperty('value',45)
            with patch('monitorize.platform.system_setup.apply_display_idle_timeout') as admin:
                e=QQmlExpression(qmlContext(page),page,'saveDisplayTimeout.clicked()');e.evaluate()
                for _ in range(10):
                    settle()
                    if not backend.remoteLoginSetupBusy:break
                admin.assert_not_called()
                assert timeout.property('value')==45 and not save_timeout.property('enabled')
            page.setProperty('bootOptionsExpanded',True);settle()
            # Disabling just boot preserves login startup.
            page.setProperty('loginAfterBootDisable',1)
            backend.remoteLoginSetupFinished.emit({'success':True});settle()
            assert login_state[0] and login.property('checked') and nested.property('visible')
            # External asset repairs can be rechecked without navigating away.
            status.return_value={'available':False,'enabled':False,'supported':True,'message':'Installed host VERSION and manifest disagree'}
            e=QQmlExpression(qmlContext(page),page,'refreshStartup()');e.evaluate();settle()
            assert not boot.property('enabled') and explanation.property('visible')
            assert explanation.property('text')==status.return_value['message']
            status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True}
            refresh=page.findChild(QObject,'refreshBootStartupStatus')
            assert refresh.property('visible') and refresh.property('enabled')
            e=QQmlExpression(qmlContext(page),refresh,'clicked()');e.evaluate();assert not e.hasError(),e.error().toString()
            settle()
            assert boot.property('enabled') and not explanation.property('visible')
            # Failed/cancelled authorization restores actual enrollment state.
            status.return_value={'available':True,'live_available':True,'enabled':False,'supported':True}
            backend.remoteLoginSetupFinished.emit({'success':False,'message':'Cancelled'});settle()
            assert not boot.property('checked') and login.property('enabled')
            assert prop('settingsError')=='Cancelled'
            for accepted_capture in ('kms','auto'):
                with patch('monitorize.desktop.backend.load_display_settings',return_value={'sunshine_capture':accepted_capture}), patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup',return_value={'success':False,'message':'Cancelled'}) as configure, patch('monitorize.desktop.remote_login_setup.configure'), patch('monitorize.desktop.remote_login_setup.controller_snapshot',return_value={}), patch('monitorize.desktop.remote_login_setup.restore_controller_snapshot'):
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
