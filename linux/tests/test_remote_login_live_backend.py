"""Offscreen live-enrollment coordination: no installed services or displays."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from types import SimpleNamespace
from monitorize.desktop.client_display_runtime import ClientDisplayRuntime, ClientSlot

APP=QCoreApplication.instance() or QCoreApplication([])


class Runtime(QObject):
    changed=pyqtSignal()
    def __init__(self):
        super().__init__();self.status={};self.participant=SimpleNamespace(generation='grant',epoch='epoch')
    def stop(self):self.participant.want_running=False
    def start(self):pass
    def poll(self):pass


class BackendFixture(QObject):
    remoteLoginSetupChanged=pyqtSignal()
    def __init__(self):
        super().__init__();self._closing=False;self._remote_setup_busy=False
        self.session=SimpleNamespace(client_runtime=None,active=False,busy=False)
        self.streaming=SimpleNamespace(third_streaming=False,streaming=False)
        self._remote_login=Runtime();self._session_start_generation=0
        self._background_executor=Mock();self._background_executor.submit.return_value.add_done_callback=Mock()
        self.applied=[]
    def _finish_remote_login_setup(self,result):self.applied.append(result)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        from monitorize.desktop.remote_login_setup import RemoteLoginSetupCoordinator
        self.backend=BackendFixture();self.coordinator=RemoteLoginSetupCoordinator(self.backend)
        self.results=[];self.coordinator.finished.connect(self.results.append)
        self.addCleanup(self.coordinator.cancel)
    def begin(self):
        with patch('monitorize.desktop.remote_login_setup.remote_login_setup_status',return_value=dict(live_available=True)), \
             patch('monitorize.desktop.remote_login_setup.is_sunshine_running',return_value=False):
            self.coordinator.begin(True)
    def root_ready(self):
        self.coordinator._root_finished(dict(success=True,configured=True,controller_configured=True,
            reboot_required=False,uid=os.getuid(),session='3'))
    def test_root_success_waits_for_verified_listener_not_capture_frames(self):
        self.begin();self.root_ready();self.assertEqual(self.results,[])
        self.backend._remote_login.status=dict(uid=os.getuid(),session='3',epoch='epoch',
            desktop_owned=True,listening=True,capture_ready=False,state='desktop_listening')
        self.backend._remote_login.changed.emit()
        self.assertTrue(self.results[-1]['success']);self.assertTrue(self.results[-1]['activated'])
        self.assertFalse(self.results[-1]['reboot_required'])
    def test_wrong_session_and_startup_error_cannot_report_success(self):
        self.begin();self.root_ready()
        self.backend._remote_login.status=dict(uid=os.getuid(),session='other',epoch='epoch',desktop_owned=True,listening=True)
        self.backend._remote_login.changed.emit();self.assertEqual(self.results,[])
        self.backend._remote_login.status.update(session='3',startup_error='startup failed')
        self.backend._remote_login.changed.emit();self.assertFalse(self.results[-1]['success'])
    def test_active_client_blocks_before_root_or_user_changes(self):
        self.backend.session.active=True;self.begin()
        self.assertEqual(self.results,[])
        self.assertTrue(self.coordinator._awaiting_decision)
        self.coordinator.resolve(self.coordinator.request.request_id,False)
        self.assertTrue(self.results[-1]['cancelled'])
        self.backend._background_executor.submit.assert_not_called()
    def test_disable_skips_enablement_checks_and_never_calls_enable(self):
        self.backend.session.client_runtime=SimpleNamespace(active=True,
            pause_idle_for_enrollment=Mock(return_value={'configs':{}}),
            enrollment_pause_complete=Mock(return_value=True),resume_enrollment_pause=Mock())
        self.backend.session.active=True  # Idle listener, not a connected stream.
        with patch('monitorize.desktop.remote_login_setup.remote_login_setup_status',return_value=dict(live_available=False)), \
             patch('monitorize.desktop.remote_login_setup.is_sunshine_running',return_value=False):
            self.coordinator.begin(False)
        with patch('monitorize.desktop.remote_login_setup.apply_remote_login_setup',return_value=dict(success=True,configured=False)) as setup, \
             patch('monitorize.desktop.remote_login_setup.configure') as controller:
            result=self.coordinator._work()
        setup.assert_called_once_with(False);controller.assert_called_once_with(False)
        self.coordinator._root_finished(result)
        self.assertTrue(self.results[-1]['success'])
        self.assertIs(self.results[-1]['requested_enabled'],False)
        self.assertFalse(self.results[-1]['activated'])

    def test_stale_or_rejected_interruption_request_has_no_side_effect(self):
        self.backend.session.active=True;self.begin()
        self.coordinator.resolve('old-request',True)
        self.backend._background_executor.submit.assert_not_called()
        self.assertTrue(self.coordinator._awaiting_decision)
        self.coordinator.resolve(self.coordinator.request.request_id,False)
        self.assertTrue(self.results[-1]['cancelled'])

    def test_accepted_interruption_waits_for_owned_cleanup_before_root(self):
        self.backend.session.active=True;self.begin()
        self.backend._stop_owned_session=Mock()
        self.backend._owned_session_stopped=Mock(return_value=False)
        self.coordinator.resolve(self.coordinator.request.request_id,True)
        self.backend._stop_owned_session.assert_called_once()
        self.backend._background_executor.submit.assert_not_called()
        self.backend._owned_session_stopped.return_value=True
        self.coordinator._advance()
        self.backend._background_executor.submit.assert_called_once()

    def test_queued_advance_cannot_dismiss_a_pending_decision(self):
        self.backend.session.active=True;self.begin()
        self.coordinator._advance()
        self.assertTrue(self.coordinator._awaiting_decision)
        self.assertEqual(self.results,[])
        self.coordinator.cancel()
        self.assertTrue(self.results[-1]['cancelled'])

    def test_root_error_reports_partial_and_does_not_claim_activated(self):
        self.begin()
        self.coordinator._root_finished(dict(success=False,configured=True,partial=True,message='retry'))
        self.assertTrue(self.results[-1]['partial']);self.assertFalse(self.results[-1]['activated'])
    def test_stop_during_root_setup_suppresses_automatic_desktop_start(self):
        self.begin();self.coordinator.cancel();self.root_ready()
        self.assertFalse(self.results[-1]['success'])
        self.assertFalse(self.backend._remote_login.participant.want_running)
    def test_reused_listener_with_racing_client_is_polled_without_restart(self):
        self.begin()
        runtime=self.backend._remote_login
        runtime.participant.want_running=True
        runtime.status=dict(uid=os.getuid(),session='3',epoch='epoch',desktop_owned=True,listening=True,capture_ready=True)
        with patch.object(runtime,'start') as start,patch.object(runtime,'poll') as poll:
            self.coordinator._root_finished(dict(success=True,configured=True,controller_configured=True,
                reused=True,uid=os.getuid(),session='3'))
        start.assert_not_called();poll.assert_called_once()
        self.assertEqual(self.results,[])
        runtime.changed.emit()
        self.assertTrue(self.results[-1]['activated'])
    def test_cached_listener_status_is_not_fresh_completion(self):
        self.begin()
        runtime=self.backend._remote_login
        runtime.status=dict(uid=os.getuid(),session='3',epoch='epoch',desktop_owned=True,listening=True)
        self.root_ready()
        self.coordinator._check_listener()
        self.assertEqual(self.results,[])
        runtime.changed.emit()
        self.assertTrue(self.results[-1]['activated'])


class ControllerSnapshotTests(unittest.TestCase):
    def test_failed_enrollment_restores_exact_controller_and_autostart(self):
        from monitorize.desktop import remote_login_controller as controller
        with TemporaryDirectory() as directory:
            root=Path(directory);desktop=root/'autostart/monitorize.desktop'
            desktop.parent.mkdir();desktop.write_bytes(b'original autostart')
            run=Mock()
            snapshot=controller.controller_snapshot(config_home=root,enabled=False)
            controller.configure(True,config_home=root,run=run)
            controller.restore_controller_snapshot(snapshot,run=run)
            self.assertEqual(desktop.read_bytes(),b'original autostart')
            self.assertFalse((root/'systemd/user'/controller.UNIT).exists())
            self.assertFalse((root/'monitorize/remote-login-controller-backup.json').exists())

class RuntimeRefreshTests(unittest.TestCase):
    def test_backend_live_refresh_keeps_authenticated_participant_on_success_and_failure(self):
        from monitorize.desktop.backend import MonitorizeBackend
        for success in (True,False):
            runtime=Mock(enabled=True)
            backend=SimpleNamespace(_closing=False,_remote_login=runtime,remoteLoginEnabled=True,
                _remote_setup_busy=True,_desktop_fallback=False,_desktop_fallback_generation=0,
                _desktop_fallback_pending=False,remoteLoginSetupChanged=Mock(),remoteLoginSetupFinished=Mock(),
                configurationChanged=Mock(),clientDisplaySettingsChanged=Mock(),
                configuration_state=SimpleNamespace(applied={},saved={},draft={}),streaming=SimpleNamespace(),
                session=SimpleNamespace(),_remote_login_changed=Mock())
            with patch('monitorize.platform.remote_login_identity.desktop_fallback_allowed',return_value=False), \
                 patch('monitorize.remote_login.settings.read_settings'), \
                 patch('monitorize.desktop.remote_login_runtime.RemoteLoginRuntime') as replacement:
                MonitorizeBackend._finish_remote_login_setup(backend,dict(_live_pending=True,success=success,configured=True))
            replacement.assert_not_called();runtime.close.assert_not_called()
            runtime.refresh_enrollment.assert_called_once()
            self.assertIs(backend._remote_login,runtime)


class PlatformSetupTests(unittest.TestCase):
    def test_gui_setup_uses_live_action_and_blocks_old_assets(self):
        from monitorize.platform import system_setup
        completed=SimpleNamespace(returncode=0,stdout='{"success":true,"reboot_required":false}',stderr='')
        with patch.object(system_setup,'remote_login_setup_status',return_value=dict(available=True,live_available=True)), \
             patch.object(system_setup.shutil,'which',return_value='/usr/bin/pkexec'), \
             patch.object(system_setup.subprocess,'run',return_value=completed) as run:
            self.assertTrue(system_setup.apply_remote_login_setup(True)['success'])
            self.assertEqual(run.call_args.args[0][-1],'enable-live')
        with patch.object(system_setup,'remote_login_setup_status',return_value=dict(available=True,live_available=False,live_message='Update assets')), \
             patch.object(system_setup.subprocess,'run') as run:
            self.assertFalse(system_setup.apply_remote_login_setup(True)['success']);run.assert_not_called()

    def test_remote_setup_page_no_longer_promises_reboot_for_enable(self):
        page=Path(__file__).resolve().parents[1]/'monitorize/qml/RemoteLoginSetupPage.qml'
        self.assertNotIn('Reboot after enabling',page.read_text())


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.runtime=ClientDisplayRuntime(runtime_dir=self.temp.name)
        self.addCleanup(self.runtime.stop)
        for name in ('stop_sunshine','is_sunshine_running'):
            p=patch('monitorize.desktop.client_display_runtime.'+name,return_value=False)
            p.start();self.addCleanup(p.stop)
        self.runtime.slots[1]=ClientSlot(1,dict(resolution='1920x1080'),status='waiting')
    def test_pause_only_owned_idle_instance_one_and_resume(self):
        snapshot=self.runtime.pause_idle_for_enrollment()
        self.assertNotIn(1,self.runtime.slots)
        self.assertTrue(self.runtime.enrollment_pause_complete(snapshot))
        with patch.object(self.runtime,'start_slot') as start:
            self.runtime.resume_enrollment_pause(snapshot,instances=(1,))
            start.assert_called_once_with(1,dict(resolution='1920x1080'))
    def test_active_requests_and_second_listener_block_before_mutation(self):
        for field,value in (('owner','client'),('request_id','request'),('target',{'width':1920}),('status','streaming')):
            slot=self.runtime.slots[1];original=getattr(slot,field);setattr(slot,field,value)
            with self.assertRaises(ValueError):self.runtime.pause_idle_for_enrollment()
            self.assertIn(1,self.runtime.slots);setattr(slot,field,original)
        self.runtime.slots[2]=ClientSlot(2,{},status='waiting')
        with self.assertRaisesRegex(ValueError,'Display 2'):self.runtime.pause_idle_for_enrollment()
        self.assertEqual(set(self.runtime.slots),{1,2})
    def test_explicit_stop_cancels_pause_resume_intent(self):
        snapshot=self.runtime.pause_idle_for_enrollment();self.runtime.stop()
        with patch.object(self.runtime,'start_slot') as start:
            self.runtime.resume_enrollment_pause(snapshot,instances=(1,));start.assert_not_called()
    def test_new_client_prepare_is_blocked_while_paused(self):
        self.runtime.pause_idle_for_enrollment()
        with self.assertRaisesRegex(ValueError,'setup'):
            self.runtime._request(None,dict(operation='prepare'))


class DisableCompatibilityRecoveryTests(unittest.TestCase):
    def test_incompatible_host_blocks_enable_but_not_authenticated_disable(self):
        from monitorize.platform import system_setup
        status = dict(available=False, live_available=False, enabled=True, message='Upgrade host')
        with patch.object(system_setup, 'remote_login_setup_status', return_value=status), \
             patch.object(system_setup.shutil, 'which', return_value='/usr/bin/pkexec'), \
             patch.object(system_setup.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"success": true}'
            run.return_value.stderr = ''
            self.assertFalse(system_setup.apply_remote_login_setup(True)['success'])
            run.assert_not_called()
            self.assertTrue(system_setup.apply_remote_login_setup(False)['success'])
            self.assertEqual(run.call_args.args[0][-2:], ['--remote-login', 'disable'])
