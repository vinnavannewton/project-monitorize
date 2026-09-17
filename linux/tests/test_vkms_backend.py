"""Unit tests for vkms_backend module."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from monitorize.platform import vkms_backend
from monitorize.platform.monitorize_vkms_cli import (
    MonitorizeVkmsClient,
    VkmsCommandError,
)

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

    def test_check_custom_edid_support_supported(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.check_capability.return_value = "supported"
        self.assertEqual(
            vkms_backend.check_custom_edid_support(client=mock_client),
            vkms_backend.CustomEdidCapability.SUPPORTED,
        )

    def test_check_custom_edid_support_unsupported(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.check_capability.return_value = "unsupported"
        self.assertEqual(
            vkms_backend.check_custom_edid_support(client=mock_client),
            vkms_backend.CustomEdidCapability.UNSUPPORTED,
        )

    def test_check_custom_edid_support_failed(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.check_capability.return_value = "check_failed"
        self.assertEqual(
            vkms_backend.check_custom_edid_support(client=mock_client),
            vkms_backend.CustomEdidCapability.CHECK_FAILED,
        )

    def test_open_monitorize_vkms_install_page(self):
        with patch("webbrowser.open", return_value=True) as mock_open:
            self.assertTrue(vkms_backend.open_monitorize_vkms_install_page())
            mock_open.assert_called_once_with(vkms_backend.MONITORIZE_VKMS_INSTALL_URL)

    def test_additional_vkms_slot_is_rejected_for_the_single_persistent_topology(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = vkms_backend.run_vkms_headless("additional", 1920, 1080, 60, "kde")
        self.assertEqual(rc, 1)
        self.assertIn("Unsupported VKMS display slot: additional", out.getvalue())

    def test_run_vkms_headless_cli_missing(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = False
        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = vkms_backend.run_vkms_headless("primary", 1920, 1080, 60, "gnome", client=mock_client)
        self.assertEqual(rc, 1)
        self.assertIn("standalone monitorize-vkms package", out.getvalue())

    def test_run_vkms_headless_success_lifecycle(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = True
        mock_client.create_display.return_value = {
            "name": "Virtual-1",
            "width": 2340,
            "height": 1080,
            "fps": 60.0,
            "card": "card0",
            "status": "active",
        }
        mock_client.remove_display.return_value = {"success": True}

        fake_stdin = io.StringIO("quit\n")
        out = io.StringIO()
        with (
            patch("sys.stdin", fake_stdin),
            patch("sys.stdout", out),
            patch("select.select", return_value=([fake_stdin], [], [])),
        ):
            rc = vkms_backend.run_vkms_headless("primary", 2340, 1080, 60, "gnome", client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.create_display.assert_called_once_with(2340, 1080, 60)
        mock_client.remove_display.assert_called_once_with("Virtual-1")
        self.assertIn("MONITORIZE_EVENT", out.getvalue())
        self.assertIn('"headless_ready"', out.getvalue())
        self.assertIn('"Virtual-1"', out.getvalue())

    def test_run_vkms_headless_edid_unsupported_event(self):
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = True
        mock_client.create_display.side_effect = VkmsCommandError(
            "Custom EDID unsupported", error_type="edid_error"
        )

        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = vkms_backend.run_vkms_headless(
                "primary", 2340, 1080, 60, "gnome", custom_mode=True, client=mock_client
            )

        self.assertEqual(rc, 1)
        self.assertIn("vkms_custom_edid_unsupported", out.getvalue())
        mock_client.remove_display.assert_not_called()


if __name__ == "__main__":
    unittest.main()
