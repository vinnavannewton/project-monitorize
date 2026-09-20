import unittest
from unittest.mock import Mock, patch

from monitorize.platform import virtual_display_cleanup as cleanup


class VirtualDisplayCleanupTest(unittest.TestCase):
    def test_exact_helper_matching(self):
        self.assertTrue(cleanup._is_display_owner(['/usr/bin/python3', '-m', 'monitorize.streaming.headless_virtual_display', '1920']))
        self.assertTrue(cleanup._is_display_owner(['/opt/monitorize-kde-virtual-output', 'Monitorize-1']))
        self.assertFalse(cleanup._is_display_owner(['/bin/sh', '-c', 'python3 -m monitorize.streaming.headless_virtual_display']))
        self.assertFalse(cleanup._is_display_owner(['/usr/bin/python3', '-c', 'print("monitorize.streaming.headless_virtual_display")']))
        self.assertFalse(cleanup._is_display_owner(['/usr/bin/gnome-shell']))

    def run_cleanup(self, desktop, connected=True, removal=None, owner_result=None, native=0):
        client = Mock()
        client.is_available.return_value = True
        client.get_status.return_value = {'topology': {'connector0_connected': connected}}
        client.remove_display.return_value = removal or {'success': True}
        with (patch.object(cleanup, '_close_display_owners', return_value=owner_result or (0, [])),
              patch.object(cleanup, 'DisplayController') as controller,
              patch.object(cleanup, 'MonitorizeVkmsClient', return_value=client)):
            controller.return_value.remove_stagnant_virtual_displays.return_value = native
            result = cleanup.remove_virtual_displays(desktop)
            controller.assert_called_once_with(desktop)
        return result, client

    def test_vkms_cleanup_runs_on_every_desktop(self):
        for desktop in ('gnome', 'kde', 'hyprland', 'sway', ''):
            with self.subTest(desktop=desktop):
                result, client = self.run_cleanup(desktop)
                self.assertTrue(result['success'])
                client.remove_display.assert_called_once()

    def test_disconnected_vkms_does_not_request_authorization(self):
        result, client = self.run_cleanup('gnome', connected=False)
        client.remove_display.assert_not_called()
        self.assertEqual(result, {'success': True, 'message': 'No virtual displays found'})

    def test_compositor_owner_cleanup_is_reported_without_vkms(self):
        result, _ = self.run_cleanup('kde', connected=False, owner_result=(1, []))
        self.assertEqual(result['message'], 'Removed virtual displays')

    def test_partial_failure_is_not_reported_as_success(self):
        result, _ = self.run_cleanup('hyprland', native=2, removal={'success': False, 'message': 'Authorization cancelled'})
        self.assertFalse(result['success'])
        self.assertIn('Authorization cancelled', result['message'])

    def test_missing_vkms_package_still_cleans_compositor(self):
        with (patch.object(cleanup, '_close_display_owners', return_value=(1, [])),
              patch.object(cleanup, 'DisplayController') as controller,
              patch.object(cleanup, 'MonitorizeVkmsClient') as client):
            client.return_value.is_available.return_value = False
            controller.return_value.remove_stagnant_virtual_displays.return_value = 0
            result = cleanup.remove_virtual_displays('gnome')
        self.assertTrue(result['success'])
        client.return_value.get_status.assert_not_called()
