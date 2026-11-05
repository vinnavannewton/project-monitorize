import unittest
from unittest.mock import Mock
from PyQt6.QtCore import QCoreApplication
from monitorize.desktop.restart_transaction import RestartTransaction


class RestartTransactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.now=0;self.stopped=False;self.ready=False;self.failure=''
        self.stop=Mock();self.launch=Mock();self.commit=Mock();self.finished=Mock()
        self.tx=RestartTransaction(self.stop,lambda:self.stopped,self.launch,lambda:self.ready,lambda:self.failure,self.commit,clock=lambda:self.now)
        self.tx.finished.connect(self.finished);self.addCleanup(self.tx.cancel)
        self.old={'v':1};self.new={'v':2}
    def begin(self):self.assertTrue(self.tx.begin(self.new,self.old))
    def launch_candidate(self):self.stopped=True;self.tx.tick()
    def test_waits_for_cleanup_and_readiness(self):
        self.begin();self.tx.tick();self.launch.assert_not_called()
        self.launch_candidate();self.launch.assert_called_once_with(self.new)
        self.tx.tick();self.commit.assert_not_called()
        self.ready=True;self.tx.tick();self.commit.assert_called_once_with(self.new)
        self.assertFalse(self.tx.busy);self.finished.assert_called_once_with(True,'Changes applied. Session is ready.')
    def test_rapid_clicks_do_not_queue(self):
        self.begin();self.assertFalse(self.tx.begin(self.new,self.old));self.stop.assert_called_once()
    def test_cancel_invalidates_pending_tick(self):
        self.begin();self.tx.cancel();self.stopped=True;self.tx.tick();self.launch.assert_not_called()
    def test_cleanup_timeout_never_starts_successor(self):
        self.begin();self.now=31;self.tx.tick();self.launch.assert_not_called();self.assertFalse(self.tx.busy)
    def test_failed_cleanup_ends_restart_immediately_with_recovery_reason(self):
        self.begin()
        self.tx.stopped = Mock(side_effect=RuntimeError('Display recovery failed'))
        self.tx.tick()
        self.finished.assert_called_once_with(False, 'Display recovery failed')
        self.launch.assert_not_called()
        self.assertFalse(self.tx.busy)

    def test_failure_restores_previous_once(self):
        self.begin();self.launch_candidate();self.failure='bad encoder';self.tx.tick()
        self.failure='';self.tx.tick();self.assertEqual(self.launch.call_args.args[0],self.old)
        self.ready=True;self.tx.tick();self.commit.assert_not_called();self.assertFalse(self.finished.call_args.args[0])
    def test_rollback_failure_does_not_loop(self):
        self.begin();self.launch_candidate();self.failure='bad';self.tx.tick();self.tx.tick();self.tx.tick()
        self.assertFalse(self.tx.busy);self.assertEqual(self.launch.call_count,2)
    def test_restart_does_not_publish_draft(self):
        self.tx.begin(self.old,self.old,publish=False);self.launch_candidate();self.ready=True;self.tx.tick();self.commit.assert_not_called()
    def test_persistence_failure_rolls_back(self):
        self.begin();self.launch_candidate();self.commit.side_effect=OSError('disk full');self.ready=True;self.tx.tick()
        self.assertTrue(self.tx.rollback);self.tx.tick();self.tx.tick();self.assertFalse(self.tx.busy)

    def test_cancel_during_stop_does_not_rearm_timer(self):
        self.stop.side_effect=self.tx.cancel
        self.assertFalse(self.tx.begin(self.new,self.old))
        self.assertFalse(self.tx.busy)
        self.assertFalse(self.tx.timer.isActive())

    def test_failure_without_verified_previous_configuration_stays_stopped(self):
        self.tx.begin(self.new, None)
        self.launch_candidate();self.failure='capture is incompatible';self.tx.tick()
        self.failure='';self.tx.tick();self.tx.tick()
        self.assertFalse(self.tx.busy)
        self.assertEqual(self.launch.call_count,1)
        self.assertFalse(self.finished.call_args.args[0])
        self.assertIn('capture is incompatible',self.finished.call_args.args[1])

    def test_restart_failure_does_not_retry_the_identical_broken_configuration(self):
        self.tx.begin(self.old,self.old,publish=False)
        self.launch_candidate();self.failure='broken';self.tx.tick();self.tx.tick();self.tx.tick()
        self.assertFalse(self.tx.busy)
        self.assertEqual(self.launch.call_count,1)
