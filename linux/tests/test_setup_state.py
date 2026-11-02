"""Interrupted setup must never be mistaken for cancellation or completion."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitorize.platform import setup_state, system_setup


class SetupStateTests(unittest.TestCase):
    def receipt(self):
        return setup_state.new_receipt(os.getuid(), ['input', 'firewall'], 'a' * 32)

    def test_live_and_uninspectable_helpers_remain_busy(self):
        value = self.receipt()
        value['actions']['input'] = dict(state='started', success=False, message='Applying')
        for live in (True, None):
            with patch.object(setup_state, 'process_alive', return_value=live):
                status = setup_state.observe_receipt(value)
            self.assertTrue(status['busy'])
            self.assertFalse(status['interrupted'])
            self.assertEqual(status['unknown_actions'], [])

    def test_dead_helper_leaves_started_action_unknown_and_preserves_finished_action(self):
        value = self.receipt()
        value['actions']['input'] = dict(state='succeeded', success=True, message='Input configured')
        value['actions']['firewall'] = dict(state='started', success=False, message='Applying')
        with patch.object(setup_state, 'process_alive', return_value=False):
            status = setup_state.observe_receipt(value)
        self.assertFalse(status['busy'])
        self.assertTrue(status['interrupted'])
        self.assertEqual(status['unknown_actions'], ['firewall'])
        self.assertEqual(status['completed_actions'], {'input': 'Input configured'})

    def test_finished_receipt_does_not_depend_on_a_reused_pid(self):
        value = self.receipt()
        value['actions'] = {name: dict(state='succeeded', success=True, message='Verified')
                            for name in value['selected']}
        value['phase'] = 'finished'
        with patch.object(setup_state, 'process_alive') as inspect:
            status = setup_state.observe_receipt(value)
        inspect.assert_not_called()
        self.assertFalse(status['interrupted'])
        self.assertFalse(status['busy'])

    def test_retry_retains_other_completed_actions(self):
        value = self.receipt()
        value['history']['input'] = dict(state='succeeded', success=True, message='Input configured')
        later = setup_state.new_receipt(os.getuid(), ['firewall'], 'b' * 32, value)
        self.assertEqual(later['history'], value['history'])
        self.assertEqual(later['actions']['firewall']['state'], 'pending')

    def test_invalid_identity_or_contradictory_state_is_rejected(self):
        for field, replacement in [('uid', -1), ('request_id', 'bad'), ('selected', [{}]),
                                   ('process', {}), ('phase', 'cancelled')]:
            value = self.receipt()
            value[field] = replacement
            with self.assertRaises(ValueError):
                setup_state.validate_receipt(value, os.getuid())
        value = self.receipt()
        value['actions']['input']['success'] = True
        with self.assertRaises(ValueError):
            setup_state.validate_receipt(value, os.getuid())

    def test_receipt_reader_rejects_links_unsafe_modes_and_oversize_files(self):
        with tempfile.TemporaryDirectory(prefix='.monitorize-state-test-', dir=Path.home()) as directory:
            directory = Path(directory)
            path = directory / f'{os.getuid()}.json'
            data = json.dumps(self.receipt())
            path.write_text(data)
            self.assertEqual(setup_state.read_receipt(os.getuid(), directory=directory,
                                                     owner_uid=os.getuid())['request_id'], 'a' * 32)
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                setup_state.read_receipt(os.getuid(), directory=directory, owner_uid=os.getuid())
            path.chmod(0o600)
            link = directory / 'other'
            os.link(path, link)
            with self.assertRaises(ValueError):
                setup_state.read_receipt(os.getuid(), directory=directory, owner_uid=os.getuid())
            link.unlink()
            path.write_text('x' * (setup_state.LIMIT + 1))
            with self.assertRaises(ValueError):
                setup_state.read_receipt(os.getuid(), directory=directory, owner_uid=os.getuid())
            path.unlink()
            path.symlink_to(directory / 'other')
            with self.assertRaises(OSError):
                setup_state.read_receipt(os.getuid(), directory=directory, owner_uid=os.getuid())

    def test_results_cannot_cross_request_boundaries(self):
        response = {'protocol': 2, 'request_id': 'b' * 32, 'success': True}
        result = system_setup.parse_system_setup_result(0, json.dumps(response), '', 'a' * 32)
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        result = system_setup.parse_system_setup_result(0, '{"success":true}', '', 'a' * 32)
        self.assertTrue(result['outcome_unknown'])

    def test_reboot_invalidates_live_receipt(self):
        value = copy.deepcopy(self.receipt()['process'])
        value['boot'] = '0' * 36
        self.assertFalse(setup_state.process_alive(value))
