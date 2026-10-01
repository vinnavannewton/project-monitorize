"""Check generated build commands for the optional Sunshine CUDA variant."""

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class NoCudaPackagingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("make"), "make is unavailable")
    def test_deb_rules_omit_cuda_compiler_in_no_cuda_mode(self):
        command = [
            "make", "-n", "-f", "packaging/deb/common/rules.mk", "override_dh_auto_build",
            "MONITORIZE_DEB_TARGET=ubuntu-24.04", "MONITORIZE_CUDA_ROOT=/work/cuda",
        ]
        no_cuda = subprocess.run(
            [*command, "MONITORIZE_ENABLE_CUDA=0"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout
        cuda = subprocess.run(
            [*command, "MONITORIZE_ENABLE_CUDA=1"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertIn("-DSUNSHINE_ENABLE_CUDA=OFF", no_cuda)
        self.assertNotIn("CMAKE_CUDA_COMPILER", no_cuda)
        self.assertIn("-DSUNSHINE_ENABLE_CUDA=ON", cuda)
        self.assertIn("CMAKE_CUDA_COMPILER=/work/cuda/bin/nvcc", cuda)

    @unittest.skipUnless(shutil.which("rpmspec"), "rpmspec is unavailable")
    def test_rpm_specs_exclude_cuda_build_inputs_in_no_cuda_mode(self):
        for distro in ("rpm", "tumbleweed"):
            with self.subTest(distro=distro):
                spec = ROOT / "packaging" / distro / "monitorize.spec"
                requirements = subprocess.run(
                    ["rpmspec", "--without", "cuda", "-q", "--buildrequires", str(spec)],
                    check=True, capture_output=True, text=True,
                ).stdout.splitlines()
                self.assertNotIn("aria2", requirements)
                self.assertFalse(any(name.startswith(("gcc14", "gcc15")) for name in requirements))
                expanded = subprocess.run(
                    ["rpmspec", "--without", "cuda", "-P", str(spec)],
                    check=True, capture_output=True, text=True,
                ).stdout
                self.assertIn("-DSUNSHINE_ENABLE_CUDA=OFF", expanded)
                self.assertNotIn("cuda_archive=", expanded)
                if distro == "tumbleweed":
                    self.assertNotIn("Source3:", expanded)


if __name__ == "__main__":
    unittest.main()
