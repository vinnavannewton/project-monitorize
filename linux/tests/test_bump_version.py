"""Integration tests for the release metadata synchronizer."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "pyproject.toml",
    "packaging/arch/PKGBUILD",
    "nix/package.nix",
    "packaging/rpm/monitorize.spec",
    "packaging/tumbleweed/monitorize.spec",
    "packaging/tumbleweed/monitorize.changes",
    *(f"packaging/deb/{target}/debian/changelog" for target in
      ("debian-trixie", "ubuntu-24.04", "ubuntu-26.04")),
]
MAINTAINER = "Monitorize contributors <noreply@example.com>"


class BumpVersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in FILES:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative == "pyproject.toml":
                content = '[project]\nname = "monitorize"\nversion = "0.33"\n'
            elif relative.endswith("PKGBUILD"):
                content = "pkgname=monitorize\npkgver=0.33\npkgrel=1\n_boost_version=1.89.0\nBUILD_VERSION=0.0.0\n"
            elif relative == "nix/package.nix":
                content = ('sunshineVersion = "0-unstable-2026-10-03";\n'
                           'version = sunshineVersion;\n'
                           'version = "0.33";\n')
            elif relative.endswith("monitorize.spec"):
                distro = "rpm" if "/rpm/" in relative else "tumbleweed"
                release = "1%{?dist}" if distro == "rpm" else "0"
                number = "1" if distro == "rpm" else "0"
                content = (
                    "%global cuda_version 13.1.1\n%global sunshine_commit abcdef\n"
                    "%global sunshine_ffmpeg_tag v2026.1\n"
                    f"Name: monitorize\nVersion: 0.33\nRelease: {release}\n\n"
                    f"%changelog\n* Tue Sep 29 2026 {MAINTAINER} - 0.33-{number}\n"
                    "- Previous release.\n\n"
                    f"* Mon Sep 21 2026 {MAINTAINER} - 0.32-{number}\n"
                    "- Older release.\n"
                )
            elif relative.endswith("monitorize.changes"):
                content = (f"-------------------------------------------------------------------\n"
                           f"Tue Sep 29 2026 {MAINTAINER}\n\n"
                           "- Release Monitorize 0.33.\n\n"
                           "-------------------------------------------------------------------\n"
                           "- Older release.\n")
            else:
                codename = ("trixie" if "debian-trixie" in relative else
                            "noble" if "ubuntu-24.04" in relative else "resolute")
                content = (f"monitorize (0.33) {codename}; urgency=medium\n\n"
                           "  * Previous release.\n\n"
                           f" -- {MAINTAINER}  Tue, 29 Sep 2026 17:26:19 +0530\n")
            destination.write_text(content)
        destination = self.root / "scripts/bump-version.py"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts/bump-version.py", destination)

    def read(self, relative):
        return (self.root / relative).read_text()

    def write(self, relative, content):
        (self.root / relative).write_text(content)

    def bump(self, version):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/bump-version.py"), version],
            cwd=self.root, text=True, capture_output=True, check=False,
        )

    def test_patch_then_minor_release_preserves_history_and_dependencies(self):
        before = {relative: self.read(relative) for relative in FILES}
        self.write("packaging/arch/PKGBUILD", before["packaging/arch/PKGBUILD"].replace("pkgrel=1", "pkgrel=7", 1))
        self.write("packaging/rpm/monitorize.spec", before["packaging/rpm/monitorize.spec"].replace("Release: 1%{?dist}", "Release: 4%{?dist}", 1))

        result = subprocess.run(
            [str(self.root / "scripts/bump-version.py"), "0.33.1"],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  nix/package.nix\n", result.stdout)
        self.assertIn('version = "0.33.1"', self.read("pyproject.toml"))
        arch = self.read("packaging/arch/PKGBUILD")
        self.assertRegex(arch, r"(?m)^pkgver=0\.33\.1$")
        self.assertRegex(arch, r"(?m)^pkgrel=1$")
        self.assertIn("_boost_version=1.89.0", arch)
        self.assertIn("BUILD_VERSION=0.0.0", arch)
        nix = self.read("nix/package.nix")
        self.assertIn('version = "0.33.1";', nix)
        self.assertIn('sunshineVersion = "0-unstable-2026-10-03";', nix)
        for distro, release in (("rpm", "1%{?dist}"), ("tumbleweed", "0")):
            relative = f"packaging/{distro}/monitorize.spec"
            spec = self.read(relative)
            self.assertRegex(spec, r"(?m)^Version:\s+0\.33\.1$")
            self.assertRegex(spec, rf"(?m)^Release:\s+{re.escape(release)}$")
            self.assertIn(f"- 0.33.1-{'1' if distro == 'rpm' else '0'}\n- Release Monitorize 0.33.1.", spec)
            self.assertTrue(spec.endswith(before[relative].split("%changelog\n", 1)[1]))
            for dependency in ("%global cuda_version", "%global sunshine_commit", "%global sunshine_ffmpeg_tag"):
                old_line = next(line for line in before[relative].splitlines() if line.startswith(dependency))
                self.assertIn(old_line, spec)
        for relative in FILES[5:]:
            self.assertTrue(self.read(relative).endswith(before[relative]))
            self.assertIn("Release Monitorize 0.33.1.", self.read(relative))
        for target, codename in (("debian-trixie", "trixie"), ("ubuntu-24.04", "noble"), ("ubuntu-26.04", "resolute")):
            self.assertTrue(self.read(f"packaging/deb/{target}/debian/changelog").startswith(f"monitorize (0.33.1) {codename};"))

        result = self.bump("0.33.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "0.33.2"', self.read("pyproject.toml"))
        for relative in FILES:
            self.assertIn("0.33.2", self.read(relative))

        result = self.bump("0.34")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "0.34"', self.read("pyproject.toml"))
        self.assertIn("pkgver=0.34\n", self.read("packaging/arch/PKGBUILD"))
        self.assertIn('version = "0.34";', self.read("nix/package.nix"))
        for relative in FILES[5:]:
            self.assertTrue(self.read(relative).endswith(before[relative]))
            self.assertIn("Release Monitorize 0.33.1.", self.read(relative))

        result = self.bump("1.0.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "1.0.1"', self.read("pyproject.toml"))
        self.assertIn('version = "1.0.1";', self.read("nix/package.nix"))

    def test_inconsistent_nix_version_aborts_without_writes(self):
        relative = "nix/package.nix"
        self.write(relative, self.read(relative).replace('version = "0.33";', 'version = "0.32";'))
        before = {name: self.read(name) for name in FILES}
        result = self.bump("0.33.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nix/package.nix has version=0.32; expected 0.33", result.stderr)
        self.assertEqual(before, {name: self.read(name) for name in FILES})

    def test_invalid_versions_leave_every_file_unchanged(self):
        before = {relative: self.read(relative) for relative in FILES}
        for version in ("foo", "v0.34", "0..34", "0.34-", "0.33", "0.34.1.2"):
            with self.subTest(version=version):
                self.assertNotEqual(self.bump(version).returncode, 0)
                self.assertEqual(before, {relative: self.read(relative) for relative in FILES})

    def test_inconsistent_starting_version_aborts_without_writes(self):
        relative = "packaging/arch/PKGBUILD"
        self.write(relative, self.read(relative).replace("pkgver=0.33", "pkgver=0.32", 1))
        before = {name: self.read(name) for name in FILES}
        result = self.bump("0.33.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pkgver=0.32; expected 0.33", result.stderr)
        self.assertEqual(before, {name: self.read(name) for name in FILES})

    def test_missing_late_field_aborts_without_writes(self):
        relative = "packaging/deb/ubuntu-26.04/debian/changelog"
        self.write(relative, self.read(relative).replace("monitorize (0.33)", "broken (0.33)", 1))
        before = {name: self.read(name) for name in FILES}
        result = self.bump("0.33.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(relative, result.stderr)
        self.assertEqual(before, {name: self.read(name) for name in FILES})


if __name__ == "__main__":
    unittest.main()
