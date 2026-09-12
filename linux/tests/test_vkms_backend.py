import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from monitorize.platform import vkms_backend


ROOT = Path(__file__).resolve().parents[2]


class VkmsBackendTest(unittest.TestCase):
    def test_resolution_options_read_monitorize_drm_modes_and_keep_custom_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drm_root = root / "drm"
            connector = drm_root / "card3-Virtual-1"
            connector.mkdir(parents=True)
            (root / "faux" / "monitorize").mkdir(parents=True)
            (connector / "device").symlink_to(root / "faux" / "monitorize")
            (connector / "modes").write_text("1920x1080\n2560x1440\n1920x1080\n")

            physical = drm_root / "card1-DP-1"
            physical.mkdir()
            (root / "physical").mkdir()
            (physical / "device").symlink_to(root / "physical")
            (physical / "modes").write_text("3840x2160\n")

            self.assertEqual(
                vkms_backend.resolution_options(drm_root),
                ["2560x1440", "1920x1080", "Custom..."],
            )

    def test_normal_vkms_resolution_uses_existing_sanitization_without_probe(self):
        self.assertEqual(
            vkms_backend.sanitize_vkms_resolution(1366, 768),
            (1366, 768),
        )
        self.assertEqual(
            vkms_backend.sanitize_vkms_resolution(1234, 567),
            (1920, 1080),
        )

    def test_custom_edid_capability_response_states(self):
        self.assertEqual(
            vkms_backend.custom_edid_capability_from_response({"capability": "supported"}),
            vkms_backend.CustomEdidCapability.SUPPORTED,
        )
        self.assertEqual(
            vkms_backend.custom_edid_capability_from_response({"capability": "unsupported"}),
            vkms_backend.CustomEdidCapability.UNSUPPORTED,
        )
        with self.assertRaises(vkms_backend.VkmsError):
            vkms_backend.custom_edid_capability_from_response({"capability": "unknown"})

    def test_capability_probe_uses_unique_disabled_connector_and_cleans_up(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        self.assertIn("monitorize-capability-probe-{uuid.uuid4().hex}", helper)
        self.assertIn("connector.mkdir()", helper)
        self.assertNotIn("plane.mkdir()", helper.split("def _probe_custom_edid_support", 1)[1])
        probe = helper.split("def _probe_custom_edid_support", 1)[1]
        self.assertIn("_remove_dir(connector)", probe)
        self.assertIn("_remove_dir(probe)", probe)

    @patch.object(vkms_backend.signal, "signal")
    @patch.object(vkms_backend.select, "select", return_value=([object()], [], []))
    @patch.object(vkms_backend.sys, "stdin", io.StringIO("quit\n"))
    @patch.object(vkms_backend, "configure_compositor_output")
    @patch.object(vkms_backend, "_wait_for_new_output", return_value=("Virtual-7", {}))
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={"eDP-1": {}})
    @patch.object(vkms_backend, "_helper_response")
    def test_holder_recovers_creates_discovers_configures_and_destroys(
        self, helper, _settle, _new, configure, _stdin_select, _signal
    ):
        helper.side_effect = [
            {"success": True, "changed": True},
            {"success": True, "changed": True},
            {"success": True, "changed": True},
        ]
        configure.return_value = (
            True,
            {"width": 1920, "height": 1080, "refresh_rate": 59.94},
            "KDE applied VKMS mode 1920x1080@59.94Hz",
        )

        self.assertEqual(
            vkms_backend.run_vkms_headless("primary", 1920, 1080, 120, "kde"),
            0,
        )

        self.assertEqual(
            [call.args[0] for call in helper.call_args_list],
            ["destroy", "create", "destroy"],
        )
        configure.assert_called_once_with("kde", "Virtual-7", 1920, 1080)

    @patch.object(vkms_backend.signal, "signal")
    @patch.object(vkms_backend, "_wait_for_new_output", side_effect=vkms_backend.VkmsError("timeout"))
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={})
    @patch.object(vkms_backend, "_helper_response")
    def test_failed_output_detection_cleans_up(self, helper, _settle, _new, _signal):
        helper.return_value = {"success": True, "changed": False}
        self.assertEqual(
            vkms_backend.run_vkms_headless("primary", 1920, 1080, 60, "kde"),
            1,
        )
        self.assertEqual(
            [call.args[0] for call in helper.call_args_list],
            ["destroy", "create", "destroy"],
        )

    def test_privileged_helper_rejects_arbitrary_operations(self):
        helper = ROOT / "packaging/common/monitorize-source-vkms-helper"
        result = subprocess.run(
            [str(helper), "remove-everything"],
            capture_output=True,
            text=True,
            env={**os.environ, "PKEXEC_UID": str(os.getuid())},
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_polkit_policy_binds_only_the_fixed_helper_path(self):
        policy = (
            ROOT
            / "packaging/common/io.github.vinnavannewton.monitorize.source-vkms.policy"
        ).read_text()
        self.assertIn(
            "/usr/libexec/monitorize/monitorize-source-vkms-helper",
            policy,
        )
        self.assertNotIn("allow_active>yes", policy)


if __name__ == "__main__":
    unittest.main()
