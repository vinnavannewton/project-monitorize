"""Policy checks for the desktop package release workflow."""

import json
import sys
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/desktop-release.yml"


class DesktopReleaseWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_release_tag_accepts_minor_and_patch_and_must_match_main(self):
        self.assertIn(r"^monitorize-v((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?)$", self.workflow)
        self.assertIn('git merge-base --is-ancestor "${tag_commit}" origin/main', self.workflow)
        self.assertIn("does not match pyproject.toml version", self.workflow)

    def test_tag_validation_examples(self):
        pattern = re.search(r'! "\$\{GITHUB_REF_NAME\}" =~ (.+) \]\]', self.workflow).group(1)
        for tag, accepted in (("monitorize-v0.33.2", True), ("monitorize-v0.34", True),
                              ("monitorize-v01.33.2", False), ("monitorize-v0.33.2.1", False)):
            result = subprocess.run(["bash", "-c", '[[ "$1" =~ ' + pattern + ' ]]', "test", tag])
            self.assertEqual(result.returncode == 0, accepted, tag)

    def test_publication_only_after_complete_verified_upload(self):
        script = textwrap.dedent(self.workflow.split("      - name: Stage draft, verify uploads, and publish release", 1)[1].split("        run: |\n", 1)[1])
        stub = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
command = sys.argv[2]
with Path('calls').open('a') as log:
    log.write(command + '\\n')
if command == 'create':
    assert '--draft' in sys.argv
elif command == 'upload' and os.environ['SCENARIO'] == 'upload-failure':
    sys.exit(1)
elif command == 'view':
    assets = [{'name': p.name, 'size': p.stat().st_size} for p in Path('release-assets').iterdir()]
    if os.environ['SCENARIO'] == 'missing':
        assets.pop()
    if os.environ['SCENARIO'] == 'wrong-size':
        assets[0]['size'] += 1
    print(json.dumps({'isDraft': True, 'assets': assets}))
elif command == 'edit':
    assert '--draft=false' in sys.argv
"""
        for scenario in ('success', 'upload-failure', 'missing', 'wrong-size'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'release-assets').mkdir()
                names = json.loads(subprocess.check_output(
                    [sys.executable, str(ROOT / "scripts/package-assets.py"), "manifest", "0.33.2"], text=True))
                for name in names:
                    (root / 'release-assets' / name).write_text('package')
                gh = root / 'gh'
                gh.write_text(stub)
                gh.chmod(0o755)
                result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True,
                                        env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                             'GITHUB_REF_NAME': 'monitorize-v0.33.2', 'VERSION': '0.33.2',
                                             'SCENARIO': scenario, 'EXPECTED_ASSETS': json.dumps(names)})
                calls = (root / 'calls').read_text().splitlines()
                self.assertEqual(result.returncode == 0, scenario == 'success', result.stderr)
                self.assertEqual('edit' in calls, scenario == 'success', calls)

    def test_only_enabled_cuda_targets_are_built(self):
        matrix = json.loads(subprocess.check_output(
            [sys.executable, str(ROOT / "scripts/package-assets.py"), "matrix"], text=True))
        self.assertEqual({leg["target"] for leg in matrix["include"]},
                         {"arch", "fedora-44", "tumbleweed", "ubuntu-24.04", "ubuntu-26.04"})
        self.assertIn("fromJSON(needs.validate.outputs.matrix)", self.workflow)
        self.assertIn("debian-trixie) ./packaging/deb/debian-trixie/build.sh", self.workflow)
        self.assertNotIn("--no-cuda", self.workflow)

    def test_asset_gate_rejects_missing_empty_and_substituted_packages(self):
        script = textwrap.dedent(self.workflow.split("      - name: Verify release asset set", 1)[1]
                                .split("        run: |\n", 1)[1].split("      - name: Stage draft", 1)[0])
        names = json.loads(subprocess.check_output(
            [sys.executable, str(ROOT / "scripts/package-assets.py"), "manifest", "0.33.2"], text=True))
        for scenario in ("success", "missing", "empty", "substituted"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                assets = Path(directory) / "release-assets"
                assets.mkdir()
                for name in names:
                    (assets / name).write_text("package")
                if scenario == "missing":
                    (assets / names[0]).unlink()
                elif scenario == "empty":
                    (assets / names[0]).write_text("")
                elif scenario == "substituted":
                    (assets / names[0]).rename(assets / "wrong-distro.rpm")
                result = subprocess.run(["bash", "-c", script], cwd=directory, capture_output=True,
                                        env={**os.environ, "EXPECTED_ASSETS": json.dumps(names)})
                self.assertEqual(result.returncode == 0, scenario == "success", result.stderr)

    def test_release_waits_for_builds_and_excludes_build_outputs(self):
        self.assertIn("needs: [validate, build]", self.workflow)
        self.assertIn("gh release create", self.workflow)
        self.assertNotIn("dist/**", self.workflow)
        self.assertNotIn("source/*.rpm", self.workflow)
        self.assertNotIn("build.log", self.workflow)


if __name__ == "__main__":
    unittest.main()
