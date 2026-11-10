import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from monitorize.desktop.remote_layout import RemoteLayout
from monitorize.desktop.remote_login_runtime import Participant
from monitorize.platform.session_layout import LayoutTransaction
from monitorize.platform.xrandr_layout import resolve_output
from linux.tests.test_additional_layout_backends import XServer, xoutput
from linux.tests.test_session_layout import SimulatedKde, output


class Bridge:
    def __init__(self, transaction):self.layout=transaction;self.started=False
    def start(self):self.started=True
    def command(self, operation, **values):
        slot=values.get('slot',1)
        if operation=='ping':return {'borrowed':list(self.layout.targets)}
        if operation=='configure':self.layout.mode=values['mode']
        elif operation=='attach':self.layout.reserve(slot,(values['name'],));self.layout.ready(slot,values['name'])
        elif operation=='stream':self.layout.stream(slot,values['active'])
        elif operation=='detach':self.layout.release(slot)


class RemoteLayoutTest(TestCase):
    def test_x11_alias_passes_preflight_and_sends_the_reserved_drm_name(self):
        backend = XServer()
        virtual = dict(xoutput('Virtual-1-1'), connector_id=48)
        backend.state.append(virtual)
        guardian = Mock()
        layout = RemoteLayout(backend=backend, guardian=guardian)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connector = root / 'card2-Virtual-1'
            connector.mkdir()
            (connector / 'connector_id').write_text('48')
            with patch('monitorize.platform.xrandr_layout.resolve_output',
                       side_effect=lambda name, outputs, **kwargs: resolve_output(name, outputs, root, **kwargs)):
                layout.update('virtual_only', True, 'Virtual-1')
                guardian.command.assert_any_call('attach', slot=1, name='Virtual-1')
                layout.restore()
                guardian.reset_mock()
                backend.state = [virtual]
                layout.update('virtual_only', True, 'Virtual-1')
        guardian.start.assert_not_called()

    def test_remote_virtual_only_restore_and_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=SimulatedKde();backend.state.append(output('Virtual-1',2))
            transaction=LayoutTransaction(backend,Path(directory)/'journal.json','virtual_only')
            layout=RemoteLayout(backend=backend,guardian=Bridge(transaction))
            layout.update('virtual_only',True,'Virtual-1')
            self.assertFalse(backend.state[0]['enabled'])
            self.assertEqual(backend.state[1]['priority'],1)
            layout.update('virtual_only',False)
            self.assertTrue(backend.state[0]['enabled'])
            self.assertTrue(backend.state[1]['enabled'])
            layout.update('virtual_only',True,'Virtual-1')
            self.assertFalse(backend.state[0]['enabled'])
            layout.restore()
            self.assertEqual(backend.state[0]['priority'],1)

    def test_primary_keeps_physical_enabled_and_restores_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=SimulatedKde();backend.state.append(output('Virtual-1',2))
            transaction=LayoutTransaction(backend,Path(directory)/'journal.json','primary')
            layout=RemoteLayout(backend=backend,guardian=Bridge(transaction))
            layout.update('primary',True,'Virtual-1')
            self.assertTrue(backend.state[0]['enabled'])
            self.assertEqual(backend.state[1]['priority'],1)
            layout.update('extend',True,'Virtual-1')
            self.assertEqual(backend.state[0]['priority'],1)

    def test_recovered_guardian_is_reattached_instead_of_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=SimulatedKde();backend.state.append(output('Virtual-1',2))
            transaction=LayoutTransaction(backend,Path(directory)/'journal.json','virtual_only')
            layout=RemoteLayout(backend=backend,guardian=Bridge(transaction))
            layout.update('virtual_only',True,'Virtual-1')
            transaction.release(1)  # Simulate independent guardian recovery.
            self.assertTrue(backend.state[0]['enabled'])
            layout.update('virtual_only',True,'Virtual-1')
            self.assertFalse(backend.state[0]['enabled'])
            layout.restore()

    def test_restore_error_does_not_swallow_stop_or_close(self):
        participant=Participant(factory=Mock(),output_finder=lambda:'Virtual-1')
        participant.generation='token';participant.layout=Mock()
        participant.layout.restore.side_effect=RuntimeError('restoration pending')
        with patch.object(participant,'_call',return_value={'state':'stopped'}) as call:
            result=participant.stop()
            call.assert_called_once_with('Stop','token')
            self.assertIn('restoration pending',result['layout_error'])
        participant.bus=Mock();bus=participant.bus
        with patch.object(participant,'_call') as call:
            participant.close();call.assert_called_once_with('ReleaseDesktop','token')
        bus.close.assert_called_once()

    def test_headless_needs_no_physical_layout_mutation(self):
        backend=SimulatedKde();backend.state=[output('Virtual-1')]
        guardian=Mock();layout=RemoteLayout(backend=backend,guardian=guardian)
        layout.update('virtual_only',True,'Virtual-1')
        guardian.start.assert_not_called()

    def test_policy_failure_restores_physical_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=SimulatedKde();backend.state.append(output('Virtual-1',2));backend.fail_disable=True
            transaction=LayoutTransaction(backend,Path(directory)/'journal.json','virtual_only')
            layout=RemoteLayout(backend=backend,guardian=Bridge(transaction))
            with self.assertRaises(RuntimeError):layout.update('virtual_only',True,'Virtual-1')
            self.assertTrue(backend.state[0]['enabled'])
            self.assertIsNone(layout.target)

    def test_participant_handles_desktop_ready_and_never_applies_in_greeter(self):
        participant=Participant(factory=Mock(),output_finder=lambda:'Virtual-1')
        participant.layout=Mock();participant.layout_mode='virtual_only'
        with patch.object(participant,'_poll',return_value={'state':'desktop_available','capture_ready':True}):
            participant.poll()
            participant.layout.update.assert_called_with('virtual_only',True,'Virtual-1')
        with patch.object(participant,'_poll',return_value={'state':'login_available','capture_ready':True}):
            participant.poll()
            participant.layout.update.assert_called_with('virtual_only',False,None)
        participant.stop()
        participant.layout.restore.assert_called_once()
