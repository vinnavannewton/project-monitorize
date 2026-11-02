"""Development archives must include reviewed changes without staging/committing."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2]/'scripts/native-test-source.py'
spec = importlib.util.spec_from_file_location('native_test_source', SCRIPT)
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class NativeTestSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        (self.root/'tracked.py').write_text('original\n')
        (self.root/'.gitignore').write_text('credentials\n')
        self.git('add', '.')
        self.git('-c', 'user.name=vinnavannewton', '-c', 'user.email=vinnavannewton@proton.me',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Test fixture')

    def git(self, *args):
        return source.git(self.root, *args)

    def test_capture_preserves_index_head_and_only_explicit_untracked_files(self):
        (self.root/'tracked.py').write_text('staged\n')
        self.git('add', 'tracked.py')
        (self.root/'tracked.py').write_text('final working content\n')
        (self.root/'new module.py').write_text('new module\n')
        (self.root/'unreviewed.py').write_text('not selected\n')
        (self.root/'credentials').write_text('never archive this\n')
        index = (self.root/'.git/index').read_bytes()
        head = self.git('rev-parse', 'HEAD')
        result = source.capture(self.root, ['new module.py'])
        self.assertEqual((self.root/'.git/index').read_bytes(), index)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('show', result['tree']+':tracked.py'), b'final working content\n')
        self.assertIn('new module.py', result['files'])
        self.assertNotIn('unreviewed.py', result['files'])
        self.assertNotIn('credentials', result['files'])

    def test_validation_rejects_changed_bytes_permissions_and_types(self):
        result = source.capture(self.root, [])
        path = self.root/'tracked.py'
        path.write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'changed since capture'):
            source.validate(self.root, result['tree'])
        path.write_text('original\n');path.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'permissions changed'):
            source.validate(self.root, result['tree'])
        path.unlink();path.symlink_to('.gitignore')
        with self.assertRaises(ValueError):
            source.validate(self.root, result['tree'])

    def test_deletion_is_captured_without_staging_it_in_the_real_index(self):
        (self.root/'tracked.py').unlink()
        original = (self.root/'.git/index').read_bytes()
        result = source.capture(self.root, [])
        self.assertNotIn('tracked.py', result['files'])
        self.assertEqual((self.root/'.git/index').read_bytes(), original)

    def test_symlink_is_captured_as_a_link(self):
        (self.root/'link').symlink_to('tracked.py')
        result = source.capture(self.root, ['link'])
        self.assertEqual(result['files']['link']['mode'], '120000')
        self.assertEqual(self.git('show', result['tree']+':link'), b'tracked.py')

    def test_ignored_files_directories_and_path_traversal_are_rejected(self):
        (self.root/'credentials').write_text('not for packaging')
        for name in ('credentials', '.', '../outside', str(self.root/'tracked.py')):
            with self.assertRaises(ValueError):
                source.capture(self.root, [name])

    def test_ref_arguments_and_commit_objects_are_not_accepted_as_trees(self):
        for identity in ('HEAD', '--help', self.git('rev-parse', 'HEAD').decode().strip()):
            with self.assertRaises(ValueError):
                source.validate(self.root, identity)

    def test_filename_pathspec_magic_is_literal(self):
        name = ':(glob)tracked*'
        (self.root/name).write_text('literal filename\n')
        result = source.capture(self.root, [name])
        self.assertIn(name, result['files'])


if __name__ == '__main__':
    unittest.main()
