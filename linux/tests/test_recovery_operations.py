import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt6.QtCore import QObject,pyqtSignal
from PyQt6.QtWidgets import QApplication
from monitorize.desktop.recovery_operations import RecoveryOperations

APP=QApplication.instance() or QApplication([])


class Backend(QObject):
    remoteLoginSetupChanged=pyqtSignal()
    recoveryOperationFinished=pyqtSignal(object)
    remoteLoginSetupFinished=pyqtSignal(object)
    def __init__(self):
        super().__init__();self._closing=False;self._session_start_generation=0
        self.applyingConfiguration=False;self.systemSetupRunning=False;self.remoteLoginEnabled=False
        self.session=SimpleNamespace(busy=False,active=False,client_runtime=None)
        self.streaming=SimpleNamespace(streaming=False,third_streaming=False)
        self._remote_login=SimpleNamespace(status={},busy=False,enabled=False,timer=Mock(),stop=Mock(),participant=SimpleNamespace(want_running=True))
        self._background_executor=Mock();self.future=Future();self._background_executor.submit.return_value=self.future
        self._owned_session_stopped=Mock(return_value=False)
        self.refreshVkmsHelperAvailability=Mock();self.refreshVkmsAuthorization=Mock()
    def _stop_owned_session(self):self._session_start_generation+=1


class RecoveryOperationsTests(unittest.TestCase):
    def test_active_session_requires_matching_choice_and_verified_stop(self):
        b=Backend();b.streaming.streaming=True;c=RecoveryOperations(b);events=[]
        c.decisionRequested.connect(events.append);result=c.begin('host_recovery')
        self.assertTrue(result['pending']);self.assertEqual(len(events),1)
        b._background_executor.submit.assert_not_called()
        c.resolve('stale',True);self.assertEqual(b._session_start_generation,0)
        c.resolve(result['request_id'],True);b._background_executor.submit.assert_not_called()
        b._owned_session_stopped.return_value=True;c.advance();b._background_executor.submit.assert_called_once()
        b.future.set_result(dict(success=True));self.assertFalse(c.busy)
        b.refreshVkmsHelperAvailability.assert_called_once()

    def test_decline_does_not_stop_or_authorize(self):
        b=Backend();b.streaming.streaming=True;c=RecoveryOperations(b)
        result=c.begin('source_repair');c.resolve(result['request_id'],False)
        self.assertFalse(c.busy);self.assertEqual(b._session_start_generation,0)
        b._background_executor.submit.assert_not_called()

    def test_new_intent_invalidates_old_decision(self):
        b=Backend();b.streaming.streaming=True;c=RecoveryOperations(b)
        result=c.begin('host_recovery');b._session_start_generation+=1;c.resolve(result['request_id'],True)
        self.assertFalse(c.busy);b._background_executor.submit.assert_not_called()

    def test_stop_cancels_continuation_without_replaying_helper(self):
        b=Backend();c=RecoveryOperations(b);result=c.begin('host_recovery');c.cancel()
        self.assertTrue(c.inflight)
        b.future.set_result(dict(success=True))
        self.assertFalse(c.busy);self.assertEqual(b._background_executor.submit.call_count,1)
        self.assertFalse(b._remote_login.participant.want_running);b._remote_login.stop.assert_called_once()

    def test_session_end_always_requires_explicit_decision(self):
        b=Backend();c=RecoveryOperations(b);c.begin('logout')
        self.assertTrue(c.waiting);b._background_executor.submit.assert_not_called();c.cancel()


    def test_declining_session_end_does_not_pause_idle_listener(self):
        b=Backend();runtime=Mock(active=True);b.session.client_runtime=runtime
        c=RecoveryOperations(b);request=c.begin('logout');c.resolve(request['request_id'],False)
        runtime.pause_idle_for_enrollment.assert_not_called()
        self.assertFalse(c.busy)

    def test_declining_after_idle_pause_error_cleans_state(self):
        b=Backend();runtime=Mock(active=True);runtime.pause_idle_for_enrollment.side_effect=ValueError('needs stop')
        b.session.client_runtime=runtime;c=RecoveryOperations(b)
        request=c.begin('host_recovery');c.resolve(request['request_id'],False)
        self.assertFalse(c.busy);b._background_executor.submit.assert_not_called()
