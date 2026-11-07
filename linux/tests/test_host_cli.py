import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from monitorize import host_cli


class HostCliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = host_cli.main(list(arguments))
        return result, json.loads(output.getvalue())

    def test_status_targets_the_boot_listener_at_the_greeter(self):
        with patch.object(host_cli, '_active_boot_status', return_value={'controller': 'boot'}):
            self.assertEqual(self.run_cli('status', '--json'), (0, {'controller': 'boot'}))

    def test_boot_query_failure_does_not_report_desktop_stopped(self):
        with (patch.object(host_cli, '_boot_context', return_value=True),
              patch.object(host_cli, '_boot_runtime', side_effect=RuntimeError('Boot service unavailable')),
              patch('monitorize.desktop.controller_launcher.require_local_graphical_session',
                    side_effect=RuntimeError('No desktop')),
              patch('monitorize.desktop.host_control.request') as desktop):
            self.assertEqual(self.run_cli('status', '--json'),
                             (1, {'success': False, 'message': 'Boot service unavailable'}))
            desktop.assert_not_called()

    def test_status_reports_boot_failure_alongside_verified_desktop_state(self):
        with (patch.object(host_cli, '_boot_context', return_value=True),
              patch.object(host_cli, '_boot_runtime', side_effect=RuntimeError('Boot service unavailable')),
              patch('monitorize.desktop.controller_launcher.require_local_graphical_session'),
              patch('monitorize.desktop.host_control.request', return_value={'state': 'ready'})):
            self.assertEqual(self.run_cli('status', '--json'),
                             (0, {'state': 'ready', 'boot_error': 'Boot service unavailable'}))

    def test_reload_requests_an_explicit_runtime_restart(self):
        with patch('monitorize.desktop.host_control.request', return_value={'state': 'ready'}) as call:
            self.assertEqual(self.run_cli('reload', '--restart', '--json'), (0, {'state': 'ready'}))
        call.assert_called_once_with('reload', restart=True)

    def test_pair_targets_one_verified_listener_and_hides_the_pin(self):
        request_id = 'a' * 32
        host = Mock(instance=2)
        host.pending.return_value = [{'id': request_id, 'name': 'Tablet'}]
        host.approve.return_value = True, 'Paired successfully.'
        with (patch.object(host_cli, '_control', return_value={'instances': [2]}),
              patch('monitorize.platform.pairing_api.resolve_hosts', return_value=[host]) as resolve,
              patch('sys.stdin', io.StringIO('0321\n'))):
            self.assertEqual(self.run_cli('pair', '--pin-stdin', '--json'),
                             (0, {'success': True, 'message': 'Paired successfully.'}))
        resolve.assert_called_with([2], False, external=True)
        host.approve.assert_called_once_with(request_id, '0321', 'Tablet')

    def test_pair_does_not_send_a_pin_after_host_identity_changes(self):
        host = Mock(instance=1)
        host.pending.return_value = [{'id': 'a' * 32, 'name': 'Tablet'}]
        with (patch.object(host_cli, '_control', return_value={'instances': [1]}),
              patch('monitorize.platform.pairing_api.resolve_hosts', side_effect=[[host], [Mock()]]),
              patch('sys.stdin', io.StringIO('0321\n'))):
            result, output = self.run_cli('pair', '--pin-stdin', '--json')
        self.assertEqual(result, 1)
        self.assertIn('changed identity', output['message'])
        host.approve.assert_not_called()

    def test_scoped_setup_failure_survives_machine_readable_output(self):
        failure = {'success': False, 'message': 'Authorization cancelled.'}
        with (patch('monitorize.config.host_config.initialize', return_value='/tmp/test/monitorize.json'),
              patch('monitorize.platform.host_setup.apply_setup', return_value=failure) as apply):
            result, output = self.run_cli('setup', '--firewall', '--zone', 'home', '--json')
        self.assertEqual(result, 1)
        self.assertEqual(output['message'], failure['message'])
        self.assertEqual(apply.call_args.kwargs['firewall_zones'], ['home'])
