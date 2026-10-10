"""Download names and local builder publication without real package builds."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/package-assets.py"


class PackageAssetTest(unittest.TestCase):
    def cli(self, *arguments):
        return subprocess.check_output([sys.executable, str(SCRIPT), *arguments], text=True).strip()

    def test_all_six_names_and_enabled_manifest(self):
        names = {
            "arch": "monitorize-0.33.2-archlinux-x86_64.pkg.tar.zst",
            "fedora-44": "monitorize-0.33.2-fedora-44-x86_64.rpm",
            "tumbleweed": "monitorize-0.33.2-opensuse-tumbleweed-x86_64.rpm",
            "debian-trixie": "monitorize-0.33.2-debian-13-amd64.deb",
            "ubuntu-24.04": "monitorize-0.33.2-ubuntu-24.04-amd64.deb",
            "ubuntu-26.04": "monitorize-0.33.2-ubuntu-26.04-amd64.deb",
        }
        for target, expected in names.items():
            with self.subTest(target=target):
                self.assertEqual(self.cli("name", target, "0.33.2"), expected)
                self.assertEqual(Path(self.cli("path", target, "0.33.2")).name, expected)
        manifest = json.loads(self.cli("manifest", "0.33.2"))
        self.assertEqual(set(manifest), set(names.values()) - {names["debian-trixie"]})

    def test_reenabling_debian_expands_matrix_and_manifest_together(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "package-assets.py"
            script.write_text(SCRIPT.read_text().replace("cd_enabled=False", "cd_enabled=True"))
            matrix = json.loads(subprocess.check_output([sys.executable, str(script), "matrix"], text=True))
            manifest = json.loads(subprocess.check_output([sys.executable, str(script), "manifest", "0.33.2"], text=True))
            self.assertEqual(len(matrix["include"]), 6)
            self.assertEqual(len(manifest), 6)
            self.assertIn("debian-trixie", {leg["target"] for leg in matrix["include"]})
            self.assertIn("monitorize-0.33.2-debian-13-amd64.deb", manifest)

    def test_local_publish_preserves_package_contents_and_other_rpm_outputs(self):
        for builder, target in (("arch", "arch"), ("rpm", "fedora-44"),
                                ("tumbleweed", "tumbleweed"), ("deb", "debian-trixie"),
                                ("deb", "ubuntu-24.04"), ("deb", "ubuntu-26.04")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "artifacts"
                output = root / "output"
                for base in (source, output):
                    for architecture in ("amd64", "x86_64"):
                        (base / architecture).mkdir(parents=True)
                (source / "build-manifest.txt").write_text("manifest")
                log = root / "build.log"
                log.write_text("log")
                raw = source / ("amd64" if builder == "deb" else "x86_64") / "native-package"
                raw.write_bytes(b"unchanged package metadata and payload")
                body = (ROOT / f"packaging/{builder}/build.sh").read_text()
                if builder == "arch":
                    body = body[body.index('asset_name="$(python3'):]
                else:
                    start = body.index('asset_name="$(python3')
                    end = body.index('cp "${build_log}"', start)
                    body = body[start:end]
                    if builder in ("rpm", "tumbleweed"):
                        # Include a native-named debug package in the real publication loop.
                        raw = raw.rename(raw.with_suffix('.rpm'))
                        (source / 'x86_64/monitorize-debuginfo.rpm').write_bytes(b'debug')
                        (source / 'source').mkdir()
                        (output / 'source').mkdir()
                        (source / 'source/native.src.rpm').write_bytes(b'source')
                environment = {**os.environ, "PROJECT_ROOT": str(ROOT), "OUTPUT_ROOT": str(output),
                               "artifact_root": str(source), "artifact_stage": str(source),
                               "main_package": str(raw), "main_rpm": str(raw), "main_deb": str(raw),
                               "version": "0.33.2", "target": target, "FEDORA_VERSION": "44",
                               "build_log": str(log)}
                subprocess.run(["bash", "-e", "-c", body], env=environment, check=True, capture_output=True)
                expected = output / ("amd64" if builder == "deb" else "x86_64") / self.cli("name", target, "0.33.2")
                self.assertEqual(expected.read_bytes(), raw.read_bytes())
                if builder in ("rpm", "tumbleweed"):
                    self.assertEqual((output / 'x86_64/monitorize-debuginfo.rpm').read_bytes(), b'debug')
                    self.assertEqual((output / 'source/native.src.rpm').read_bytes(), b'source')
