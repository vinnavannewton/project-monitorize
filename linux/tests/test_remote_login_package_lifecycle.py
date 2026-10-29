"""Exercise package transactions without services, displays or package managers."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitorize.remote_login.package_lifecycle import (
    BOOT, RECEIPT, SUPERVISOR, InstalledBackend, PackageLifecycle, unit_names,
)

ROOT = Path(__file__).resolve().parents[2]


class Backend:
    boot_id = 'test-boot'

    def __init__(self, *, enabled=True, protected=True, active=True, connected=False):
        self.settings = dict(uid=1000, enabled=enabled) if enabled is not None else None
        self.protected = protected
        self.running = active
        self.connected = connected
        self.events = []
        self.prior_masks = set()
        self.error = None

    def state(self):return self.settings, self.protected
    def connection_in_use(self, uid):return self.connected
    def active(self, name):return self.running
    def masked(self, name):return name in self.prior_masks
    def pause(self, uid, masks, *, allow_disconnect=False):
        self.events.append(('pause', list(masks)))
        self.connected = False
    def release(self, uid):
        self.events.append(('release', uid))
        if self.error:raise RuntimeError(self.error)
        self.protected = False
    def disable(self, uid, masks):
        self.events.append(('disable', uid))
        self.settings['enabled'] = False
    def restore(self, uid, *, restart):
        self.events.append(('restore', uid))
        if self.error:raise RuntimeError(self.error)
        return restart and self.settings['enabled']
    def unmask(self, masks):self.events.append(('unmask', list(masks)))
    def start(self):self.events.append(('start',))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backend = Backend()
        self.lifecycle = PackageLifecycle(self.backend, root=self.root, owner_uid=os.getuid())

    def test_unused_and_disabled_users_need_no_systemd_or_host(self):
        for enabled in (None, False):
            self.backend.settings = dict(uid=1000, enabled=enabled) if enabled is not None else None
            self.backend.protected = False
            self.lifecycle.prepare('upgrade')
            self.lifecycle.resume()
            self.assertEqual(self.backend.events, [])
            self.assertFalse((self.root/RECEIPT).exists())

    def test_idle_upgrade_releases_then_restores_without_changing_settings(self):
        self.lifecycle.prepare('upgrade')
        receipt = self.root/RECEIPT
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(receipt.read_text())['stage'], 'prepared')
        self.assertEqual([e[0] for e in self.backend.events], ['pause', 'release'])
        self.assertTrue(self.backend.settings['enabled'])
        self.lifecycle.resume()
        self.assertEqual([e[0] for e in self.backend.events], ['pause', 'release', 'restore', 'unmask', 'start'])
        self.assertFalse(receipt.exists())

    def test_setup_can_pause_leftover_units_even_when_disabled_without_a_lease(self):
        self.backend.settings['enabled']=False;self.backend.protected=False
        self.lifecycle.prepare('upgrade',include_idle_services=True)
        self.assertEqual([e[0] for e in self.backend.events],['pause','release'])
        self.assertTrue((self.root/RECEIPT).exists())
        self.lifecycle.resume()
        self.assertNotIn('start',[e[0] for e in self.backend.events])

    def test_expired_maintenance_does_not_remove_current_administrator_masks(self):
        self.lifecycle.prepare('upgrade')
        self.backend.boot_id='next-boot'
        self.backend.prior_masks.add(SUPERVISOR)
        self.backend.events=[]
        self.lifecycle.disable_enrollment(1000)
        pause=self.backend.events[0]
        self.assertNotIn(SUPERVISOR,pause[1])
        self.assertNotIn(SUPERVISOR,self.backend.events[-1][1])
        self.assertNotIn('start',[event[0] for event in self.backend.events])

    def test_foreign_maintenance_cannot_be_consumed_by_disable(self):
        self.lifecycle.prepare('upgrade')
        value=self.lifecycle.load();value['uid']=2000;value['masks']=unit_names(2000)
        self.lifecycle.save(value)
        self.backend.events=[]
        before=(self.root/RECEIPT).read_bytes()
        with self.assertRaises(PermissionError):self.lifecycle.disable_enrollment(1000)
        self.assertEqual((self.root/RECEIPT).read_bytes(),before)
        self.assertEqual(self.backend.events,[])

    def test_explicit_disable_never_restores_an_interrupted_upgrade(self):
        self.lifecycle.prepare('upgrade')
        self.lifecycle.disable_enrollment(1000)
        self.assertFalse(self.backend.settings['enabled'])
        self.assertFalse((self.root/RECEIPT).exists())
        self.assertNotIn('restore',[event[0] for event in self.backend.events])
        self.assertNotIn('start',[event[0] for event in self.backend.events])

    def test_failed_release_keeps_saved_disable_and_retry_receipt(self):
        self.backend.error='connector busy'
        with self.assertRaisesRegex(RuntimeError,'connector busy'):
            self.lifecycle.disable_enrollment(1000)
        self.assertFalse(self.backend.settings['enabled'])
        self.assertTrue((self.root/RECEIPT).exists())
        self.backend.error=None
        self.lifecycle.disable_enrollment(1000)
        self.assertFalse((self.root/RECEIPT).exists())

    def test_disabled_policy_still_stops_a_leftover_service(self):
        self.backend.settings['enabled']=False;self.backend.protected=False
        self.lifecycle.disable_enrollment(1000)
        self.assertEqual([e[0] for e in self.backend.events],['pause','disable','release','unmask'])

    def test_explicit_disable_rejects_foreign_enrollment(self):
        with self.assertRaises(PermissionError):self.lifecycle.disable_enrollment(1001)
        self.assertEqual(self.backend.events,[])

    def test_active_connection_requires_explicit_disconnect_before_any_mutation(self):
        self.backend.connected = True
        with self.assertRaisesRegex(RuntimeError, '--allow-disconnect'):
            self.lifecycle.prepare('upgrade')
        self.assertEqual(self.backend.events, [])
        self.assertFalse((self.root/RECEIPT).exists())
        self.lifecycle.prepare('upgrade', allow_disconnect=True)
        count = len(self.backend.events)
        # The package's subsequent pre-hook must accept the explicit preparation.
        self.lifecycle.prepare('upgrade')
        self.assertEqual([event[0] for event in self.backend.events[count:]], ['pause', 'release'])

    def test_removal_disables_after_release_and_does_not_restart(self):
        self.lifecycle.prepare('remove')
        self.assertEqual([e[0] for e in self.backend.events], ['pause', 'release', 'disable', 'unmask'])
        self.assertFalse(self.backend.settings['enabled'])
        self.assertFalse((self.root/RECEIPT).exists())
        self.lifecycle.resume()
        self.assertEqual(len(self.backend.events), 4)

    def test_cleanup_failure_retains_receipt_and_specific_recovery(self):
        self.backend.error = 'Exact connector changed'
        with self.assertRaisesRegex(RuntimeError, 'Exact connector changed.*--resume'):
            self.lifecycle.prepare('remove')
        self.assertTrue(self.backend.settings['enabled'])
        self.assertEqual(json.loads((self.root/RECEIPT).read_text())['stage'], 'pausing')
        self.backend.error = None
        self.lifecycle.prepare('remove')
        self.assertFalse((self.root/RECEIPT).exists())

    def test_interrupted_prepare_can_be_resumed(self):
        self.backend.error = 'output busy'
        with self.assertRaises(RuntimeError):self.lifecycle.prepare('upgrade')
        self.backend.error = None
        self.lifecycle.resume()
        self.assertEqual(self.backend.events[-1], ('start',))
        self.assertFalse((self.root/RECEIPT).exists())

    def test_resume_failure_is_retryable(self):
        self.lifecycle.prepare('upgrade')
        self.backend.error = 'host unavailable'
        with self.assertRaisesRegex(RuntimeError, 'host unavailable.*--resume'):
            self.lifecycle.resume()
        self.assertTrue((self.root/RECEIPT).exists())
        self.backend.error = None
        self.lifecycle.resume()
        self.assertFalse((self.root/RECEIPT).exists())

    def test_prior_masks_and_inactive_supervisor_are_preserved(self):
        self.backend.running = False
        self.backend.prior_masks = {BOOT}
        self.lifecycle.prepare('upgrade')
        self.lifecycle.resume()
        self.assertNotIn(BOOT, self.backend.events[0][1])
        self.assertNotIn(('start',), self.backend.events)

    def test_old_boot_or_changed_operation_is_not_replayed(self):
        self.lifecycle.prepare('upgrade')
        with self.assertRaisesRegex(RuntimeError, 'different package operation'):
            self.lifecycle.prepare('remove')
        self.backend.boot_id = 'another-boot'
        with self.assertRaisesRegex(RuntimeError, 'previous boot'):
            self.lifecycle.prepare('upgrade')
        count = len(self.backend.events)
        self.lifecycle.resume()
        self.assertFalse((self.root/RECEIPT).exists())
        self.assertEqual(len(self.backend.events), count)

    def test_orphan_protected_display_blocks_without_guessing_owner(self):
        self.backend.settings = None
        with self.assertRaisesRegex(RuntimeError, 'no enrollment configuration'):
            self.lifecycle.prepare('upgrade')
        self.assertEqual(self.backend.events, [])

    def test_unsafe_receipt_and_unit_names_are_rejected(self):
        self.lifecycle.prepare('upgrade')
        path = self.root/RECEIPT
        values = json.loads(path.read_text())
        values['masks'] = ['display-manager.service']
        path.write_text(json.dumps(values))
        with self.assertRaisesRegex(ValueError, 'unit list'):self.lifecycle.resume()

    def test_pause_checks_capture_process_exit_before_releasing(self):
        backend = InstalledBackend()
        properties = dict(ActiveState='active', LoadState='masked', ControlGroup='')
        with patch.object(backend, 'run'), patch.object(backend, 'properties', return_value=properties), patch.object(backend, 'wait_for_pause'):
            with self.assertRaisesRegex(RuntimeError, 'has not stopped'):
                backend.pause(1000, unit_names(1000))

    def test_retry_after_partial_resume_rechecks_connections(self):
        self.lifecycle.prepare('upgrade')
        self.backend.connected = True
        with self.assertRaisesRegex(RuntimeError, 'became active'):
            self.lifecycle.prepare('upgrade')
        self.lifecycle.prepare('upgrade', allow_disconnect=True)

    def test_health_distinguishes_listening_from_streaming(self):
        backend = InstalledBackend()
        from types import SimpleNamespace
        settings = SimpleNamespace(directory=self.root)
        with patch('monitorize.remote_login.settings.read_settings', return_value=settings), \
             patch.object(backend, 'properties', return_value=dict(ActiveState='active', MainPID='12')), \
             patch('monitorize.remote_login.health.read_health') as health:
            for state, expected in (('desktop_listening', False), ('disconnect_grace', False), ('streaming', True)):
                health.return_value = {'state': state}
                self.assertEqual(backend.connection_in_use(1000), expected)

    def test_disabled_enrollment_is_not_started_on_recovery(self):
        self.lifecycle.prepare('upgrade')
        self.backend.settings['enabled'] = False
        self.lifecycle.resume()
        self.assertNotIn(('start',), self.backend.events)

    def test_missing_health_is_conservatively_treated_as_connected(self):
        backend = InstalledBackend()
        from types import SimpleNamespace
        settings = SimpleNamespace(directory=self.root)
        with patch('monitorize.remote_login.settings.read_settings', return_value=settings), \
             patch.object(backend, 'properties', return_value=dict(ActiveState='active', MainPID='12')):
            self.assertTrue(backend.connection_in_use(1000))

    def test_admission_gate_rejects_new_launch_but_retains_existing_stream(self):
        from monitorize.remote_login import idle
        display = idle.IdleDisplay(lambda: {}, lambda: None)
        with patch.object(idle, 'PACKAGE_RECEIPT', self.root/RECEIPT):
            message = dict(operation='prepare', client_id='client', request_id='request')
            display.request(message)
            self.assertTrue(display.health(listening=True)['package_busy'])
            self.lifecycle.prepare('upgrade')
            health = display.health(listening=True)
            self.assertTrue(health['package_paused'])
            self.assertTrue(health['package_busy'])
            with self.assertRaisesRegex(RuntimeError, 'package transaction'):
                display.request(dict(message, request_id='another'))
            # Existing sessions can finish normally while the package waits.
            display.request(dict(message, operation='release'))
            self.assertFalse(display.health(listening=True)['package_busy'])

    def test_cancelling_handshake_preserves_the_running_listener(self):
        from types import SimpleNamespace
        backend = InstalledBackend()
        settings = SimpleNamespace(uid=1000, enabled=True)
        with patch('monitorize.remote_login.settings.read_settings', return_value=settings), \
             patch.object(backend, 'active', return_value=True), patch.object(backend, 'host') as host:
            self.assertTrue(backend.restore(1000, restart=True))
            host.assert_not_called()

    def test_wait_for_pause_requires_fresh_engine_ack_before_stopping(self):
        backend = InstalledBackend()
        from types import SimpleNamespace
        settings = SimpleNamespace(directory=self.root)
        with patch('monitorize.remote_login.settings.read_settings', return_value=settings), \
             patch.object(backend, 'properties', return_value=dict(ActiveState='active', MainPID='12')), \
             patch('monitorize.remote_login.health.read_health') as health:
            health.return_value = dict(package_paused=True, package_busy=False)
            backend.wait_for_pause(1000)
            health.return_value = dict(package_paused=True, package_busy=True)
            with self.assertRaisesRegex(RuntimeError, 'client connected'):
                backend.wait_for_pause(1000)
            health.return_value = {}
            with patch('monitorize.remote_login.package_lifecycle.time.monotonic', side_effect=[0, 5]):
                with self.assertRaisesRegex(RuntimeError, 'did not acknowledge'):
                    backend.wait_for_pause(1000)

    def test_removal_publishes_disabled_policy_before_unmasking_and_disabling(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from monitorize.remote_login.root_files import RootFiles
        backend = InstalledBackend()
        files = RootFiles(self.root, os.getuid())
        files.write('etc/monitorize/remote-login.json', json.dumps(dict(uid=1000, enabled=True, encoder='software')), 0o600)
        events = []
        commands = Mock()
        commands.enabled.return_value = True
        commands.disable.side_effect = lambda name: events.append(('disable', name))
        def unmask(masks):
            self.assertFalse(json.loads((self.root/'etc/monitorize/remote-login.json').read_text())['enabled'])
            events.append(('unmask', masks))
        with patch('monitorize.remote_login.settings.read_settings', return_value=SimpleNamespace(uid=1000)), \
             patch('monitorize.remote_login.package_lifecycle.RootFiles', return_value=files), \
             patch('monitorize.remote_login.enrollment.Commands', return_value=commands), \
             patch.object(backend, 'unmask', side_effect=unmask):
            backend.disable(1000, [BOOT, SUPERVISOR])
        self.assertEqual([event[0] for event in events], ['unmask', 'disable', 'disable'])
        self.assertEqual(json.loads((self.root/'etc/monitorize/remote-login.json').read_text())['encoder'], 'software')

    def test_recovery_reserves_for_current_desktop_or_greeter_before_starting(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        backend = InstalledBackend()
        host, runtime, helper = Mock(), Mock(), SimpleNamespace(STATE_DIRECTORY=self.root)
        host.boot_output_status.return_value = None
        runtime.desktop_context.return_value = 'current-session'
        with patch('monitorize.remote_login.settings.read_settings', return_value=SimpleNamespace(uid=1000, enabled=True)), \
             patch.object(backend, 'active', return_value=False), \
             patch.object(backend, 'host', return_value=(host, runtime, helper)):
            runtime.local_sessions.return_value = [dict(seat='seat0', **{'class': 'user'})]
            self.assertTrue(backend.restore(1000, restart=True))
            self.assertEqual(host.reserve_desktop_installed.call_args.args, (1000, 'current-session'))
            runtime.local_sessions.return_value = [dict(seat='seat0', **{'class': 'greeter'})]
            self.assertTrue(backend.restore(1000, restart=True))
            host.main.assert_called_once_with()

    def test_all_native_recipes_prepare_and_resume_and_remove_explicitly(self):
        for distro in ('rpm', 'tumbleweed'):
            spec = (ROOT/f'packaging/{distro}/monitorize.spec').read_text()
            self.assertIn('package-guard.py --prepare upgrade', spec)
            self.assertIn('package-guard.py --prepare remove', spec)
            self.assertIn('package-guard.py --resume', spec.split('%posttrans', 1)[1])
        for target in ('ubuntu-24.04', 'ubuntu-26.04', 'debian-trixie'):
            base = ROOT/'packaging/deb'/target/'debian'
            self.assertIn('upgrade|deconfigure) operation=upgrade', (base/'prerm').read_text())
            self.assertIn('remove) operation=remove', (base/'prerm').read_text())
            self.assertIn('--resume', (base/'postinst').read_text())
        arch = ROOT/'packaging/arch'
        self.assertNotIn('Operation = Remove', (arch/'monitorize-remote-login-guard.hook').read_text())
        self.assertIn('--prepare remove', (arch/'monitorize-remote-login-remove.hook').read_text())
        self.assertIn('--resume', (arch/'monitorize.install').read_text())
