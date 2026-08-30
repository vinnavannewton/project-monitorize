import json
import subprocess
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from unittest.mock import patch

from monitorize.platform import system_setup


ROOT = Path(__file__).resolve().parents[2]


class SystemSetupTest(unittest.TestCase):
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
