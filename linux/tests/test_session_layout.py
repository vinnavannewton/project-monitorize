import copy
import json
from pathlib import Path
import tempfile
import unittest

from monitorize.platform.session_layout import KdeLayout, LayoutTransaction


def output(name, priority=1, enabled=True):
    return dict(name=name, connected=True, enabled=enabled, priority=priority)


class SimulatedKde(KdeLayout):
    def __init__(self):
        self.state = [output('eDP-1')]
        self.commands = []
        self.fail_restore = False
        self.fail_disable = False

    def outputs(self):
        return copy.deepcopy(self.state)

    def apply(self, settings):
        self.commands.append(list(settings))
        for setting in settings:
            _, name, action = setting.split('.', 2)
            current = next(o for o in self.state if o['name'] == name)
            if action == 'enable':
                if self.fail_restore:
                    raise RuntimeError('restore failed')
                current['enabled'] = True
            elif action == 'disable':
                if self.fail_disable:
                    raise RuntimeError('disable failed')
                current['enabled'] = False
            elif action.startswith('priority.'):
                current['priority'] = int(action.split('.')[1])


class LayoutTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = SimulatedKde()
        self.path = Path(self.temp.name) / 'layout.json'
        self.layout = LayoutTransaction(self.backend, self.path, 'virtual_only')
        self.layout.reserve(1)
        self.backend.state.append(output('Virtual-1', 2))
        self.layout.ready(1, 'Virtual-1')

    def test_no_physical_change_before_stream_starts(self):
        self.assertFalse(self.backend.commands)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_virtual_only_restores_physical_before_release(self):
        self.layout.stream(1, True)
        self.assertFalse(self.backend.state[0]['enabled'])
        self.layout.release(1)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertEqual(self.backend.state[0]['priority'], 1)
        self.assertFalse(self.path.exists())

    def test_kwin_screencast_output_without_enabled_field_remains_active(self):
        del self.backend.state[1]['enabled']
        self.layout.stream(1, True)
        self.assertFalse(self.backend.state[0]['enabled'])
        self.assertEqual(self.backend.state[1]['priority'], 1)
        self.layout.release(1)
        self.assertTrue(self.backend.state[0]['enabled'])

    def test_missing_enabled_does_not_override_disconnection_or_zero_priority(self):
        for state in (
            {'connected': False, 'priority': 2},
            {'connected': True, 'priority': 0},
            {'connected': True, 'priority': 2, 'enabled': False},
            {'connected': True},
        ):
            with self.subTest(state=state):
                self.assertFalse(KdeLayout.active(state))

    def test_primary_keeps_physical_enabled_and_restores_priority(self):
        self.layout.mode = 'primary'
        self.layout.stream(1, True)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertEqual(self.backend.state[1]['priority'], 1)
        self.layout.stream(1, False)
        self.assertEqual(self.backend.state[0]['priority'], 1)

    def test_disconnect_restores_during_grace_and_resume_reapplies(self):
        self.layout.stream(1, True)
        self.layout.stream(1, False)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertEqual(self.layout.targets[1], 'Virtual-1')
        self.layout.stream(1, True)
        self.assertFalse(self.backend.state[0]['enabled'])

    def test_two_clients_do_not_overwrite_original_snapshot(self):
        self.layout.stream(1, True)
        self.layout.reserve(2)
        self.backend.state.append(output('Virtual-2', 3))
        self.layout.ready(2, 'Virtual-2')
        self.layout.stream(2, True)
        self.assertEqual([o['name'] for o in self.layout.snapshot], ['eDP-1'])
        self.layout.release(1)
        self.assertFalse(self.backend.state[0]['enabled'])
        self.assertEqual(self.backend.state[2]['priority'], 1)
        self.layout.release(2)
        self.assertTrue(self.backend.state[0]['enabled'])

    def test_slot_two_first_and_reconnecting_slot_one_is_not_primary(self):
        self.layout.reserve(2)
        self.backend.state.append(output('Virtual-2', 3))
        self.layout.ready(2, 'Virtual-2')
        self.layout.stream(2, True)
        self.assertEqual(self.backend.state[2]['priority'], 1)
        self.layout.stream(1, True)
        self.assertEqual(self.backend.state[1]['priority'], 1)
        self.layout.stream(1, False)
        self.assertEqual(self.backend.state[2]['priority'], 1)
        self.assertFalse(self.backend.state[0]['enabled'])

    def test_failed_restoration_does_not_release_owned_output(self):
        self.layout.stream(1, True)
        self.backend.fail_restore = True
        with self.assertRaises(RuntimeError):
            self.layout.release(1)
        self.assertEqual(self.layout.targets[1], 'Virtual-1')
        self.assertTrue(self.path.exists())
        self.assertIn(1, self.layout.streaming)

    def test_partial_apply_failure_rolls_back_physical(self):
        self.backend.fail_disable = True
        with self.assertRaises(RuntimeError):
            self.layout.stream(1, True)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertEqual(self.backend.state[0]['priority'], 1)

    def test_new_manager_recovers_saved_journal(self):
        self.layout.stream(1, True)
        fresh = LayoutTransaction(self.backend, self.path, 'primary')
        fresh.recover()
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertFalse(self.path.exists())

    def test_hotplug_restores_connected_replacement_without_old_geometry(self):
        self.layout.stream(1, True)
        self.backend.state[0]['connected'] = False
        self.backend.state.append(output('HDMI-1', 3, False))
        self.layout.release(1)
        self.assertTrue(self.backend.state[-1]['enabled'])

    def test_no_physical_recovery_keeps_journal_and_target(self):
        self.layout.stream(1, True)
        self.backend.state[0]['connected'] = False
        with self.assertRaisesRegex(RuntimeError, 'keeping virtual'):
            self.layout.release(1)
        self.assertTrue(self.path.exists())
        self.assertIn(1, self.layout.targets)

    def test_pending_second_holder_preserves_shared_snapshot(self):
        self.layout.reserve(2)
        self.layout.release(1)
        self.assertTrue(self.layout.snapshot)
        self.assertTrue(self.path.exists())
        self.layout.release(2)
        self.assertFalse(self.layout.snapshot)

    def test_unplugged_original_primary_restores_remaining_physical_as_primary(self):
        self.backend.state.append(output('HDMI-1', 2))
        self.layout.snapshot.append(dict(name='HDMI-1', enabled=True, priority=2))
        self.layout.stream(1, True)
        self.backend.state[0]['connected'] = False
        self.layout.release(1)
        self.assertTrue(self.backend.state[-1]['enabled'])
        self.assertEqual(self.backend.state[-1]['priority'], 1)
