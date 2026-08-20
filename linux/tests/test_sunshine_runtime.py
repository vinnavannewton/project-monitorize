import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from monitorize.platform import sunshine_service as service


class SunshineRuntimeTest(unittest.TestCase):
    def tearDown(self):
        service._SUNSHINE_PROCESS = None
        service._SUNSHINE_PROCESSES.clear()

    def test_explicit_binary_and_assets_take_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "sunshine"
            assets = Path(tmp) / "assets"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            assets.mkdir()

            with (
                patch.dict(os.environ, {
                    "MONITORIZE_SUNSHINE_BIN": str(binary),
                    "MONITORIZE_SUNSHINE_ASSETS_DIR": str(assets),
                }, clear=False),
                patch.object(service, "get_sunshine_config_path", return_value="/tmp/sunshine.conf"),
            ):
                self.assertEqual(
                    service.get_sunshine_candidates()[0],
                    [str(binary), "/tmp/sunshine.conf"],
                )
                self.assertEqual(service.get_sunshine_assets_dir(str(binary)), str(assets))

    def test_start_passes_assets_and_rejects_immediate_exit(self):
        alive = MagicMock()
        alive.poll.return_value = None
        exited = MagicMock()
        exited.poll.return_value = 7

        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=False),
            patch.object(service, "get_sunshine_config_dir", return_value="/tmp/config"),
            patch.object(service, "get_sunshine_candidates", return_value=[["/tmp/sunshine", "/tmp/config/sunshine.conf"]]),
            patch.object(service, "get_sunshine_assets_dir", return_value="/tmp/assets"),
            patch.object(service.time, "sleep"),
            patch.object(service.subprocess, "Popen", return_value=alive) as popen,
        ):
            ok, _ = service.start_sunshine()
            self.assertTrue(ok)
            self.assertEqual(popen.call_args.kwargs["env"]["SUNSHINE_ASSETS_DIR"], "/tmp/assets")

        service._SUNSHINE_PROCESS = None
        service._SUNSHINE_PROCESSES.clear()
        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=False),
            patch.object(service, "get_sunshine_config_dir", return_value="/tmp/config"),
            patch.object(service, "get_sunshine_candidates", return_value=[["/tmp/sunshine", "/tmp/config/sunshine.conf"]]),
            patch.object(service, "get_sunshine_assets_dir", return_value="/tmp/assets"),
            patch.object(service, "get_sunshine_last_error", return_value="encoder unavailable"),
            patch.object(service.time, "sleep"),
            patch.object(service.subprocess, "Popen", return_value=exited),
        ):
            ok, message = service.start_sunshine()
            self.assertFalse(ok)
            self.assertIn("code 7", message)
            self.assertIn("encoder unavailable", message)


if __name__ == "__main__":
    unittest.main()
