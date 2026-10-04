"""Policy checks for the desktop package release workflow."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/desktop-release.yml"


class DesktopReleaseWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_release_tag_is_two_part_and_must_match_main(self):
        self.assertIn(r"^monitorize-v([0-9]+\.[0-9]+)$", self.workflow)
        self.assertIn('git merge-base --is-ancestor "${tag_commit}" origin/main', self.workflow)
        self.assertIn("does not match pyproject.toml version", self.workflow)

    def test_all_six_cuda_package_targets_are_built(self):
        for target in (
            "arch",
            "fedora-44",
            "tumbleweed",
            "debian-trixie",
            "ubuntu-24.04",
            "ubuntu-26.04",
        ):
            self.assertIn(f"target: {target}", self.workflow)
        self.assertNotIn("--no-cuda", self.workflow)
        self.assertIn("Expected six installable release assets", self.workflow)

    def test_release_waits_for_builds_and_excludes_build_outputs(self):
        self.assertIn("needs: [validate, build]", self.workflow)
        self.assertIn("gh release create", self.workflow)
        self.assertNotIn("dist/**", self.workflow)
        self.assertNotIn("source/*.rpm", self.workflow)
        self.assertNotIn("build.log", self.workflow)


if __name__ == "__main__":
    unittest.main()
