import json
import os
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication


class ClientDisplayRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        from monitorize.desktop.client_display_runtime import ClientDisplayRuntime
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = ClientDisplayRuntime(runtime_dir=self.temp.name)
        self.addCleanup(self.runtime.stop)
        for name, value in [('supports_control_protocol', True), ('sync_sunshine_stream_config', (True, '')), ('save_sunshine_config', (True, '')), ('start_sunshine', (True, '')), ('get_sunshine_process', SimpleNamespace(pid=os.getpid())), ('stop_sunshine', None)]:
            p = patch('monitorize.desktop.client_display_runtime.' + name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        readiness = patch.object(self.runtime, '_startup_status', return_value=('ready', ''))
        readiness.start()
        self.addCleanup(readiness.stop)
        p = patch('monitorize.desktop.client_display_runtime.is_sunshine_running', return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(self.runtime.start_slot(1, {'de': 'kde'}))

    def request(self, **changes):
        req = dict(version=1, token=self.runtime.token, instance=1, operation='prepare', client_id='alice', request_id='one', width=1920, height=1080, fps=60)
        req.update(changes)
        return req

    def test_hyprland_vkms_requires_host_lifecycle_capability_before_listener(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsProtocolError
        self.runtime.stop()
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient') as factory, patch('monitorize.desktop.client_display_runtime.start_sunshine') as launch:
            factory.return_value.require_managed_backend.side_effect=VkmsProtocolError('Update host for Hyprland lifecycle')
            self.assertFalse(self.runtime.start_slot(1,{'de':'hyprland','virtual_display_creator':'vkms'}))
            launch.assert_not_called()

    def test_generic_x11_requires_host_xrandr_capability_before_listener(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsProtocolError
        self.runtime.stop()
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient') as factory, patch('monitorize.desktop.client_display_runtime.start_sunshine') as launch:
            factory.return_value.require_managed_backend.side_effect=VkmsProtocolError('Update host for X11 lifecycle')
            self.assertFalse(self.runtime.start_slot(1,{'de':'x11','virtual_display_creator':'vkms'}))
            launch.assert_not_called()

    def test_kms_listener_reports_command_before_launch(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        message = 'KMS capture requires cap_sys_admin. Run: sudo setcap cap_sys_admin+p /test/sunshine'
        slot = ClientSlot(2, {'de': 'kde', 'capture': 'kms', 'virtual_display_creator': 'vkms'})
        with patch('monitorize.platform.sunshine_service.get_sunshine_kms_setup_error', return_value=message), patch('monitorize.desktop.client_display_runtime.start_sunshine', return_value=(True, '')) as start:
            self.assertFalse(self.runtime._launch(slot))
        start.assert_not_called()
        self.assertTrue(slot.permanent_failure)
        self.assertEqual(slot.error, message)
        self.assertEqual(self.runtime.error, message)

    def test_native_gnome_kms_is_rejected_before_launching_a_listener(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        slot=ClientSlot(2,{'de':'gnome','capture':'kms','virtual_display_creator':'native'})
        with patch('monitorize.platform.sunshine_service.get_sunshine_kms_setup_error',return_value=''), patch('monitorize.desktop.client_display_runtime.start_sunshine',return_value=(True,'')) as launch:
            self.assertFalse(self.runtime._launch(slot))
        launch.assert_not_called()
        self.assertIn('Auto',slot.error)

    def test_kms_manual_card_reports_command_before_creating_display(self):
        message = 'Run: sudo setcap cap_sys_admin+p /test/sunshine'
        with patch('monitorize.platform.sunshine_service.get_sunshine_kms_setup_error', return_value=message), patch.object(self.runtime, '_prepare') as prepare:
            self.assertFalse(self.runtime.start_manual_slot(2, {'de': 'kde', 'capture': 'kms', 'virtual_display_creator': 'vkms', 'resolution': '1920x1080', 'fps': '60'}))
        prepare.assert_not_called()
        self.assertEqual(self.runtime.error, message)

    def test_kms_auto_vkms_capture_is_checked_too(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        slot = ClientSlot(2, {'de': 'gnome', 'virtual_display_creator': 'vkms', 'capture': 'auto'})
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient'), patch('monitorize.platform.sunshine_service.get_sunshine_kms_setup_error', return_value='Run: sudo setcap cap_sys_admin+p /test/sunshine') as check:
            self.assertFalse(self.runtime._launch(slot))
        check.assert_called_once_with(2)

    def exchange(self, request):
        client = socket.socket(socket.AF_UNIX)
        self.addCleanup(client.close)
        client.connect(self.runtime.socket_path)
        client.sendall(json.dumps(request).encode() + b'\n')
        client.setblocking(False)
        data = b''
        deadline = time.monotonic() + 1
        while b'\n' not in data and time.monotonic() < deadline:
            self.app.processEvents()
            try:
                data += client.recv(65536)
            except BlockingIOError:
                time.sleep(.001)
        return json.loads(data)

    def test_untrusted_socket_request_cannot_create_display(self):
        result = self.exchange(self.request(token='wrong'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.runtime.slots[1].status, 'waiting')
        self.assertIsNone(self.runtime.slots[1].process)

    def test_invalid_modes_rejected_without_allocating_owner(self):
        for changes in [dict(width=0), dict(fps=float('nan')), dict(width=True), dict(height=40000)]:
            result = self.exchange(self.request(**changes))
            self.assertFalse(result['ok'])
            self.assertEqual(self.runtime.slots[1].owner, '')

    def test_release_from_another_client_cannot_remove_owned_display(self):
        slot = self.runtime.slots[1]
        slot.owner = 'alice'
        slot.status = 'streaming'
        result = self.exchange(self.request(operation='release', client_id='bob'))
        self.assertFalse(result['ok'])
        self.assertEqual(slot.status, 'streaming')

    def test_release_returns_only_requested_slot_to_waiting(self):
        self.assertTrue(self.runtime.start_slot(2, {'de': 'kde'}))
        self.runtime.slots[1].owner = 'alice'
        self.runtime.slots[2].owner = 'bob'
        self.runtime.slots[2].status = 'streaming'
        result = self.exchange(self.request(operation='release'))
        self.assertTrue(result['ok'])
        self.assertEqual(self.runtime.slots[1].status, 'waiting')
        self.assertEqual(self.runtime.slots[2].status, 'streaming')

    def test_runtime_socket_directory_private_and_stop_removes_socket(self):
        self.assertEqual(os.stat(os.path.dirname(self.runtime.socket_path)).st_mode & 0o777, 0o700)
        self.runtime.stop()
        self.assertFalse(os.path.exists(self.runtime.socket_path))

    def test_unsupported_backend_does_not_start_listener(self):
        self.assertFalse(self.runtime.start_slot(2, {'de': 'unknown'}))
        self.assertNotIn(2, self.runtime.slots)

    def test_app_selected_mode_starts_listener_without_display(self):
        with patch.object(self.runtime,'_prepare') as prepare:
            self.assertTrue(self.runtime.start_slot(2,dict(de='kde',use_client_display_settings=False,
                resolution='2304x1440',fps='90')))
            prepare.assert_not_called()
            self.assertFalse(self.runtime.slots[2].target)
            self.assertEqual(self.runtime.slots[2].status,'waiting')

    def test_app_selected_mode_is_used_after_authenticated_prepare(self):
        slot=self.runtime.slots[1]
        slot.config.update(use_client_display_settings=False,resolution='2304x1440',fps='90')
        def ready(current):
            self.runtime._reply(current.pending.pop(),target={'output_name':'Virtual-1'})
        with patch.object(self.runtime,'_prepare',side_effect=ready):
            self.assertTrue(self.exchange(self.request(width=1280,height=720,fps=30))['ok'])
        self.assertEqual(slot.requested,(2304,1440,90))

    def test_invalid_app_mode_fails_before_listener_launch(self):
        with patch.object(self.runtime,'_launch') as launch:
            self.assertFalse(self.runtime.start_slot(2,dict(de='kde',use_client_display_settings=False,
                resolution='bad',fps='60')))
            launch.assert_not_called()


class ClientDisplayLifecycleTest(ClientDisplayRuntimeTest):
    def test_prepare_waits_for_readiness_and_preserves_exact_target(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        slot = self.runtime.slots[1]
        slot.owner = 'alice'
        slot.request_id = 'one'
        slot.requested = (2560, 1600, 120)
        slot.status = 'preparing'
        from PyQt6.QtCore import QTimer
        slot.deadline = QTimer()
        slot.deadline.start(20000)
        self.runtime._ready(slot, dict(name='HEADLESS-2', node_id=42, width=2560, height=1600, fps=120, offset_x=1920))
        self.assertEqual(slot.target, dict(output_name='HEADLESS-2', capture='pipewire_node', pipewire_node=42, width=2560, height=1600, fps=120.0, offset_x=1920, offset_y=0))
        result = self.exchange(self.request(width=2560, height=1600, fps=120))
        self.assertTrue(result['ok'])
        self.assertEqual(result['target']['output_name'], 'HEADLESS-2')
        self.assertTrue(self.exchange(self.request(request_id='new', width=2560, height=1600, fps=120))['ok'])
        self.assertFalse(self.exchange(self.request(operation='release'))['ok'])
        self.assertEqual(slot.target['output_name'], 'HEADLESS-2')

    def test_stopping_stream_keeps_display_for_resume_then_release_cleans(self):
        slot = self.runtime.slots[1]
        slot.owner = 'alice'
        slot.target = {'output_name': 'HEADLESS-1'}
        self.assertTrue(self.exchange(self.request(operation='stream_stopped'))['ok'])
        self.assertEqual(slot.status, 'reconnecting')
        self.assertTrue(slot.grace.isActive())
        self.assertTrue(self.exchange(self.request(operation='stream_started'))['ok'])
        self.assertFalse(slot.grace.isActive())
        self.assertTrue(self.exchange(self.request(operation='release'))['ok'])
        self.assertFalse(slot.target)

    def test_mixed_manual_slot_starts_host_only_after_display_ready(self):
        from PyQt6.QtCore import QTimer
        with patch.object(self.runtime, '_prepare'):
            self.assertTrue(self.runtime.start_manual_slot(2, {'de': 'kde', 'resolution': '1280x720', 'fps': '60'}))
        slot = self.runtime.slots[2]
        self.assertEqual(slot.status, 'preparing')
        self.assertEqual(slot.requested, (1280, 720, 60.0))
        slot.deadline = QTimer()
        self.runtime._ready(slot, {'name': 'HEADLESS-2', 'node_id': 77})
        self.assertEqual(slot.status, 'streaming')
        self.assertEqual(slot.target['width'], 1280)

    def test_retiring_holder_blocks_new_generation(self):
        self.runtime.retiring[1] = object()
        result = self.exchange(self.request())
        self.assertFalse(result['ok'])
        self.assertEqual(self.runtime.slots[1].owner, '')
        self.runtime.retiring.clear()

    def test_crash_retries_with_backoff_and_stop_cancels_retry(self):
        slot = self.runtime.slots[1]
        slot.config['client_restart_on_crash'] = True
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', return_value=1):
            self.runtime._check_engines()
            self.assertEqual(slot.status, 'recovering')
            self.assertEqual(slot.retry.interval(), 2000)
            slot.retry.stop()
            self.runtime._retry(slot)
            self.runtime._check_engines()
            self.assertEqual(slot.retry.interval(), 5000)
            timer = slot.retry
            self.runtime.stop_slot(1)
            self.assertFalse(timer.isActive())
            self.assertNotIn(1, self.runtime.slots)

    def test_managed_process_pid_must_match_socket_peer(self):
        from types import SimpleNamespace
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_process', return_value=SimpleNamespace(pid=os.getpid() + 100000)):
            result = self.exchange(self.request())
        self.assertFalse(result['ok'])
        self.assertEqual(self.runtime.slots[1].owner, '')

    def test_oversized_and_malformed_requests_are_bounded(self):
        self.assertFalse(self.exchange(self.request(padding='x' * 66000))['ok'])
        self.assertFalse(self.exchange(self.request(version=True))['ok'])

    def test_failed_restart_keeps_retrying_even_when_no_process_remains(self):
        slot = self.runtime.slots[1]
        slot.config['client_restart_on_crash'] = True
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', return_value=1):
            self.runtime._check_engines()
        slot.retry.stop()
        with patch('monitorize.desktop.client_display_runtime.start_sunshine', return_value=(False, 'failed')):
            self.runtime._retry(slot)
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', return_value=None):
            self.runtime._check_engines()
        self.assertIsNotNone(slot.retry)
        self.assertEqual(slot.retry.interval(), 5000)

    def test_abandoned_prepare_cancels_ownership(self):
        client = socket.socket(socket.AF_UNIX)
        client.connect(self.runtime.socket_path)
        with patch.object(self.runtime, '_prepare'):
            client.sendall(json.dumps(self.request()).encode() + b'\n')
            deadline = time.monotonic() + 1
            while self.runtime.slots[1].status != 'preparing' and time.monotonic() < deadline:
                self.app.processEvents()
        self.assertEqual(self.runtime.slots[1].owner, 'alice')
        client.close()
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual(self.runtime.slots[1].owner, '')
        self.assertEqual(self.runtime.slots[1].status, 'waiting')

    def test_grace_expiry_recycles_host_even_without_crash_restart(self):
        slot = self.runtime.slots[1]
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'HEADLESS-1'}
        self.assertTrue(self.exchange(self.request(operation='stream_stopped'))['ok'])
        slot.grace.stop()
        with patch('monitorize.desktop.client_display_runtime.stop_sunshine') as stopped:
            slot.grace.timeout.emit()
            stopped.assert_called_once_with(1)
        self.assertIsNotNone(slot.retry)
        slot.retry.stop()
        self.runtime._retry(slot)
        self.assertEqual(slot.status, 'waiting')
        self.assertEqual(slot.owner, '')

    def test_manual_host_crash_marks_only_its_slot_error(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        slot = ClientSlot(2, {'de': 'kde'}, status='streaming', manual=True)
        self.runtime.slots[2] = slot
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', side_effect=lambda instance: 1 if instance == 2 else None):
            self.runtime._check_engines()
        self.assertEqual(slot.status, 'error')
        self.assertIsNone(slot.retry)
        self.assertEqual(self.runtime.slots[1].status, 'waiting')

    def test_ready_rejects_backend_mode_substitution(self):
        slot = self.runtime.slots[1]
        slot.requested = (2560, 1600, 120)
        with self.assertRaises(ValueError):
            self.runtime._ready(slot, {'name': 'HEADLESS-1', 'node_id': 99, 'width': 1920, 'height': 1080, 'fps': 60})
        self.assertFalse(slot.target)

    def test_host_pending_startup_does_not_report_waiting(self):
        self.runtime.stop_slot(1)
        with patch.object(self.runtime, '_startup_status', return_value=('pending', '')):
            self.assertTrue(self.runtime.start_slot(1, {'de': 'kde'}))
            self.assertEqual(self.runtime.slots[1].status, 'starting')
        self.runtime._check_engines()
        self.assertEqual(self.runtime.slots[1].status, 'waiting')

    def test_start_does_not_overwrite_active_unrelated_instance(self):
        with patch('monitorize.desktop.client_display_runtime.is_sunshine_running', return_value=True), patch('monitorize.desktop.client_display_runtime.is_sunshine_settings_instance', return_value=False):
            self.assertFalse(self.runtime.start_slot(2, {'de': 'kde'}))
        self.assertNotIn(2, self.runtime.slots)

    def test_startup_timeout_stops_host_and_reports_error(self):
        slot = self.runtime.slots[1]
        slot.status, slot.startup_deadline = 'starting', 0
        with patch.object(self.runtime, '_startup_status', return_value=('pending', '')), patch('monitorize.desktop.client_display_runtime.stop_sunshine') as stop:
            self.runtime._check_engines()
            stop.assert_called_once_with(1)
        self.assertEqual(slot.status, 'error')
        self.assertIn('timed out', slot.error)

    def test_deferred_readiness_requires_current_launch_protocol_and_listener(self):
        from monitorize.desktop.client_display_runtime import ClientDisplayRuntime
        slot = self.runtime.slots[1]
        logfile = os.path.join(self.temp.name, 'sunshine.log')
        old = b'MONITORIZE_CONTROL_PROTOCOL=1\nConfiguration UI available at https://old\n'
        with open(logfile, 'wb') as stream:
            stream.write(old)
        slot.log_offset = len(old)
        slot.log_identity = ()
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_config_dir', return_value=self.temp.name):
            self.assertEqual(ClientDisplayRuntime._startup_status(self.runtime, slot)[0], 'pending')
            with open(logfile, 'ab') as stream:
                stream.write(b'MONITORIZE_CONTROL_PROTOCOL=1\n')
            self.assertEqual(ClientDisplayRuntime._startup_status(self.runtime, slot)[0], 'pending')
            with open(logfile, 'ab') as stream:
                stream.write(b'Configuration UI available at https://new\n')
            self.assertEqual(ClientDisplayRuntime._startup_status(self.runtime, slot)[0], 'ready')

    def test_port_collision_reports_endpoint_and_stops_automatic_retry(self):
        from monitorize.desktop.client_display_runtime import ClientDisplayRuntime
        slot = self.runtime.slots[1]
        slot.log_offset, slot.log_identity = 0, ()
        with open(os.path.join(self.temp.name, 'sunshine.log'), 'w') as stream:
            stream.write("Fatal: Couldn't bind RTSP server to port [50110], Address already in use\n")
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_config_dir', return_value=self.temp.name):
            state, error = ClientDisplayRuntime._startup_status(self.runtime, slot)
        self.assertEqual(state, 'failed')
        self.assertIn('port 50110', error)
        self.assertIn('already in use', error)
        self.assertTrue(slot.permanent_failure)

    def test_listener_readiness_handles_sunshine_log_rotation(self):
        from monitorize.desktop.client_display_runtime import ClientDisplayRuntime
        slot = self.runtime.slots[1]
        logfile = os.path.join(self.temp.name, 'sunshine.log')
        for old_size in (10, 10000):
            with self.subTest(old_size=old_size):
                with open(logfile, 'wb') as stream:
                    stream.write(b'x' * old_size)
                info = os.stat(logfile)
                slot.log_offset = old_size
                slot.log_identity = info.st_dev, info.st_ino
                os.replace(logfile, logfile + '.old')
                with open(logfile, 'wb') as stream:
                    stream.write(b'MONITORIZE_CONTROL_PROTOCOL=1\nConfiguration UI available at https://new\n')
                with patch('monitorize.desktop.client_display_runtime.get_sunshine_config_dir', return_value=self.temp.name):
                    self.assertEqual(ClientDisplayRuntime._startup_status(self.runtime, slot)[0], 'ready')

    def test_native_kde_accepts_verified_cvt_mode_for_virtual_only(self):
        from PyQt6.QtCore import QTimer
        slot = self.runtime.slots[1]
        slot.config['display_behavior'] = 'virtual_only'
        slot.requested = (2340, 1080, 120)
        slot.deadline = QTimer()
        self.runtime._ready(slot, dict(name='Virtual-Monitorize-1', node_id=82,
                                      width=2336, height=1080, fps=119.439))
        self.assertEqual(slot.target['width'], 2336)
        self.assertAlmostEqual(slot.target['fps'], 119.439)

    def test_prepare_error_is_returned_without_stopping_https_listener(self):
        slot = self.runtime.slots[1]
        slot.status = 'preparing'
        peer = object()
        slot.pending = [peer]
        with patch.object(self.runtime, '_reply') as reply, patch(
            'monitorize.desktop.client_display_runtime.stop_sunshine'
        ) as stopped:
            self.runtime._holder_failed(slot, None, 'Desktop socket unavailable')
        reply.assert_called_once_with(peer, False, error='Desktop socket unavailable')
        stopped.assert_not_called()
        self.assertEqual(slot.error, 'Desktop socket unavailable')
        self.assertIsNone(slot.retry)

    def test_kde_vkms_listener_rejects_old_helper_before_launch(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsProtocolError
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient') as factory, patch(
            'monitorize.desktop.client_display_runtime.start_sunshine'
        ) as launch:
            factory.return_value.require_managed_backend.side_effect = VkmsProtocolError('Update KDE helper')
            self.assertFalse(self.runtime.start_slot(2, {'de': 'kde', 'virtual_display_creator': 'vkms'}))
        factory.return_value.require_managed_backend.assert_called_once_with('kde')
        launch.assert_not_called()
        self.assertEqual(self.runtime.error, 'Update KDE helper')

    def test_unexpected_holder_loss_recycles_only_owning_listener(self):
        slot = self.runtime.slots[1]
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'HEADLESS-1'}
        with patch('monitorize.desktop.client_display_runtime.stop_sunshine') as stopped:
            self.runtime._holder_failed(slot, None, 'holder exited')
            stopped.assert_called_once_with(1)
        self.assertEqual(slot.status, 'recovering')
        self.assertIsNotNone(slot.retry)
        self.assertEqual(slot.error, 'holder exited')

    def test_manual_holder_loss_stops_host_without_background_retry(self):
        from monitorize.desktop.client_display_runtime import ClientSlot
        slot = ClientSlot(2, {'de': 'kde'}, status='streaming', manual=True)
        self.runtime.slots[2] = slot
        with patch('monitorize.desktop.client_display_runtime.stop_sunshine') as stopped:
            self.runtime._holder_failed(slot, None, 'holder exited')
            stopped.assert_called_once_with(2)
        self.assertEqual(slot.status, 'error')
        self.assertIsNone(slot.retry)

    def test_repeated_stream_stopped_does_not_leave_old_grace_timer(self):
        slot = self.runtime.slots[1]
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'HEADLESS-1'}
        self.exchange(self.request(operation='stream_stopped'))
        old_timer = slot.grace
        self.exchange(self.request(operation='stream_stopped'))
        self.assertFalse(old_timer.isActive())
        self.exchange(self.request(operation='stream_started'))
        self.assertFalse(slot.grace.isActive())

    def test_disconnect_grace_uses_general_setting_for_manual_app_launch(self):
        slot = self.runtime.slots[1]
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'HEADLESS-1'}
        with patch('monitorize.config.settings.load_display_idle_timeout', return_value=47):
            self.assertTrue(self.exchange(self.request(operation='stream_stopped'))['ok'])
        self.assertEqual(slot.grace.interval(), 47000)
        self.exchange(self.request(operation='stream_started'))
        self.assertFalse(slot.grace.isActive())

    def test_untracked_engine_cannot_use_shared_runtime_token(self):
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_process', return_value=None), patch.object(self.runtime, '_prepare', side_effect=AssertionError('Untracked peer must not allocate displays')):
            result = self.exchange(self.request())
        self.assertFalse(result['ok'])
        self.assertEqual(self.runtime.slots[1].owner, '')

    def test_short_stream_does_not_reset_crash_backoff_but_stable_host_does(self):
        slot = self.runtime.slots[1]
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'HEADLESS-1'}
        slot.failures = 3
        self.assertTrue(self.exchange(self.request(operation='stream_started'))['ok'])
        self.assertEqual(slot.failures, 3)
        slot.healthy_since = time.monotonic() - 61
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', return_value=None):
            self.runtime._check_engines()
        self.assertEqual(slot.failures, 0)

    def test_downgraded_engine_is_never_launched_on_retry(self):
        slot = self.runtime.slots[1]
        slot.config['client_restart_on_crash'] = True
        with patch('monitorize.desktop.client_display_runtime.get_sunshine_exit_code', return_value=1):
            self.runtime._check_engines()
        slot.retry.stop()
        with patch('monitorize.desktop.client_display_runtime.supports_control_protocol', return_value=False), patch('monitorize.desktop.client_display_runtime.start_sunshine', return_value=(True, '')) as start:
            self.runtime._retry(slot)
            start.assert_not_called()
        self.assertEqual(slot.status, 'error')
        self.assertIn('protocol', slot.error)
        self.runtime._check_engines()
        self.assertIsNone(slot.retry)

    def test_verified_cleanup_allows_retirement_after_startup_error(self):
        from unittest.mock import Mock
        process = Mock()
        process.readAllStandardOutput.return_value = b''
        self.runtime.retiring[1] = process
        self.runtime._clean_holders.add(process)
        self.runtime._retired(1, process, 1)
        self.assertNotIn(1, self.runtime.cleanup_blocked)

    def test_stale_retirement_cannot_block_current_generation(self):
        from unittest.mock import Mock
        current, stale = Mock(), Mock()
        self.runtime.retiring[1] = current
        self.runtime._retired(1, stale, 1)
        self.assertIs(self.runtime.retiring[1], current)
        self.assertNotIn(1, self.runtime.cleanup_blocked)

    def test_failed_holder_cleanup_blocks_new_generation_without_blocking_quit(self):
        from unittest.mock import Mock
        process = Mock()
        process.readAllStandardOutput.return_value = b''
        self.runtime.retiring[1] = process
        slot = self.runtime.slots[1]
        slot.status = 'stopping'
        self.runtime._retired(1, process, 1)
        self.assertNotIn(1, self.runtime.retiring)
        self.assertIn(1, self.runtime.cleanup_blocked)
        self.assertEqual(slot.status, 'error')
        self.assertFalse(self.exchange(self.request())['ok'])
        self.runtime.stop_slot(1)
        self.assertFalse(self.runtime.start_slot(1, {'de': 'kde'}))

    def test_native_style_holder_receives_quit_before_retirement(self):
        from PyQt6.QtCore import QProcess
        import sys
        process = QProcess(self.runtime)
        process.start(sys.executable, ['-c', "import signal,sys; signal.signal(signal.SIGTERM, lambda *args: None); print('ready', flush=True); sys.stdin.readline()"])
        self.assertTrue(process.waitForStarted(1000))
        self.assertTrue(process.waitForReadyRead(1000))
        slot = self.runtime.slots[1]
        slot.process = process
        try:
            self.runtime._cleanup(slot)
            deadline = time.monotonic() + .3
            while self.runtime.retiring and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.001)
            self.assertFalse(self.runtime.retiring)
        finally:
            if self.runtime.retiring:
                process.kill()
                process.waitForFinished(1000)

    def test_listener_accepts_explicit_codecs_with_capable_engine(self):
        for codec in ('Auto','H.264','HEVC','AV1'):
            with patch('monitorize.desktop.client_display_runtime.start_sunshine',return_value=(True,'')) as start:
                self.assertTrue(self.runtime.start_slot(2,{'de':'kde','codec':codec}))
                start.assert_called_once()
            self.runtime.stop_slot(2)

    def test_old_listener_engine_is_rejected_before_launch(self):
        with patch('monitorize.desktop.client_display_runtime.supports_control_protocol',
                   side_effect=lambda instance,**kwargs:kwargs.get('marker')!=b'MONITORIZE_CODEC_NEGOTIATION=1'), \
             patch('monitorize.desktop.client_display_runtime.start_sunshine') as start:
            self.assertFalse(self.runtime.start_slot(2,{'de':'kde','codec':'AV1'}))
            start.assert_not_called()
        self.assertIn('codec negotiation',self.runtime.error)

    def test_x11_target_resolves_drm_name_to_exact_xrandr_alias(self):
        from PyQt6.QtCore import QTimer
        slot = self.runtime.slots[1]
        slot.config['capture'] = 'x11'
        slot.requested = (1920, 1080, 60)
        slot.deadline = QTimer()
        with patch('monitorize.desktop.streaming_controller._x11_capture_output', return_value='Virtual-1-1'):
            self.runtime._ready(slot, {'name': 'Virtual-1', 'width': 1920, 'height': 1080, 'fps': 60})
        self.assertEqual(slot.target['output_name'], 'Virtual-1-1')

    def test_x11_missing_output_cannot_publish_capture_target(self):
        from PyQt6.QtCore import QTimer
        slot = self.runtime.slots[1]
        slot.config['capture'] = 'x11'
        slot.requested = (1920, 1080, 60)
        slot.deadline = QTimer()
        with patch('monitorize.desktop.streaming_controller._x11_capture_output', side_effect=ValueError('Output missing')):
            with self.assertRaises(ValueError):
                self.runtime._ready(slot, {'name': 'Virtual-1'})
        self.assertFalse(slot.target)

    def test_explicit_client_restart_keeps_private_listener_configuration(self):
        with patch('monitorize.desktop.client_display_runtime.stop_sunshine') as stop:
            self.assertTrue(self.runtime.restart_slot(1))
            stop.assert_called_once_with(1)
        self.assertEqual(self.runtime.slots[1].status, 'recovering')
        self.assertIsNotNone(self.runtime.slots[1].retry)
        self.assertFalse(self.runtime.restart_slot(2))

    def test_mixed_manual_slot_rejects_old_engine_before_creating_output(self):
        with patch('monitorize.desktop.client_display_runtime.supports_control_protocol', return_value=False), patch.object(self.runtime, '_prepare') as prepare:
            self.assertFalse(self.runtime.start_manual_slot(2, {'de': 'kde'}))
        prepare.assert_not_called()

    def test_unattended_vkms_listener_refuses_missing_enrollment_before_launch(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsCommandError
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient.require_unattended', side_effect=VkmsCommandError('Run setup first')), patch('monitorize.desktop.client_display_runtime.start_sunshine', return_value=(True, '')) as start:
            self.assertFalse(self.runtime.start_slot(2, {'de': 'kde', 'virtual_display_creator': 'vkms'}))
            start.assert_not_called()
        self.assertNotIn(2, self.runtime.slots)

    def test_revoked_authorization_prevents_watchdog_listener_restart(self):
        from monitorize.platform.monitorize_vkms_dbus import VkmsCommandError
        slot = self.runtime.slots[1]
        slot.config['virtual_display_creator'] = 'vkms'
        with patch('monitorize.platform.monitorize_vkms_dbus.MonitorizeVkmsClient.require_unattended', side_effect=VkmsCommandError('Permission revoked')), patch('monitorize.desktop.client_display_runtime.start_sunshine', return_value=(True, '')) as start:
            self.assertFalse(self.runtime._launch(slot))
            start.assert_not_called()
        self.assertTrue(slot.permanent_failure)
        self.assertEqual(slot.status, 'error')

    def test_guarded_layout_waits_for_encoded_frame_then_restores_on_disconnect(self):
        from unittest.mock import Mock
        slot = self.runtime.slots[1]
        slot.config['display_behavior'] = 'virtual_only'
        slot.owner, slot.request_id = 'alice', 'one'
        slot.target = {'output_name': 'Virtual-1'}
        self.runtime.guardian = SimpleNamespace(command=Mock())
        self.assertTrue(self.exchange(self.request(operation='stream_started'))['ok'])
        self.runtime.guardian.command.assert_not_called()
        self.assertTrue(self.exchange(self.request(operation='stream_ready'))['ok'])
        self.runtime.guardian.command.assert_called_once_with('stream', slot=1, active=True)
        self.assertTrue(self.exchange(self.request(operation='stream_stopped'))['ok'])
        self.runtime.guardian.command.assert_called_with('stream', slot=1, active=False)

    def test_fixed_display_accepts_lifecycle_but_never_client_mode_changes(self):
        from unittest.mock import Mock
        slot = self.runtime.slots[1]
        slot.manual = True
        slot.target = {'output_name': 'Virtual-1'}
        self.runtime.guardian = SimpleNamespace(command=Mock())
        self.assertFalse(self.exchange(self.request())['ok'])
        self.assertTrue(self.exchange(self.request(operation='stream_started'))['ok'])
        self.runtime.guardian.command.assert_not_called()
        self.assertTrue(self.exchange(self.request(operation='stream_ready'))['ok'])
        self.assertFalse(self.exchange(self.request(operation='stream_ready', client_id='bob'))['ok'])
        self.assertTrue(self.exchange(self.request(operation='stream_stopped'))['ok'])
        self.assertFalse(slot.owner)
        self.assertTrue(slot.target)


if __name__ == '__main__':
    unittest.main()
