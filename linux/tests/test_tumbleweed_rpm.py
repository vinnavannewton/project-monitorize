import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TumbleweedRpmPackagingTest(unittest.TestCase):
    def test_tumbleweed_rpm_keeps_sunshine_private_and_matches_project_version(self):
        spec = (ROOT / "packaging/tumbleweed/monitorize.spec").read_text()
        builder = (ROOT / "packaging/tumbleweed/build.sh").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()

        project_version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
        spec_version = re.search(r'^Version:\s+(\S+)', spec, re.MULTILINE).group(1)
        self.assertEqual(project_version, spec_version)
        self.assertIn("registry.opensuse.org/opensuse/tumbleweed:latest", builder)
        self.assertIn("zypper --non-interactive install", builder)
        self.assertIn("rpmbuild -ba", builder)
        self.assertIn("Smoke-testing", builder)
        self.assertIn("install --allow-unsigned-rpm --no-recommends /tmp/monitorize.rpm", builder)
        self.assertIn("%sysusers_create_package", spec)
        self.assertIn("libboost_filesystem-devel", spec)
        self.assertIn("shaderc", spec)
        self.assertIn("pulseaudio-devel", spec)
        self.assertIn("%global _firewalld_dir %{_prefix}/lib/firewalld", spec)
        self.assertIn("%{_libexecdir}/monitorize/sunshine", spec)
        self.assertIn("MONITORIZE_SUNSHINE_BIN", spec)
        self.assertIn("MONITORIZE_SUNSHINE_ASSETS_DIR", spec)
        self.assertIn("%{_modulesloaddir}/monitorize.conf", spec)
        self.assertNotIn("cmake --install", spec)
        self.assertNotRegex(spec, r"install .*%\{_bindir\}/sunshine")
        self.assertNotRegex(spec, r"install .*?/usr/local")


if __name__ == "__main__":
    unittest.main()
