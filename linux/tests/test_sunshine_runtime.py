import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from monitorize.platform import sunshine_service as service


class SunshineRuntimeTest(unittest.TestCase):
    def tearDown(self):
        service._SUNSHINE_PROCESS = None
        service._SUNSHINE_PROCESSES.clear()
        service._SUNSHINE_PIPEWIRE_NODES.clear()
        service._SUNSHINE_PIPEWIRE_OFFSETS.clear()
        service._SUNSHINE_PIPEWIRE_DIMS.clear()

    def test_arbitrary_binary_and_assets_are_not_adopted(self):
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
                self.assertNotIn([str(binary), "/tmp/sunshine.conf"], service.get_sunshine_candidates())
                self.assertIsNone(service.get_sunshine_assets_dir(str(binary)))

    def test_unmanaged_service_is_not_running_monitorize(self):
        with patch.object(service.socket, "create_connection") as connect:
            self.assertFalse(service.is_sunshine_running())
            connect.assert_not_called()

    def test_clear_restore_tokens_only_removes_monitorize_managed_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_home = Path(tmp) / "config"
            managed_dirs = [
                config_home / "monitorize" / "sunshine-1",
                config_home / "monitorize" / "sunshine-2",
                config_home / "monitorize" / "sunshine-profile-1" / "sunshine",
                config_home / "monitorize" / "sunshine-profile-2" / "sunshine",
                config_home / "monitorize" / "sunshine",
            ]
            token_paths = []
            for directory in managed_dirs:
                directory.mkdir(parents=True, exist_ok=True)
                for filename in service.PORTAL_RESTORE_TOKEN_FILES:
                    path = directory / filename
                    path.write_text("token")
                    token_paths.append(path)
            personal = config_home / "sunshine" / "portal_token"
            personal.parent.mkdir(parents=True)
            personal.write_text("personal-token")

            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                removed, errors = service.clear_sunshine_portal_restore_tokens()

            self.assertEqual(removed, len(token_paths))
            self.assertEqual(errors, [])
            self.assertTrue(all(not path.exists() for path in token_paths))
            self.assertEqual(personal.read_text(), "personal-token")

    def test_sync_persists_explicit_capture_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "sunshine.conf"
            config_path.write_text("capture = kwin\n")
            with (
                patch.object(service, "get_sunshine_config_path", return_value=str(config_path)),
                patch.object(service, "is_sunshine_running", return_value=False),
            ):
                ok, _ = service.sync_sunshine_stream_config(
                    "Virtual-Monitorize-1", instance=1,
                    adapter_name="/dev/dri/renderD129",
                    capture="wlr",
                )

            self.assertTrue(ok)
            self.assertIn("capture = wlr\n", config_path.read_text())
            self.assertIn("adapter_name = /dev/dri/renderD129\n", config_path.read_text())

    def test_sync_persists_vulkan_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "sunshine.conf"
            with (
                patch.object(service, "get_sunshine_config_path", return_value=str(config_path)),
                patch.object(service, "is_sunshine_running", return_value=False),
            ):
                ok, _ = service.sync_sunshine_stream_config("", encoder="Vulkan")

            self.assertTrue(ok)
            self.assertIn("encoder = vulkan\n", config_path.read_text())

    def test_reads_only_new_strict_selection_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "sunshine.log"
            log_path.write_text("old MONITORIZE_STRICT_SELECTION_FAILED\n")
            offset = log_path.stat().st_size
            log_path.write_text(
                log_path.read_text() + "new MONITORIZE_STRICT_CODEC_REJECTED\n"
            )
            with patch.object(service, "get_sunshine_config_dir", return_value=tmp):
                self.assertEqual(
                    service.get_sunshine_strict_selection_error(offset=offset),
                    "new MONITORIZE_STRICT_CODEC_REJECTED",
                )

    def test_start_passes_assets_and_rejects_immediate_exit(self):
        alive = MagicMock()
        alive.poll.return_value = None

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
        exited = MagicMock()
        exited.poll.return_value = 7
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

    def test_start_passes_selected_nvidia_device_environment(self):
        alive = MagicMock()
        alive.poll.return_value = None
        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=False),
            patch.object(service, "get_sunshine_config_dir", return_value="/tmp/config"),
            patch.object(service, "get_sunshine_candidates", return_value=[["/tmp/sunshine"]]),
            patch.object(service, "get_sunshine_assets_dir", return_value=None),
            patch.object(service.time, "sleep"),
            patch.object(service.subprocess, "Popen", return_value=alive) as popen,
        ):
            ok, _ = service.start_sunshine(
                extra_environment={"CUDA_VISIBLE_DEVICES": "1"}
            )

        self.assertTrue(ok)
        self.assertEqual(
            popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "1"
        )

    def test_start_restarts_managed_process_when_pipewire_node_changes(self):
        running = MagicMock(pid=123)
        launched = MagicMock()
        launched.poll.return_value = None
        service._SUNSHINE_PROCESSES[1] = running

        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=True),
            patch("builtins.open", mock_open(read_data=b"SUNSHINE_PIPEWIRE_NODE=42\0")),
            patch.object(service, "stop_sunshine") as stop,
            patch.object(service, "get_sunshine_config_dir", return_value="/tmp/config"),
            patch.object(service, "get_sunshine_candidates", return_value=[["/tmp/sunshine", "/tmp/config/sunshine.conf"]]),
            patch.object(service, "get_sunshine_assets_dir", return_value=None),
            patch.object(service.time, "sleep"),
            patch.object(service.subprocess, "Popen", return_value=launched) as popen,
        ):
            ok, _ = service.start_sunshine(pipewire_node=84)

        self.assertTrue(ok)
        stop.assert_called_once_with(1, clear_pipewire_node=False)
        self.assertEqual(popen.call_args.kwargs["env"]["SUNSHINE_PIPEWIRE_NODE"], "84")

    def test_start_restarts_managed_process_to_clear_pipewire_node(self):
        running = MagicMock(pid=123)
        launched = MagicMock()
        launched.poll.return_value = None
        service._SUNSHINE_PROCESSES[1] = running
        service.set_sunshine_pipewire_node(42)

        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=True),
            patch("builtins.open", mock_open(read_data=b"SUNSHINE_PIPEWIRE_NODE=42\0")),
            patch.object(service, "stop_sunshine") as stop,
            patch.object(service, "get_sunshine_config_dir", return_value="/tmp/config"),
            patch.object(service, "get_sunshine_candidates", return_value=[["/tmp/sunshine", "/tmp/config/sunshine.conf"]]),
            patch.object(service, "get_sunshine_assets_dir", return_value=None),
            patch.object(service.time, "sleep"),
            patch.object(service.subprocess, "Popen", return_value=launched) as popen,
        ):
            ok, _ = service.start_sunshine()

        self.assertTrue(ok)
        stop.assert_called_once_with(1, clear_pipewire_node=False)
        self.assertNotIn("SUNSHINE_PIPEWIRE_NODE", popen.call_args.kwargs["env"])

    def test_start_does_not_restart_untracked_sunshine(self):
        with (
            patch.object(service, "ensure_sunshine_tray_disabled"),
            patch.object(service, "is_sunshine_running", return_value=True),
            patch.object(service, "stop_sunshine") as stop,
            patch.object(service.subprocess, "Popen") as popen,
        ):
            ok, _ = service.start_sunshine(pipewire_node=84)

        self.assertTrue(ok)
        stop.assert_not_called()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
