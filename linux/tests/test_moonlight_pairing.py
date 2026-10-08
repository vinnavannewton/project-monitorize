"""PIN acceptance must not be confused with completed Moonlight verification."""
import json
import unittest
from unittest.mock import MagicMock, patch

from monitorize.platform import sunshine_service as service


class MoonlightPairingTest(unittest.TestCase):
    def pair(self, responses, running=(1, 2)):
        def reply(request, **kwargs):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(responses.pop(0)).encode()
            self.assertEqual(kwargs["timeout"], 15.0)
            self.assertEqual(json.loads(request.data)["pin"], "0123")
            return response

        with patch.object(service, "is_sunshine_running", side_effect=lambda instance: instance in running), patch(
            "urllib.request.urlopen", side_effect=reply
        ) as request:
            result = service.pair_moonlight_pin("0123", instance=1)
            return result, request.call_count

    def test_only_completed_handshake_reports_success(self):
        result, count = self.pair([{"status": True, "pairing_complete": True}])
        self.assertTrue(result[0])
        self.assertEqual(count, 1)

    def test_old_binary_acceptance_does_not_report_success(self):
        result, count = self.pair([{"status": True}])
        self.assertFalse(result[0])
        self.assertIn("not yet confirmed", result[1])
        self.assertEqual(count, 1)

    def test_false_completion_does_not_report_success(self):
        result, _ = self.pair([{"status": True, "pairing_complete": False}])
        self.assertFalse(result[0])

    def test_no_pending_request_falls_back_to_second_display(self):
        result, count = self.pair([
            {"status": False, "error_code": "no_pending"},
            {"status": True, "pairing_complete": True},
        ])
        self.assertTrue(result[0])
        self.assertEqual(count, 2)

    def test_failed_or_ambiguous_attempt_never_sends_pin_to_other_display(self):
        for code in ("pairing_failed", "pairing_timeout", "ambiguous_pending", "invalid_pin"):
            with self.subTest(code=code):
                result, count = self.pair([{"status": False, "error_code": code, "error": "Retry in Moonlight"}])
                self.assertEqual(result, (False, "Retry in Moonlight"))
                self.assertEqual(count, 1)

    def test_invalid_pin_does_not_contact_api(self):
        with patch("urllib.request.urlopen") as request:
            self.assertFalse(service.pair_moonlight_pin("12x3")[0])
            request.assert_not_called()

    def test_stopped_instances_are_not_contacted(self):
        result, count = self.pair([], running=())
        self.assertFalse(result[0])
        self.assertEqual(count, 0)

    def test_unknown_failure_does_not_send_pin_to_other_display(self):
        result, count = self.pair([{"status": False}])
        self.assertFalse(result[0])
        self.assertEqual(count, 1)

    def test_connection_failure_does_not_send_pin_to_other_display(self):
        with patch.object(service, "is_sunshine_running", return_value=True), patch(
            "urllib.request.urlopen", side_effect=TimeoutError("late response")
        ) as request:
            self.assertFalse(service.pair_moonlight_pin("0123", instance=1)[0])
            self.assertEqual(request.call_count, 1)
