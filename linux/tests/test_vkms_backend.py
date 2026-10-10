"""Independent slot ownership, cancellation, and capture lifecycle."""
import io
import json
import signal
import unittest
from unittest.mock import MagicMock, patch
from monitorize.platform import vkms_backend
from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient, MonitorizeVkmsError, VkmsCommandError

class VkmsBackendTest(unittest.TestCase):
    def client(self, display="mon1", width=1920, height=1080):
        client = MagicMock(spec=MonitorizeVkmsClient)
        client.create_display.return_value = {"name": "Virtual-7", "card": "card4", "width": width,
            "height": height, "fps": 60, "display": display, "generation": "g-" + display, "created": True}
        client.remove_display.return_value = {"success": True}
        return client

    def run_holder(self, client, slot="primary", desktop="kde", polls=None):
        output = io.StringIO()
        stdin = io.StringIO("quit\n")
        with patch("sys.stdout", output), patch("sys.stdin", stdin), patch("select.select", side_effect=polls or [([], [], []), ([stdin], [], [])]):
            result = vkms_backend.run_vkms_headless(slot, 1920, 1080, 60, desktop, client=client)
        return result, output.getvalue()

    def test_both_slots_create_and_remove_only_their_generation(self):
        for slot, display in (("primary", "mon1"), ("additional", "mon2")):
            with self.subTest(slot=slot):
                client = self.client(display)
                code, output = self.run_holder(client, slot)
                self.assertEqual(code, 0)
                client.create_display.assert_called_once_with(1920, 1080, 60, display=display)
                client.remove_display.assert_called_once_with(display, "g-" + display)
                event = json.loads(next(line.split(" ", 1)[1] for line in output.splitlines() if line.startswith("MONITORIZE_EVENT ")))
                self.assertEqual(event["display"], display)
                self.assertEqual(event["name"], "Virtual-7")
                self.assertEqual(event["card"], "card4")

    def test_retry_does_not_claim_or_remove_an_existing_holder(self):
        client = self.client()
        client.create_display.return_value["created"] = False
        code, output = self.run_holder(client)
        self.assertEqual(code, 1)
        self.assertNotIn("headless_ready", output)
        client.remove_display.assert_not_called()

    def test_stop_during_create_removes_late_result_before_ready(self):
        client = self.client("mon2")
        handlers = {}
        def created(*args, **kwargs):
            handlers[signal.SIGTERM]()
            return {"name":"Virtual-8", "card":"card4", "width":1920, "height":1080, "fps":60,
                    "created":True, "generation":"late", "display":"mon2"}
        client.create_display.side_effect = created
        with patch.object(vkms_backend.signal, "signal", side_effect=lambda signum, handler: handlers.update({signum:handler})):
            code, output = self.run_holder(client, "additional")
        self.assertEqual(code, 0)
        self.assertNotIn("headless_ready", output)
        client.remove_display.assert_called_once_with("mon2", "late")

    def test_queued_quit_after_create_does_not_emit_ready(self):
        client = self.client()
        stdin = io.StringIO("quit\n")
        code, output = self.run_holder(client, polls=[([stdin], [], [])])
        self.assertEqual(code, 0)
        self.assertNotIn("headless_ready", output)
        client.remove_display.assert_called_once_with("mon1", "g-mon1")

    def test_gnome_capture_failure_removes_only_its_created_slot(self):
        client = self.client("mon2")
        with patch("monitorize.platform.gnome_monitor_capture.GnomeMonitorCapture") as capture:
            capture.return_value.start.side_effect = RuntimeError("No capture stream")
            code, output = self.run_holder(client, "additional", "gnome")
        self.assertEqual(code, 1)
        self.assertNotIn("headless_ready", output)
        capture.return_value.close.assert_called_once()
        client.remove_display.assert_called_once_with("mon2", "g-mon2")

    def test_gnome_existing_output_node_is_emitted(self):
        with patch("monitorize.platform.gnome_monitor_capture.GnomeMonitorCapture") as capture:
            capture.return_value.start.return_value = {"node_id":42, "offset_x":1920, "offset_y":0}
            code, output = self.run_holder(self.client(), desktop="gnome")
        self.assertEqual(code, 0)
        capture.return_value.start.assert_called_once_with("Virtual-7")
        capture.return_value.close.assert_called_once()
        self.assertIn('"node_id":42', output)

    def test_failure_before_create_never_removes(self):
        client = self.client()
        client.require_ready.side_effect = MonitorizeVkmsError("Upgrade host service")
        code, output = self.run_holder(client)
        self.assertEqual(code, 1)
        client.create_display.assert_not_called()
        client.remove_display.assert_not_called()

    def test_create_failure_never_removes_unknown_slot(self):
        client = self.client()
        client.create_display.side_effect = VkmsCommandError("occupied")
        self.assertEqual(self.run_holder(client)[0], 1)
        client.remove_display.assert_not_called()

    def test_cleanup_failure_is_reported(self):
        client = self.client()
        client.remove_display.return_value = {"success":False, "message":"unsafe removal"}
        code, output = self.run_holder(client)
        self.assertEqual(code, 1)
        self.assertIn("unsafe removal", output)
        client.remove_display.assert_called_once()

    def test_unknown_slot_rejected_before_service_access(self):
        client = self.client()
        code, _ = self.run_holder(client, "third")
        self.assertEqual(code, 1)
        client.require_ready.assert_not_called()

    def test_bootstrap_error_recognized(self):
        self.assertTrue(vkms_backend._reinstall_required(VkmsCommandError(
            "Two-display Monitorize VKMS bootstrap is not initialized.", "helper_error")))

    def test_install_page(self):
        with patch("webbrowser.open", return_value=True):
            self.assertTrue(vkms_backend.open_monitorize_vkms_install_page())
