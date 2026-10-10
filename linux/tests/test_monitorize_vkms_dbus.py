"""Host-service protocol, explicit identity, and per-slot removal."""
import json
import unittest
from unittest.mock import Mock, patch
from monitorize.platform.monitorize_vkms_dbus import (
    MonitorizeVkmsClient, VkmsProtocolError, VkmsCommandError, CREATE_TIMEOUT, REMOVE_TIMEOUT,
)

class VkmsDbusTest(unittest.TestCase):
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
