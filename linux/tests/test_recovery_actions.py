import unittest
from dataclasses import FrozenInstanceError
from monitorize.platform.recovery_actions import RecoveryRequest


class RecoveryRequestTests(unittest.TestCase):
    def test_disable_is_immutable_and_preserved_in_results(self):
        request=RecoveryRequest('boot_startup',1,False)
        with self.assertRaises(FrozenInstanceError):request.enabled=True
        result=request.result({'success':True,'configured':False,'requested_enabled':True})
        self.assertIs(result['requested_enabled'],False)
        self.assertEqual(result['outcome'],'success')
        self.assertEqual(result['request_id'],request.request_id)

    def test_invalid_or_ambiguous_intent_is_rejected(self):
        for enabled in (None,0,1,'false'):
            with self.assertRaises(ValueError):RecoveryRequest('boot_startup',0,enabled)
        with self.assertRaises(ValueError):RecoveryRequest('shell',0,False)

    def test_partial_and_cancelled_results_do_not_report_success(self):
        request=RecoveryRequest('maintenance_resume',0)
        self.assertEqual(request.result({'success':False,'cleanup_pending':True})['outcome'],'partial')
        self.assertEqual(request.result({'success':False,'cancelled':True})['outcome'],'cancelled')
