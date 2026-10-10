"""Check packaged Sunshine patches against the source revisions they target."""
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUNSHINE = ROOT / "external/sunshine"


class FlatpakPatchSequenceTests(unittest.TestCase):
    def test_manifest_patch_sequence_applies_to_pinned_source(self):
        manifest = (ROOT / "packaging/flatpak/com.vinnavan.Monitorize.yml").read_text()
        section = manifest.split("url: https://github.com/vinnavannewton/monitorize-sunshine.git", 1)[1]
        section = section.split("  - name: monitorize", 1)[0]
        revision = re.search(r"commit: ([0-9a-f]{40})", section).group(1)
        patches = re.findall(r"path: (.+\.patch)", section)
        self.assertIn("sunshine-flatpak-strict-selection.patch", patches)
        with tempfile.TemporaryDirectory() as directory:
            archive = subprocess.run(
                ["git", "-C", str(SUNSHINE), "archive", revision],
                check=True, capture_output=True,
            )
            archive_path = Path(directory) / "source.tar"
            archive_path.write_bytes(archive.stdout)
            with tarfile.open(archive_path) as source:
                source.extractall(directory, filter="data")
            original = (Path(directory) / "src/rtsp.cpp").read_text()
            self.assertNotIn("MONITORIZE_STRICT_CODEC_REJECTED", original)
            for patch in patches:
                result = subprocess.run(
                    ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i",
                     str((ROOT / "packaging/flatpak" / patch).resolve())],
                    cwd=directory, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, f"{patch}: {result.stdout}{result.stderr}")
            rtsp = (Path(directory) / "src/rtsp.cpp").read_text()
            native_dir = Path(directory) / "native"
            (native_dir / "src").mkdir(parents=True)
            for name in ("rtsp.cpp", "nvhttp.cpp"):
                source = subprocess.run(
                    ["git", "-C", str(SUNSHINE), "show", f"HEAD:src/{name}"],
                    check=True, capture_output=True,
                )
                (native_dir / "src" / name).write_bytes(source.stdout)
            result = subprocess.run(
                ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i",
                 str(ROOT / "packaging/sunshine-strict-selection.patch")],
                cwd=native_dir, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            native = (native_dir / "src/rtsp.cpp").read_text()
            # Both patched revisions must enforce the same codec rejection.
            def policy(text):
                start = text.index("    const bool force_h264")
                end = text.index("    if (config.monitor.videoFormat == 1", start)
                return text[start:end]
            self.assertEqual(policy(rtsp), policy(native))
            nvhttp = (Path(directory) / "src/nvhttp.cpp").read_text()
            self.assertIn("force_non_h264 ? 0 : SCM_H264", nvhttp)
            self.assertIn("!force_non_h264 && video::last_encoder_probe_supported_yuv444_for_codec[0]", nvhttp)


if __name__ == "__main__":
    unittest.main()
