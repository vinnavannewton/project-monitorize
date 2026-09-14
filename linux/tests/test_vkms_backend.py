import io
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from monitorize.platform import vkms_backend
from monitorize.platform.vkms_edid import EdidError, generate_edid, parse_preferred_timing


ROOT = Path(__file__).resolve().parents[2]


def load_source_vkms_helper():
    path = ROOT / "packaging/common/monitorize-source-vkms-helper"
    loader = SourceFileLoader("monitorize_source_vkms_helper", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VkmsBackendTest(unittest.TestCase):
    @staticmethod
    def _drm_connector(status, modes):
        return {
            "Virtual-1": {
                "name": "Virtual-1",
                "path": "/sys/class/drm/card0-Virtual-1",
                "card": "card0",
                "status": status,
                "modes": modes,
            }
        }

    def test_drm_readiness_waits_for_initially_disconnected_connector(self):
        states = [
            {},
            self._drm_connector("disconnected", []),
            self._drm_connector("connected", ["2340x1080"]),
        ]
        messages = []
        with (
            patch.object(vkms_backend, "monitorize_drm_connectors", side_effect=states),
            patch.object(vkms_backend.time, "sleep"),
        ):
            connector = vkms_backend._wait_for_new_drm_connector(
                set(), 2340, 1080, report=messages.append
            )

        self.assertEqual(connector["status"], "connected")
        self.assertIn("Initial DRM status: disconnected", messages)
        self.assertTrue(any("became connected" in message for message in messages))

    def test_drm_readiness_waits_for_initially_unknown_connector(self):
        states = [
            self._drm_connector("unknown", []),
            self._drm_connector("connected", ["1920x1080"]),
        ]
        messages = []
        with (
            patch.object(vkms_backend, "monitorize_drm_connectors", side_effect=states),
            patch.object(vkms_backend.time, "sleep"),
        ):
            connector = vkms_backend._wait_for_new_drm_connector(
                set(), 1920, 1080, report=messages.append
            )

        self.assertEqual(connector["name"], "Virtual-1")
        self.assertIn("Initial DRM status: unknown", messages)

    def test_drm_readiness_reports_connector_appearance_timeout(self):
        with (
            patch.object(vkms_backend, "monitorize_drm_connectors", return_value={}),
            patch.object(vkms_backend.time, "monotonic", side_effect=[0, 0, 6]),
            patch.object(vkms_backend.time, "sleep"),
        ):
            with self.assertRaisesRegex(vkms_backend.VkmsError, "FAIL_STAGE=DRM_CONNECTOR_APPEAR"):
                vkms_backend._wait_for_new_drm_connector(set(), 1920, 1080, timeout=5)

    def test_drm_readiness_reports_status_timeout(self):
        with (
            patch.object(
                vkms_backend,
                "monitorize_drm_connectors",
                return_value=self._drm_connector("disconnected", []),
            ),
            patch.object(vkms_backend.time, "monotonic", side_effect=[0, 0, 0, 6]),
            patch.object(vkms_backend.time, "sleep"),
        ):
            with self.assertRaisesRegex(vkms_backend.VkmsError, "FAIL_STAGE=DRM_CONNECTOR_STATUS"):
                vkms_backend._wait_for_new_drm_connector(set(), 1920, 1080, timeout=5)

    def test_drm_readiness_waits_for_mode_after_connected_status(self):
        states = [
            self._drm_connector("connected", []),
            self._drm_connector("connected", ["2340x1080"]),
        ]
        messages = []
        with (
            patch.object(vkms_backend, "monitorize_drm_connectors", side_effect=states),
            patch.object(vkms_backend.time, "sleep"),
        ):
            connector = vkms_backend._wait_for_new_drm_connector(
                set(), 2340, 1080, report=messages.append
            )

        self.assertEqual(connector["modes"], ["2340x1080"])
        self.assertIn("DRM connector is connected; waiting for requested mode 2340x1080", messages)
        self.assertIn("DRM modes ready: 2340x1080", messages)

    def test_drm_readiness_reports_mode_timeout(self):
        with (
            patch.object(
                vkms_backend,
                "monitorize_drm_connectors",
                return_value=self._drm_connector("connected", []),
            ),
            patch.object(vkms_backend.time, "monotonic", side_effect=[0, 0, 0, 0, 6]),
            patch.object(vkms_backend.time, "sleep"),
        ):
            with self.assertRaisesRegex(vkms_backend.VkmsError, "FAIL_STAGE=DRM_MODE_READY"):
                vkms_backend._wait_for_new_drm_connector(set(), 1920, 1080, timeout=5)

    def test_monitorize_drm_connectors_reads_kernel_hotplug_state_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drm_root = root / "drm"
            connector = drm_root / "card4-Virtual-2"
            connector.mkdir(parents=True)
            (root / "faux" / "monitorize").mkdir(parents=True)
            (connector / "device").symlink_to(root / "faux" / "monitorize")
            (connector / "status").write_text("connected\n")
            (connector / "modes").write_text("2340x1080\n1920x1080\n")

            self.assertEqual(
                vkms_backend.monitorize_drm_connectors(drm_root),
                {
                    "Virtual-2": {
                        "name": "Virtual-2",
                        "path": str(connector),
                        "card": "card4",
                        "status": "connected",
                        "modes": ["2340x1080", "1920x1080"],
                    }
                },
            )

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

    def test_capability_check_requires_the_persistent_bootstrap_connector(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        probe = helper.split("def _probe_custom_edid_support", 1)[1]
        self.assertIn("_require_bootstrap(root, logs)", probe)
        self.assertNotIn("mkdir()", probe)

    def test_helper_requires_the_bootstrapped_custom_module(self):
        helper = load_source_vkms_helper()
        with patch.object(helper, "_module_loaded", return_value=False):
            with self.assertRaisesRegex(helper.VkmsHelperError, "bootstrap is not initialized"):
                helper._require_bootstrap(Path("/config"), [])

    def test_helper_refuses_a_loaded_stock_module(self):
        helper = load_source_vkms_helper()
        with patch.object(
            helper,
            "_module_loaded",
            side_effect=lambda name: name == helper.STOCK_MODULE_NAME,
        ):
            with self.assertRaisesRegex(helper.VkmsHelperError, "bootstrap is not initialized"):
                helper._require_bootstrap(Path("/config"), [])

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
    @patch.object(vkms_backend.select, "select", return_value=([object()], [], []))
    @patch.object(vkms_backend.sys, "stdin", io.StringIO("quit\n"))
    @patch.object(vkms_backend, "configure_compositor_output")
    @patch.object(vkms_backend, "_wait_for_new_output")
    @patch.object(vkms_backend, "_wait_for_inventory_settle", return_value={"eDP-1": {}})
    @patch.object(vkms_backend, "active_compositor_outputs", return_value={"eDP-1": {}})
    @patch.object(vkms_backend, "_wait_for_new_drm_connector", return_value={"name": "Virtual-4", "card": "card4", "path": "/sys/class/drm/card4-Virtual-4", "status": "connected", "modes": ["1920x1080"]})
    @patch("monitorize.platform.gnome_virtual_monitor.remove_new_vkms_monitors_from_layout", return_value=False)
    @patch("monitorize.platform.gnome_virtual_monitor.wait_for_new_vkms_connector", return_value=("Virtual-4", None, ""))
    @patch("monitorize.platform.gnome_virtual_monitor.activate_discovered_vkms_monitor")
    @patch("monitorize.platform.gnome_virtual_monitor._mutter_state")
    @patch.object(vkms_backend, "_helper_response")
    def test_gnome_vkms_activates_discovered_drm_connector_without_native_fallback(
        self, helper, mutter_state, activate, wait_physical, _cleanup_layout, _drm_wait, _outputs, _settle, wait_new,
        configure, _stdin_select, _signal,
    ):
        mode = ("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})
        state = (
            1,
            [(("eDP-1", "Vendor", "Panel", "1"), [mode], {})],
            [(0, 0, 1.0, 0, True, [("eDP-1",)])],
            {},
        )
        mutter_state.return_value = state
        helper.return_value = {"success": True, "changed": False}
        activate.return_value = (
            True,
            {"name": "Virtual-4", "width": 1920, "height": 1080, "refresh_rate": 60.0},
            "Mutter activated Virtual-4 at 1920x1080@60Hz",
        )
        phases = []
        _drm_wait.side_effect = lambda *_args, **_kwargs: (
            phases.append("drm-ready") or {
                "name": "Virtual-4", "card": "card4",
                "path": "/sys/class/drm/card4-Virtual-4", "status": "connected",
                "modes": ["1920x1080"],
            }
        )

        def physical_wait(*_args, **_kwargs):
            self.assertEqual(phases, ["drm-ready"])
            phases.append("mutter-discovery")
            return "Virtual-4", None, ""

        wait_physical.side_effect = physical_wait

        self.assertEqual(
            vkms_backend.run_vkms_headless("primary", 1920, 1080, 60, "gnome"),
            0,
        )

        activate.assert_called_once()
        wait_physical.assert_called_once()
        self.assertEqual(phases, ["drm-ready", "mutter-discovery"])
        wait_new.assert_not_called()
        configure.assert_not_called()
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["destroy", "create", "destroy"])

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

    def test_additional_vkms_slot_is_rejected_for_the_single_persistent_topology(self):
        self.assertEqual(
            vkms_backend.run_vkms_headless("additional", 1920, 1080, 60, "kde"),
            1,
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

    def test_helper_has_a_binary_custom_edid_path_before_enable(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        custom = helper.split("def _enable_connector", 1)[1]
        self.assertIn('path.open("wb", buffering=0)', helper)
        self.assertIn('connector / "edid_enabled"', custom)
        self.assertLess(custom.index("_write_bytes(edid, custom_edid)"), custom.index('_write(connector / "enabled", "1")'))
        self.assertLess(custom.index('_write(connector / "status", "1")'), custom.index('_write(connector / "enabled", "1")'))
        self.assertIn('"error_kind": "custom_edid_unsupported"', helper)

    def test_helper_exposes_one_persistent_instance_slot(self):
        helper = (ROOT / "packaging/common/monitorize-source-vkms-helper").read_text()
        self.assertIn('"primary": "monitorize"', helper)
        self.assertNotIn('"additional": "monitorize-2"', helper)
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
