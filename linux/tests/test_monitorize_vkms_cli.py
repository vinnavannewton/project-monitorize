"""Unit tests for the MonitorizeVkmsClient adapter and session integration."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitorize.desktop.backend import MonitorizeBackend
from monitorize.platform.monitorize_vkms_cli import (
    DEFAULT_REMOVE_TIMEOUT,
    DEFAULT_TIMEOUT,
    MONITORIZE_VKMS_NOT_INSTALLED,
    MONITORIZE_VKMS_PROTOCOL_ERROR,
    MonitorizeVkmsClient,
    MonitorizeVkmsError,
    VkmsCliNotFoundError,
    VkmsCommandError,
    VkmsProtocolError,
    format_mode,
)
from monitorize.platform import vkms_backend


class MonitorizeVkmsCliTest(unittest.TestCase):
    """Test suite for monitorize-vkms CLI adapter according to specification."""

    # ----------------------------------------------------------------------
    # TEST 1 — MODE FORMATTER
    # ----------------------------------------------------------------------
    def test_mode_formatter_standard(self):
        """Verify standard integer dimensions and refresh rate formatting."""
        self.assertEqual(format_mode(2340, 1080, 60), "2340x1080@60")
        self.assertEqual(format_mode(1920, 1080, 120), "1920x1080@120")
        self.assertEqual(format_mode(2560, 1440, 60.0), "2560x1440@60")

    def test_mode_formatter_decimal(self):
        """Verify fractional refresh rate formatting without trailing zeros or locale commas."""
        self.assertEqual(format_mode(1920, 1080, 59.94), "1920x1080@59.94")
        self.assertEqual(format_mode(2340, 1080, 59.997), "2340x1080@59.997")
        self.assertEqual(format_mode(1920, 1080, 60.00000000000001), "1920x1080@60")
        formatted = format_mode(1920, 1080, 59.94)
        self.assertNotIn(",", formatted)
        self.assertNotIn("60.00000000000001", formatted)

    def test_capability_completion_parses_multiline_status_json(self):
        process = MagicMock()
        process.readAllStandardOutput.return_value = json.dumps(
            {
                "success": True,
                "kernel_module": {"loaded": True},
                "topology": {"device_enabled": True},
                "drm": {
                    "active_connectors": [
                        {
                            "name": "Virtual-1",
                            "modes": ["2340x1080"],
                        }
                    ]
                },
            },
            indent=2,
        ).encode()
        owner = SimpleNamespace(_vkms_custom_capability_process=process)
        owner._finish_vkms_custom_capability = MagicMock()

        MonitorizeBackend._complete_vkms_custom_capability(owner, process, 0)

        self.assertEqual(
            owner._finish_vkms_custom_capability.call_args.args[0].value,
            "supported",
        )

    def test_client_parses_multiline_status_json_with_mode_array(self):
        payload = {
            "success": True,
            "kernel_module": {"loaded": True},
            "topology": {"device_enabled": True},
            "drm": {
                "active_connectors": [
                    {"name": "Virtual-1", "modes": ["2340x1080"]}
                ]
            },
        }
        mock_proc = MagicMock(
            returncode=0,
            stdout=json.dumps(payload, indent=2),
            stderr="",
        )
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = MonitorizeVkmsClient().get_status()

        self.assertEqual(result, payload)

    # ----------------------------------------------------------------------
    # TEST 2 — CLI NOT INSTALLED
    # ----------------------------------------------------------------------
    def test_cli_not_installed_raises_controlled_error(self):
        """Verify controlled MONITORIZE_VKMS_NOT_INSTALLED without FileNotFoundError traceback."""
        with patch("shutil.which", return_value=None):
            client = MonitorizeVkmsClient()
            self.assertFalse(client.is_available())
            with self.assertRaises(VkmsCliNotFoundError) as ctx:
                client.create_display(1920, 1080, 60)
            self.assertEqual(ctx.exception.error_code, MONITORIZE_VKMS_NOT_INSTALLED)
            self.assertIn("requires the standalone monitorize-vkms package", str(ctx.exception))

    # ----------------------------------------------------------------------
    # TEST 3 — CREATE SUCCESS JSON
    # ----------------------------------------------------------------------
    def test_create_display_success_json_and_argv(self):
        """Verify create_display parses actual JSON schema and builds exact argv."""
        payload = {
            "success": True,
            "width": 2340,
            "height": 1080,
            "refresh_rate": 59.997,
            "drm_connector": "Virtual-1",
            "drm_card": "card0",
            "drm_path": "/sys/class/drm/card0-Virtual-1",
            "compositor": "gnome",
            "compositor_details": {"mode": "2340x1080@59.997"},
            "status": "active",
        }
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            client = MonitorizeVkmsClient()
            result = client.create_display(2340, 1080, 60)

            self.assertEqual(result["name"], "Virtual-1")
            self.assertEqual(result["width"], 2340)
            self.assertEqual(result["height"], 1080)
            self.assertEqual(result["fps"], 59.997)
            self.assertEqual(result["card"], "card0")
            self.assertEqual(result["status"], "active")

            mock_run.assert_called_once_with(
                ["/usr/bin/monitorize-vkms", "create", "2340x1080@60", "--json"],
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT,
                check=False,
            )

    # ----------------------------------------------------------------------
    # TEST 4 — CREATE FAILURE JSON
    # ----------------------------------------------------------------------
    def test_create_failure_json_translation(self):
        """Verify error translation of machine error codes into friendly messages."""
        cases = [
            (
                {"success": False, "error_type": "drm_error", "message": "DRM connector mode did not appear"},
                1,
                "DRM connector error",
            ),
            (
                {"ok": False, "error_code": "DRM_MODE_READY", "message": "Mode was not published"},
                1,
                "VKMS virtual display was created but DRM mode did not become ready",
            ),
            (
                {"ok": False, "error_code": "TOPOLOGY_NOT_READY", "message": "Not bootstrapped"},
                1,
                "Monitorize VKMS is installed but not ready. Reboot once after installation.",
            ),
            (
                {"ok": False, "error_code": "DISPLAY_ALREADY_ACTIVE", "message": "Already active"},
                1,
                "A Monitorize VKMS display is already active.",
            ),
            (
                {"ok": False, "error_code": "UNSUPPORTED_COMPOSITOR", "message": "Unsupported"},
                1,
                "VKMS Experimental cannot activate a display on this compositor yet.",
            ),
            (
                {"ok": False, "error_type": "polkit_denied", "message": "cancelled"},
                1,
                "Administrator authorization was denied or cancelled.",
            ),
        ]
        with patch("shutil.which", return_value="/usr/bin/monitorize-vkms"):
            client = MonitorizeVkmsClient()
            for payload, rc, expected_substr in cases:
                mock_proc = MagicMock(returncode=rc, stdout=json.dumps(payload), stderr="")
                with patch("subprocess.run", return_value=mock_proc):
                    with self.assertRaises(VkmsCommandError) as ctx:
                        client.create_display(1920, 1080, 60)
                    self.assertIn(expected_substr, str(ctx.exception))

    # ----------------------------------------------------------------------
    # TEST 5 — MALFORMED JSON
    # ----------------------------------------------------------------------
    def test_malformed_json_raises_protocol_error(self):
        """Verify invalid stdout triggers controlled VkmsProtocolError."""
        mock_proc = MagicMock(returncode=0, stdout="[CRITICAL] Memory fault\nnot a json", stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            client = MonitorizeVkmsClient()
            with self.assertRaises(VkmsProtocolError) as ctx:
                client.create_display(1920, 1080, 60)
            self.assertEqual(ctx.exception.error_code, MONITORIZE_VKMS_PROTOCOL_ERROR)

    # ----------------------------------------------------------------------
    # TEST 6 — TIMEOUT
    # ----------------------------------------------------------------------
    def test_subprocess_timeout_raises_controlled_error(self):
        """Verify subprocess.TimeoutExpired raises controlled MonitorizeVkmsError."""
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["monitorize-vkms"], timeout=25.0),
            ),
        ):
            client = MonitorizeVkmsClient()
            with self.assertRaises(MonitorizeVkmsError) as ctx:
                client.create_display(1920, 1080, 60)
            self.assertIn("timed out", str(ctx.exception))

    # ----------------------------------------------------------------------
    # TEST 7 — REMOVE
    # ----------------------------------------------------------------------
    def test_remove_display_constructs_safe_argv(self):
        """Verify remove_display builds exact CLI argv without shell=True."""
        payload = {"success": True, "removed": True, "message": "Virtual display removed."}
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            client = MonitorizeVkmsClient()
            res = client.remove_display("Virtual-2")
            self.assertTrue(res.get("success"))

            mock_run.assert_called_once_with(
                ["/usr/bin/monitorize-vkms", "remove", "--json"],
                capture_output=True,
                text=True,
                timeout=DEFAULT_REMOVE_TIMEOUT,
                check=False,
            )

    # ----------------------------------------------------------------------
    # TEST 8 — SESSION INTEGRATION
    # ----------------------------------------------------------------------
    def test_session_integration_lifecycle(self):
        """Verify run_vkms_headless lifecycle: create, emit event, wait, remove."""
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = True
        mock_client.create_display.return_value = {
            "name": "Virtual-3",
            "width": 2340,
            "height": 1080,
            "fps": 60.0,
            "card": "card0",
            "status": "active",
        }
        mock_client.remove_display.return_value = {"success": True}

        fake_stdin = io.StringIO("quit\n")
        captured_stdout = io.StringIO()

        with (
            patch("sys.stdin", fake_stdin),
            patch("sys.stdout", captured_stdout),
            patch("select.select", return_value=([fake_stdin], [], [])),
        ):
            rc = vkms_backend.run_vkms_headless(
                "primary", 2340, 1080, 60, "gnome", client=mock_client
            )

        self.assertEqual(rc, 0)
        mock_client.create_display.assert_called_once_with(2340, 1080, 60)
        mock_client.remove_display.assert_called_once_with("Virtual-3")

        output = captured_stdout.getvalue()
        self.assertIn("MONITORIZE_EVENT", output)
        self.assertIn('"type":"headless_ready"', output)
        self.assertIn('"name":"Virtual-3"', output)
        self.assertIn('"width":2340', output)
        self.assertIn('"height":1080', output)
        self.assertIn('"vkms":true', output)

    # ----------------------------------------------------------------------
    # TEST 9 — CREATE FAIL DOES NOT CALL REMOVE UNNECESSARILY
    # ----------------------------------------------------------------------
    def test_create_fail_does_not_call_remove(self):
        """Verify failed create never invokes remove on unknown connector."""
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = True
        mock_client.create_display.side_effect = VkmsCommandError("Create failed", error_type="invalid_mode")

        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            rc = vkms_backend.run_vkms_headless(
                "primary", 100, 100, 60, "gnome", client=mock_client
            )

        self.assertEqual(rc, 1)
        mock_client.create_display.assert_called_once()
        mock_client.remove_display.assert_not_called()

    # ----------------------------------------------------------------------
    # TEST 10 — SUNSHINE FAILURE AFTER CREATE
    # ----------------------------------------------------------------------
    def test_failure_after_create_triggers_cleanup(self):
        """Verify cleanup removes display if session terminates unexpectedly after create."""
        mock_client = MagicMock(spec=MonitorizeVkmsClient)
        mock_client.is_available.return_value = True
        mock_client.create_display.return_value = {
            "name": "Virtual-2",
            "width": 1920,
            "height": 1080,
            "fps": 60,
            "card": "card0",
            "status": "active",
        }
        mock_client.remove_display.return_value = {"success": True}

        # Simulate exception during wait loop (e.g. streaming process crashed)
        with (
            patch("select.select", side_effect=RuntimeError("Streaming service died")),
            patch("sys.stdout", io.StringIO()),
        ):
            rc = vkms_backend.run_vkms_headless(
                "primary", 1920, 1080, 60, "gnome", client=mock_client
            )

        self.assertEqual(rc, 1)
        mock_client.remove_display.assert_called_once_with("Virtual-2")

    # ----------------------------------------------------------------------
    # TEST 11 — NON-VKMS REGRESSION
    # ----------------------------------------------------------------------
    def test_non_vkms_regression_gnome_native_and_mirror(self):
        """Verify GNOME native Meta-* and Mirror mode do NOT call monitorize-vkms."""
        from monitorize.streaming import headless_virtual_display

        with (
            patch("monitorize.streaming.headless_virtual_display.run_gnome_headless", return_value=0) as mock_gnome,
            patch.object(MonitorizeVkmsClient, "create_display") as mock_vkms_create,
        ):
            with patch("sys.argv", ["headless_virtual_display.py", "1920", "1080", "60", "primary", "gnome", "native"]):
                rc = headless_virtual_display.main()
                self.assertEqual(rc, 0)
                mock_gnome.assert_called_once()
                mock_vkms_create.assert_not_called()

    # ----------------------------------------------------------------------
    # TEST 12 — CUSTOM RESOLUTION
    # ----------------------------------------------------------------------
    def test_arbitrary_custom_resolution_uses_direct_cli_create(self):
        """Verify custom resolution 2340x1080@60 delegates directly to create without internal EDID logic."""
        payload = {
            "success": True,
            "width": 2340,
            "height": 1080,
            "refresh_rate": 60.0,
            "drm_connector": "Virtual-1",
            "drm_card": "card0",
            "status": "active",
        }
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            client = MonitorizeVkmsClient()
            client.create_display(2340, 1080, 60)
            mock_run.assert_called_once_with(
                ["/usr/bin/monitorize-vkms", "create", "2340x1080@60", "--json"],
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT,
                check=False,
            )

    # ----------------------------------------------------------------------
    # TEST 13 — STANDARD RESOLUTION
    # ----------------------------------------------------------------------
    def test_standard_resolution_uses_same_create_api(self):
        """Verify preset resolution 1920x1080@60 uses the exact same standalone create API."""
        payload = {
            "success": True,
            "width": 1920,
            "height": 1080,
            "refresh_rate": 60.0,
            "drm_connector": "Virtual-1",
            "drm_card": "card0",
            "status": "active",
        }
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            client = MonitorizeVkmsClient()
            client.create_display(1920, 1080, 60)
            mock_run.assert_called_once_with(
                ["/usr/bin/monitorize-vkms", "create", "1920x1080@60", "--json"],
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT,
                check=False,
            )

    # ----------------------------------------------------------------------
    # TEST 14 — CONNECTOR NOT HARD-CODED
    # ----------------------------------------------------------------------
    def test_connector_name_dynamic_not_hardcoded(self):
        """Verify dynamic connector like Virtual-7 is preserved through create, event, and remove."""
        payload = {
            "success": True,
            "width": 2340,
            "height": 1600,
            "refresh_rate": 60.0,
            "drm_connector": "Virtual-7",
            "drm_card": "card1",
            "status": "active",
        }
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            client = MonitorizeVkmsClient()
            result = client.create_display(2340, 1600, 60)
            self.assertEqual(result["name"], "Virtual-7")
            self.assertEqual(result["card"], "card1")

    # ----------------------------------------------------------------------
    # TEST 15 — JSON STDOUT CLEANNESS
    # ----------------------------------------------------------------------
    def test_json_stdout_cleanness_with_ambient_lines(self):
        """Verify adapter cleanly parses the JSON object even if ambient log lines precede it."""
        multiline_stdout = (
            "Kernel module monitorize_vkms loaded\n"
            "Connector connector0 found\n"
            '{"success": true, "width": 1920, "height": 1080, "refresh_rate": 60, "drm_connector": "Virtual-1", "drm_card": "card0", "status": "active"}\n'
        )
        mock_proc = MagicMock(returncode=0, stdout=multiline_stdout, stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/monitorize-vkms"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            client = MonitorizeVkmsClient()
            res = client.create_display(1920, 1080, 60)
            self.assertEqual(res["name"], "Virtual-1")
            self.assertEqual(res["width"], 1920)


if __name__ == "__main__":
    unittest.main()
