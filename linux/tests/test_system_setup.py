import json
import subprocess
import runpy
import unittest
import xml.etree.ElementTree as ElementTree
import tempfile
from pathlib import Path
from unittest.mock import patch

from monitorize.platform import system_setup


ROOT = Path(__file__).resolve().parents[2]


class SystemSetupTest(unittest.TestCase):
    def remote_status(self,host,*,matching=True,source=True,manager_current=True):
        capability=dict(supported=True,manager='gdm.service',compositor='gnome',validated=False,message='Supported')
        with tempfile.TemporaryDirectory() as directory, \
             patch('monitorize.remote_login.settings.SETTINGS',Path(directory)/'absent'), \
             patch('monitorize.remote_login.display_managers.manager_capabilities',return_value=capability), \
             patch('monitorize.platform.remote_login_host.host_readiness',return_value=host), \
             patch.object(Path,'read_bytes',autospec=True,side_effect=lambda path: b'old' if not matching and str(path).startswith('/usr/lib/monitorize-remote-login/') else b'current'), \
             patch.object(Path,'read_text',return_value='GDM_WAYLAND_PROTOCOL = 1' if manager_current else ''), \
             patch.object(system_setup,'__file__',str(ROOT/'linux/monitorize/platform/system_setup.py') if source else '/usr/lib/python3/site-packages/monitorize/platform/system_setup.py'):
            return system_setup.remote_login_setup_status()

    def test_host_failure_is_not_hidden_by_a_missing_display_manager_module(self):
        host=dict(available=False,reason='host_incompatible',protocols={},message='VERSION and manifest disagree')
        result=self.remote_status(host,manager_current=False)
        self.assertEqual(result['message'],host['message'])
        self.assertFalse(result['available'])

    def test_source_and_native_missing_assets_have_matching_repair_instructions(self):
        host=dict(available=False,reason='missing_assets',protocols={},message='Missing')
        self.assertIn('Repair remote login',self.remote_status(host)['message'])
        self.assertIn('native package',self.remote_status(host,source=False)['message'])

    def test_app_and_privileged_checker_mismatch_is_caught_before_authorization(self):
        host=dict(available=True,reason='ready',protocols={},message='Ready')
        self.assertTrue(self.remote_status(host)['available'])
        result=self.remote_status(host,matching=False)
        self.assertFalse(result['available'])
        self.assertEqual(result['reason'],'service_update_required')
        self.assertIn('different compatibility checks',result['message'])
        self.assertIn('Repair remote login',result['message'])

    def test_new_firewall_profile_is_reloaded_before_adding_service(self):
        from unittest.mock import Mock
        namespace=runpy.run_path(str(ROOT/'packaging/common/monitorize-system-setup'))
        function=namespace['enable_firewall'];glob=function.__globals__
        commands=[]
        profiles=['ssh','ssh monitorize']
        def execute(command):
            commands.append(command)
            if '--get-services' in command:return profiles.pop(0)
            if '--get-active-zones' in command:return 'home\n  interfaces: wlan0'
            if '--query-service=monitorize' in command:return 'yes'
            return ''
        with patch.dict(glob,command_path=lambda *args:'/usr/bin/firewall-cmd',run=execute),patch.object(glob['subprocess'],'run',return_value=subprocess.CompletedProcess([],0,'running','')):
            self.assertIn('allows Monitorize',function())
        reload_index=commands.index(['/usr/bin/firewall-cmd','--reload'])
        add_index=next(i for i,c in enumerate(commands) if '--add-service=monitorize' in c)
        self.assertLess(reload_index,add_index)
        self.assertEqual(sum('--reload' in c for c in commands),1)

    def test_missing_firewall_profile_has_actionable_error_without_opening_ports(self):
        from unittest.mock import Mock
        namespace=runpy.run_path(str(ROOT/'packaging/common/monitorize-system-setup'))
        function=namespace['enable_firewall'];glob=function.__globals__
        run=Mock(return_value='ssh')
        with patch.dict(glob,command_path=lambda *args:'/usr/bin/firewall-cmd',run=run),patch.object(glob['subprocess'],'run',return_value=subprocess.CompletedProcess([],0,'running','')):
            with self.assertRaisesRegex(RuntimeError,'monitorize.xml'):function()
        self.assertFalse(any('--add-service=monitorize' in call.args[0] for call in run.call_args_list))

    def test_missing_agent_error_explains_how_to_restore_the_prompt(self):
        for detail in (
            "No authentication agent found.",
            "Error creating textual authentication agent: Error opening current controlling terminal",
        ):
            with self.subTest(detail=detail):
                result = system_setup.parse_system_setup_result(127, "", detail)
                self.assertFalse(result["success"])
                self.assertIn("Could not open", result["message"])
                self.assertIn("Install and start a Polkit authentication agent", result["message"])

    def test_denial_adds_conditional_agent_hint_but_cancellation_and_other_errors_do_not(self):
        result = system_setup.parse_system_setup_result(127, "", "Not authorized")
        self.assertIn("If no password dialog appeared", result["message"])
        for detail in ("Authorization was cancelled.", "Firewall setup failed."):
            self.assertEqual(system_setup.parse_system_setup_result(1, "", detail)["message"], detail)

    @patch.object(system_setup, "get_system_setup_status", return_value={"available": True, "input_ready": False})
    @patch.object(system_setup.shutil, "which", return_value="/usr/bin/pkexec")
    @patch.object(system_setup.subprocess, "run")
    def test_selected_actions_are_sent_to_the_packaged_polkit_helper(self, run, _which, _status):
        run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps({"success": True, "message": "Configured", "logout_required": True}), ""
        )

        result = system_setup.apply_system_setup(True, True)

        self.assertTrue(result["success"])
        self.assertTrue(result["logout_required"])
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/pkexec", str(system_setup.HELPER), "--input", "--firewall"],
        )

    @patch.object(system_setup, "get_system_setup_status", return_value={"available": True, "input_ready": False})
    def test_rejects_empty_setup_selection(self, _status):
        result = system_setup.apply_system_setup(False, False)
        self.assertFalse(result["success"])
        self.assertIn("Select at least one", result["message"])

    def test_packaged_helper_and_policy_are_restricted(self):
        helper = (ROOT / "packaging/common/monitorize-system-setup").read_text()
        policy = (ROOT / "packaging/common/io.github.vinnavannewton.monitorize.system-setup.policy").read_text()
        self.assertIn('"PKEXEC_UID"', helper)
        self.assertIn('"--input"', helper)
        self.assertIn('"--firewall"', helper)
        self.assertNotIn("shell=True", helper)
        self.assertIn("logout_required = input_changed", helper)
        self.assertIn("io.github.vinnavannewton.monitorize.system-setup", policy)
        self.assertIn("/usr/libexec/monitorize/monitorize-system-setup", policy)
        ElementTree.fromstring(policy)

    def test_setup_page_defaults_both_actions_to_checked(self):
        page = (ROOT / "linux/monitorize/qml/SystemSetupPage.qml").read_text()
        self.assertIn("Enable touch and input", page)
        self.assertIn("Allow Moonlight through the firewall", page)
        self.assertEqual(page.count("checked: true"), 2)
        self.assertIn("Authorize and finish setup", page)
        self.assertIn('statusSucceeded = result["success"] === true', page)
        self.assertIn("signal setupCompleted()", page)
        self.assertIn("signal cancellationRequested()", page)


if __name__ == "__main__":
    unittest.main()
