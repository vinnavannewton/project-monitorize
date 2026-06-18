import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "linux"))

from pipeline_builder import build_pipeline


class PipelineBuilderTest(unittest.TestCase):
    def pipeline(self, encoder=None, preserve=False):
        return build_pipeline(
            pw_fd=1,
            node_id=2,
            width=1280,
            height=800,
            fps=60,
            bitrate=8000,
            port=7110,
            hw_encoder=encoder,
            preserve_source_size=preserve,
        )

    def test_preserves_dynamic_dimensions_for_all_encoders(self):
        for encoder in (None, "vah264enc", "nvh264enc"):
            pipeline = self.pipeline(encoder, preserve=True)
            self.assertNotIn("width=1280", pipeline)
            self.assertNotIn("height=800", pipeline)

    def test_fixed_dimensions_remain_default(self):
        for encoder in (None, "vah264enc", "nvh264enc"):
            pipeline = self.pipeline(encoder)
            self.assertIn("width=1280", pipeline)
            self.assertIn("height=800", pipeline)


if __name__ == "__main__":
    unittest.main()
