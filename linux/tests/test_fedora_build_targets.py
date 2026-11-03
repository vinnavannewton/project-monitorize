"""Exercise Fedora target routing and the enabled build queue without compiling."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class FedoraBuildTargetTests(unittest.TestCase):
    def test_target_preflight_and_wrappers_keep_variants_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rpm = root / 'packaging/rpm'
            rpm.mkdir(parents=True)
            # Execute production argument parsing, stopping before Git/Podman.
            preflight = (ROOT / 'packaging/rpm/build.sh').read_text().split('source_commit=', 1)[0]
            (rpm / 'build.sh').write_text(preflight + '''
printf '%s\\n' "$FEDORA_VERSION" "$IMAGE" "$output_root" "$enable_cuda" "$rebuild"
''')
            (rpm / 'build.sh').chmod(0o755)
            for version in ('44', '45'):
                (rpm / f'fedora-{version}').mkdir()
                shutil.copy2(ROOT / f'packaging/rpm/fedora-{version}/build.sh',
                             rpm / f'fedora-{version}/build.sh')
            cases = [
                ('build.sh', [], '45', False, False),
                ('build.sh', ['--target', 'fedora-44'], '44', False, False),
                ('fedora-44/build.sh', ['--no-cuda', '--rebuild-offline'], '44', True, True),
                ('fedora-45/build.sh', ['--no-cuda', '--rebuild-offline'], '45', True, True),
            ]
            for entry, arguments, version, no_cuda, offline in cases:
                with self.subTest(entry=entry, arguments=arguments):
                    result = subprocess.run([str(rpm / entry), *arguments], check=True,
                                            capture_output=True, text=True)
                    output = root / f'dist/rpm/fedora-{version}'
                    if no_cuda:
                        output /= 'no-cuda'
                    self.assertEqual(result.stdout.splitlines(), [
                        version, f'registry.fedoraproject.org/fedora:{version}',
                        str(output), '0' if no_cuda else '1', 'true' if offline else 'false',
                    ])

    def test_bad_targets_fail_before_git_or_podman(self):
        for arguments in (['--target'], ['--target', 'fedora-46'], ['--unknown']):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(ROOT / 'packaging/rpm/build.sh'), *arguments],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('Usage:', result.stderr)

    def test_queue_runs_fedora_45_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packaging').mkdir()
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'packaging/build-all.sh', root / 'packaging/build-all.sh')
            shutil.copy2(ROOT / 'scripts/package-assets.py', root / 'scripts/package-assets.py')
            preview = subprocess.check_output([str(root / 'packaging/build-all.sh'), '--list'],
                                              text=True).splitlines()
            targets = [line.split('\t')[0] for line in preview]
            self.assertEqual(targets, ['fedora-45', 'tumbleweed', 'arch', 'ubuntu-24.04', 'ubuntu-26.04'])
            for line in preview:
                target, script = line.split('\t')
                path = root / script
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'#!/usr/bin/env bash\necho {target} >> calls\nexit '
                                + ('1' if target == 'fedora-45' else '0') + '\n')
                path.chmod(0o755)
            result = subprocess.run([str(root / 'packaging/build-all.sh')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual((root / 'calls').read_text().splitlines(), targets)
            self.assertIn('FAIL  fedora-45 (exit 1)', result.stdout)


if __name__ == '__main__':
    unittest.main()
