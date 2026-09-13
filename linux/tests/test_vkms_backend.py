import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from monitorize.platform import vkms_backend
from monitorize.platform.vkms_edid import EdidError, generate_edid, parse_preferred_timing


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

    def test_resolution_options_offer_normal_vkms_modes_before_a_connector_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = vkms_backend.resolution_options(Path(tmp) / "missing")
        self.assertEqual(options[0], "4096x2160")
        self.assertIn("1920x1080", options)
        self.assertEqual(options[-1], "Custom...")

    def test_normal_vkms_resolution_uses_existing_sanitization_without_probe(self):
        self.assertEqual(
            vkms_backend.sanitize_vkms_resolution(1366, 768),
            (1366, 768),
        )

    def test_custom_edid_encodes_the_proven_reference_size_and_refresh(self):
        edid = generate_edid(1920, 1080, 75)
        timing = parse_preferred_timing(edid)
        self.assertEqual(len(edid), 128)
        self.assertEqual(edid[:8], b"\x00\xff\xff\xff\xff\xff\xff\x00")
        self.assertEqual(sum(edid) % 256, 0)
        self.assertEqual((timing.width, timing.height), (1920, 1080))
        self.assertAlmostEqual(timing.refresh_hz, 75, delta=0.2)

    def test_custom_edid_preserves_the_requested_nonstandard_size(self):
        timing = parse_preferred_timing(generate_edid(2340, 1080, 60))
        self.assertEqual((timing.width, timing.height), (2340, 1080))
        self.assertAlmostEqual(timing.refresh_hz, 60, delta=0.2)

    def test_custom_edid_rejects_unrepresentable_requests(self):
        with self.assertRaises(EdidError):
            generate_edid(4096, 1080, 60)
        with self.assertRaises(EdidError):
            generate_edid(2340, 1080, 241)
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
        self.assertEqual(
            [call.kwargs["slot"] for call in helper.call_args_list],
            ["primary", "primary", "primary"],
        )
        configure.assert_called_once_with("kde", "Virtual-7", 1920, 1080, 60.0)

    @patch.object(vkms_backend.signal, "signal")
    @patch.object(vkms_backend.select, "select", return_value=([object()], [], []))
    @patch.object(vkms_backend.sys, "stdin", io.StringIO("quit\n"))
    @patch.object(vkms_backend, "configure_compositor_output")
    @patch.object(vkms_backend, "_wait_for_new_output", return_value=("Virtual-7", {}))
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={"eDP-1": {}})
    @patch.object(vkms_backend, "_helper_response")
    def test_custom_holder_bypasses_stock_sanitizer_and_uses_edid_operation(
        self, helper, _settle, _new, configure, _stdin_select, _signal
    ):
        helper.side_effect = [{"success": True, "changed": False}, {"success": True, "changed": True}, {"success": True}]
        configure.return_value = (True, {"width": 2340, "height": 1080, "refresh_rate": 60.0}, "selected")
        self.assertEqual(
            vkms_backend.run_vkms_headless("primary", 2340, 1080, 60, "kde", custom_mode=True),
            0,
        )
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["destroy", "create-custom", "destroy"])
        self.assertEqual([call.kwargs["slot"] for call in helper.call_args_list], ["primary"] * 3)
        self.assertEqual(len(helper.call_args_list[1].kwargs["edid"]), 128)
        configure.assert_called_once_with("kde", "Virtual-7", 2340, 1080, 60.0)

    @patch.object(vkms_backend.signal, "signal")
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={})
    @patch.object(vkms_backend, "_helper_response")
    def test_custom_unsupported_never_falls_back_to_a_stock_mode(
        self, helper, _settle, _signal
    ):
        helper.side_effect = [
            {"success": True, "changed": False},
            vkms_backend.VkmsCustomEdidUnsupported("no EDID attributes"),
            {"success": True},
        ]
        self.assertEqual(
            vkms_backend.run_vkms_headless("primary", 2340, 1080, 60, "kde", custom_mode=True),
            1,
        )
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["destroy", "create-custom", "destroy"])

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
        self.assertEqual([call.kwargs["slot"] for call in helper.call_args_list], ["primary"] * 3)

    @patch.object(vkms_backend.signal, "signal")
    @patch.object(vkms_backend.select, "select", return_value=([object()], [], []))
    @patch.object(vkms_backend.sys, "stdin", io.StringIO("quit\n"))
    @patch.object(vkms_backend, "configure_compositor_output")
    @patch.object(vkms_backend, "_wait_for_new_output", return_value=("Virtual-8", {}))
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={"Virtual-7": {}})
    @patch.object(vkms_backend, "_helper_response")
    def test_additional_holder_uses_its_own_fixed_slot(
        self, helper, _settle, _new, configure, _stdin_select, _signal
    ):
        helper.return_value = {"success": True, "changed": False}
        configure.return_value = (
            True,
            {"width": 1920, "height": 1080, "refresh_rate": 60.0},
            "selected",
        )
        self.assertEqual(
            vkms_backend.run_vkms_headless("additional", 1920, 1080, 60, "kde"),
            0,
        )
        self.assertEqual([call.kwargs["slot"] for call in helper.call_args_list], ["additional"] * 3)

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

    def test_helper_has_a_binary_custom_edid_path_before_enable(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        custom = helper.split("def _create_instance", 1)[1]
        self.assertIn('path.open("wb", buffering=0)', helper)
        self.assertIn('connector / "edid_enabled"', custom)
        self.assertLess(custom.index("_write_bytes(edid, custom_edid)"), custom.index('_write(instance / "enabled", "1")'))
        self.assertIn('"error_kind": "custom_edid_unsupported"', helper)

    def test_helper_exposes_only_two_fixed_instance_slots(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        self.assertIn('"primary": "monitorize"', helper)
        self.assertIn('"additional": "monitorize-2"', helper)
        self.assertIn('parser.add_argument("--slot", choices=tuple(INSTANCE_NAMES)', helper)

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
