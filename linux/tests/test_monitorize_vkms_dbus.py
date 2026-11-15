"""Host-service protocol, explicit identity, and per-slot removal."""
import json
import unittest
from unittest.mock import Mock, patch
from monitorize.platform.monitorize_vkms_dbus import (
    MonitorizeVkmsClient, VkmsProtocolError, VkmsCommandError, CREATE_TIMEOUT, REMOVE_TIMEOUT,
    VkmsServiceUnavailableError, VkmsCliNotFoundError,
)

class VkmsDbusTest(unittest.TestCase):
    @staticmethod
    def bus_error(name):
        error = RuntimeError('bus connection failed')
        error.get_dbus_name = lambda: 'org.freedesktop.DBus.Error.' + name
        return error

    def test_discovery_recovers_once_from_disconnect_with_fresh_connection(self):
        self.client._bus = Mock()
        old_bus = self.client._bus
        self.service.GetCapabilities.side_effect = [self.bus_error('NoReply'), json.dumps(self.capabilities)]
        with patch('monitorize.platform.monitorize_vkms_dbus.time.sleep'):
            self.client.require_ready()
        old_bus.close.assert_called_once()
        self.assertEqual(self.service.GetCapabilities.call_count, 2)
        self.assertIsNone(self.client._bus)

    def test_persistent_transport_failure_is_bounded_and_not_missing_package(self):
        self.service.GetCapabilities.side_effect = self.bus_error('Disconnected')
        with patch('monitorize.platform.monitorize_vkms_dbus.time.sleep'), self.assertRaises(VkmsServiceUnavailableError):
            self.client.require_ready()
        self.assertEqual(self.service.GetCapabilities.call_count, 2)

    def test_missing_service_does_not_retry(self):
        self.service.GetCapabilities.side_effect = self.bus_error('ServiceUnknown')
        with self.assertRaises(VkmsCliNotFoundError):
            self.client.require_ready()
        self.service.GetCapabilities.assert_called_once()

    def test_mutations_never_replay_after_transport_failure(self):
        for method, operation in (
            ('CreateDisplay', lambda: self.client.create_display(1920, 1080, 60, display='mon2')),
            ('DestroyDisplay', lambda: self.client.remove_display('mon2', 'token2')),
        ):
            with self.subTest(method=method):
                call = getattr(self.service, method)
                call.side_effect = self.bus_error('NoReply')
                with self.assertRaises(VkmsServiceUnavailableError):
                    operation()
                call.assert_called_once()

    def test_authorization_transport_failure_does_not_suggest_installation(self):
        self.service.GetAuthorizationState.side_effect = self.bus_error('NoReply')
        with self.assertRaises(VkmsServiceUnavailableError) as error:
            self.client.get_authorization_state()
        self.assertNotIn('Install', str(error.exception))

    def test_missing_authorization_method_is_upgrade_error_not_denial(self):
        error=RuntimeError('Unknown method: GetAuthorizationState is not a valid method')
        error.get_dbus_name=lambda:'org.freedesktop.DBus.Error.UnknownMethod'
        self.service.GetAuthorizationState.side_effect=error
        with self.assertRaises(VkmsProtocolError) as caught:self.client.get_authorization_state()
        self.assertIn('Update',str(caught.exception))
        self.assertNotIn('denied',str(caught.exception))

    def test_unattended_create_uses_only_silent_method(self):
        self.service.CreateDisplayUnattended.return_value = json.dumps(self.created)
        self.client.create_display(1920, 1080, 60, display='mon2', unattended=True)
        self.service.CreateDisplayUnattended.assert_called_once()
        self.service.CreateDisplay.assert_not_called()

    def test_missing_or_revoked_authorization_is_not_ready(self):
        self.service.GetAuthorizationState.return_value = json.dumps({'success': True, 'authorization_version': 1, 'unattended_enabled': False})
        with self.assertRaises(VkmsCommandError):
            self.client.require_unattended()
        self.service.CreateDisplay.assert_not_called()

    def test_authentication_failures_are_explained_for_bus_errors_and_json_responses(self):
        for detail in ("No authentication agent found", "Polkit denied the Monitorize VKMS request."):
            with self.subTest(detail=detail):
                self.service.CreateDisplay.side_effect = PermissionError(detail)
                with self.assertRaises(VkmsCommandError) as error:
                    self.client.create_display(1920, 1080, 60, display="mon2")
                self.assertIn("authentication agent", str(error.exception))
                self.service.CreateDisplay.side_effect = None
                self.service.CreateDisplay.return_value = json.dumps({"success": False, "message": detail})
                with self.assertRaises(VkmsCommandError) as error:
                    self.client.create_display(1920, 1080, 60, display="mon2")
                self.assertIn("authentication agent", str(error.exception))

    def setUp(self):
        self.service = Mock()
        self.client = MonitorizeVkmsClient()
        self.client._interface = Mock(return_value=self.service)
        self.capabilities = {"success":True, "api_version":1, "displays":["mon1","mon2"],
                             "topology_ready":True, "capability":"supported"}
        self.service.GetCapabilities.return_value = json.dumps(self.capabilities)
        self.created = {"success":True,"slot":"mon2","connector":"Virtual-9","card":"card4",
                        "generation":"token2","created":True}
        self.service.CreateDisplay.return_value = json.dumps(self.created)

    def test_two_slot_capability_is_required(self):
        self.client.require_ready()
        self.capabilities["displays"] = ["mon1"]
        self.service.GetCapabilities.return_value = json.dumps(self.capabilities)
        with self.assertRaises(VkmsProtocolError):
            self.client.require_ready()

    def test_old_topology_has_reboot_message(self):
        self.capabilities["topology_ready"] = False
        self.capabilities["message"] = "Reboot after installing"
        self.service.GetCapabilities.return_value = json.dumps(self.capabilities)
        with self.assertRaisesRegex(VkmsCommandError, "Reboot"):
            self.client.require_ready()

    def test_create_preserves_id_connector_generation_and_decimal_refresh(self):
        result = self.client.create_display(2340,1080,59.94,display="mon2")
        self.service.CreateDisplay.assert_called_once_with("mon2",2340,1080,59.94,timeout=CREATE_TIMEOUT)
        self.assertEqual((result["display"],result["name"],result["generation"],result["fps"]),
                         ("mon2","Virtual-9","token2",59.94))

    def test_repeated_create_retains_created_false(self):
        self.created["created"] = False
        self.service.CreateDisplay.return_value = json.dumps(self.created)
        self.assertFalse(self.client.create_display(1920,1080,60,display="mon2")["created"])

    def test_wrong_slot_or_missing_generation_is_rejected(self):
        for field,value in (("slot","mon1"),("generation",None),("created",1),("connector","")):
            data=dict(self.created);data[field]=value
            self.service.CreateDisplay.return_value = json.dumps(data)
            with self.subTest(field=field), self.assertRaises(VkmsProtocolError):
                self.client.create_display(1920,1080,60,display="mon2")

    def test_normal_cleanup_requires_and_sends_generation(self):
        self.service.DestroyDisplay.return_value = '{"success":true}'
        self.client.remove_display("mon2","token2")
        self.service.DestroyDisplay.assert_called_once_with("mon2","token2",timeout=REMOVE_TIMEOUT)
        with self.assertRaises(ValueError):
            self.client.remove_display("mon1","")
        self.service.DestroyAll.assert_not_called()

    def test_remove_all_preserves_partial_result(self):
        payload={"success":False,"results":{"mon1":{"changed":True},"mon2":{"error":"unsafe"}}}
        self.service.DestroyAll.return_value=json.dumps(payload)
        self.assertEqual(self.client.remove_all(),payload)

    def test_malformed_and_failed_responses_never_look_successful(self):
        for response in ('[]','not json','{"success":1}','{"success":false,"message":"denied"}'):
            self.service.GetCapabilities.return_value=response
            with self.subTest(response=response), self.assertRaises((VkmsProtocolError,VkmsCommandError)):
                self.client.require_ready()

    def test_invalid_mode_is_rejected_before_bus_call(self):
        for width,fps in ((9000,60),(1920,float("nan")),(1920,0)):
            with self.assertRaises(ValueError):
                self.client.create_display(width,1080,fps,display="mon2")
        self.service.CreateDisplay.assert_not_called()

    def test_bus_sender_uses_private_connection(self):
        bus=Mock()
        with patch("dbus.SystemBus",return_value=bus) as system_bus, patch("dbus.Interface"):
            client=MonitorizeVkmsClient()
            client._interface();client._interface()
        system_bus.assert_called_once_with(private=True)
