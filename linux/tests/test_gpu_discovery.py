import unittest
from pathlib import Path
from unittest.mock import patch

from monitorize.platform import gpu_discovery as gpu


class GpuDiscoveryTest(unittest.TestCase):
    def tearDown(self):
        gpu.discover_vaapi_h264_gpus.cache_clear()
        gpu.discover_nvidia_h264_gpus.cache_clear()

    def test_vaapi_lists_only_h264_high_encode_devices(self):
        with (
            patch.object(gpu, "_render_nodes_by_pci", return_value={
                "0000:03:00.0": "/dev/dri/renderD129",
                "0000:04:00.0": "/dev/dri/renderD130",
            }),
            patch.object(gpu, "_run", side_effect=[
                "Driver version: Mesa driver for AMD Radeon RX 7600\n"
                "VAProfileH264High : VAEntrypointEncSlice",
                "Driver version: decode-only\n"
                "VAProfileH264High : VAEntrypointVLD",
            ]),
        ):
            devices = gpu.discover_vaapi_h264_gpus()

        self.assertEqual([device["id"] for device in devices], ["0000:03:00.0"])
        self.assertEqual(devices[0]["render_node"], "/dev/dri/renderD129")

    def test_nvidia_filters_devices_without_an_encoder_engine(self):
        with (
            patch.object(gpu, "_render_nodes_by_pci", return_value={
                "0000:01:00.0": "/dev/dri/renderD128",
            }),
            patch.object(gpu, "_run", return_value=(
                "0, 00000000:01:00.0, NVIDIA RTX A, 0\n"
                "1, 00000000:02:00.0, NVIDIA Display Only, N/A\n"
            )),
        ):
            devices = gpu.discover_nvidia_h264_gpus()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["id"], "0000:01:00.0")
        self.assertEqual(devices[0]["cuda_index"], "0")

    def test_selector_is_hidden_for_one_gpu_and_includes_auto_for_two(self):
        one = ({"id": "0000:01:00.0", "label": "GPU 1"},)
        two = one + ({"id": "0000:02:00.0", "label": "GPU 2"},)
        with patch.object(gpu, "compatible_gpus", return_value=one):
            self.assertEqual(gpu.encoding_gpu_options("VA-API"), [])
        with patch.object(gpu, "compatible_gpus", return_value=two):
            options = gpu.encoding_gpu_options("VA-API")
        self.assertEqual(options[0]["id"], "")
        self.assertEqual([option["id"] for option in options[1:]], [
            "0000:01:00.0", "0000:02:00.0"
        ])

    def test_resolution_uses_stable_pci_id(self):
        device = {
            "id": "0000:03:00.0",
            "render_node": "/dev/dri/renderD131",
            "cuda_index": "",
        }
        with patch.object(gpu, "compatible_gpus", return_value=(device,)):
            self.assertIs(
                gpu.resolve_encoding_gpu("VA-API", "0000:03:00.0"), device
            )
            self.assertIsNone(
                gpu.resolve_encoding_gpu("VA-API", "../../bad"),
            )

    def test_qml_selectors_are_conditional_and_cover_both_displays(self):
        qml_dir = Path(__file__).parents[1] / "monitorize" / "qml"
        primary = (qml_dir / "DisplaySetupPage.qml").read_text()
        second = (qml_dir / "StreamingPage.qml").read_text()
        self.assertIn('text: "Encoding GPU"', primary)
        self.assertIn("visible: gpuOptions.length > 0", primary)
        self.assertIn("page.selectedGpuId()", primary)
        self.assertIn('text: "Encoding GPU"', second)
        self.assertIn("visible: secondGpuOptions.length > 0", second)
        self.assertIn("page.selectedSecondGpuId()", second)


if __name__ == "__main__":
    unittest.main()
