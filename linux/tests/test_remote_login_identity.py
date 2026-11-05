import json
import os
from pathlib import Path
import tempfile
import unittest

from monitorize.platform.remote_login_identity import migrate_identity, acquire_identity_lock, identity_paths


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir(mode=0o700)
        self.state={'root':{'uniqueid':'host-id','named_devices':[{'uuid':'client'}],'future':42}}
        for name,data in [('sunshine_state.json',json.dumps(self.state).encode()),('cacert.pem',b'cert'),('cakey.pem',b'key')]:
            p=self.source/name;p.write_bytes(data);p.chmod(0o600)
        self.destination=self.root/'managed'

    def test_migration_preserves_bytes_and_source(self):
        result=migrate_identity(self.source,self.destination,uid=os.getuid())
        self.assertTrue(result['changed'])
        for name in ('sunshine_state.json','cacert.pem','cakey.pem'):
            self.assertEqual((self.source/name).read_bytes(),(self.destination/'identity'/name).read_bytes())
            self.assertEqual((self.destination/'identity'/name).stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads((self.destination/'identity/sunshine_state.json').read_text()),self.state)
        self.assertEqual((self.destination/'identity').stat().st_mode & 0o777,0o700)

    def test_no_overwrite_or_partial_destination(self):
        (self.source/'cakey.pem').unlink()
        with self.assertRaises(ValueError):migrate_identity(self.source,self.destination,uid=os.getuid())
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.managed.*')),[])

    def test_reject_symlink_and_untrusted_permissions(self):
        key=self.source/'cakey.pem';key.unlink();key.symlink_to(self.source/'cacert.pem')
        with self.assertRaises(ValueError):migrate_identity(self.source,self.destination,uid=os.getuid())
        key.unlink();key.write_bytes(b'key');key.chmod(0o666)
        with self.assertRaises(ValueError):migrate_identity(self.source,self.destination,uid=os.getuid())

    def test_normal_profile_credentials_subdirectory_and_public_directory(self):
        self.source.chmod(0o755)
        credentials=self.source/'credentials';credentials.mkdir(mode=0o755)
        for name in ('cacert.pem','cakey.pem'):
            (self.source/name).rename(credentials/name)
        migrate_identity(self.source,self.destination,uid=os.getuid())
        self.assertEqual((self.destination/'identity/cakey.pem').read_bytes(),b'key')

    def test_existing_destination_never_overwritten(self):
        migrate_identity(self.source,self.destination,uid=os.getuid())
        with self.assertRaises(FileExistsError):migrate_identity(self.source,self.destination,uid=os.getuid())

    def test_full_lifetime_lock_excludes_second_owner(self):
        migrate_identity(self.source,self.destination,uid=os.getuid())
        paths=identity_paths(self.destination)
        with acquire_identity_lock(paths,uid=os.getuid()):
            with self.assertRaises(BlockingIOError):
                with acquire_identity_lock(paths,uid=os.getuid()):pass
        with acquire_identity_lock(paths,uid=os.getuid()):pass

    def test_migration_refuses_running_source(self):
        # All future managed launches use the same profile lock during migration.
        lock=self.source/'engine.lock'
        with acquire_identity_lock(identity_paths(self.source,flat=True),uid=os.getuid()):
            with self.assertRaises(BlockingIOError):migrate_identity(self.source,self.destination,uid=os.getuid())
        self.assertFalse(self.destination.exists())

    def test_lock_refuses_symlink(self):
        (self.source/'engine.lock').symlink_to(self.source/'cakey.pem')
        with self.assertRaises(OSError):
            with acquire_identity_lock(identity_paths(self.source,flat=True),uid=os.getuid()):pass

    def test_fresh_identity_can_be_created_without_a_paired_client(self):
        import shutil
        from unittest.mock import patch

        if not shutil.which('openssl'):
            self.skipTest('OpenSSL is required to exercise TLS identity generation')
        for path in self.source.iterdir():
            path.unlink()
        with patch('monitorize.remote_login.daemon.require_installed'):
            migrate_identity(self.source, self.destination, uid=os.getuid())
        paths = identity_paths(self.destination)
        state = json.loads(paths.state.read_text())
        self.assertEqual(state['root']['named_devices'], [])
        self.assertTrue(state['root']['uniqueid'])
        self.assertIn(b'BEGIN CERTIFICATE', paths.cert.read_bytes())
        self.assertEqual(paths.key.stat().st_mode & 0o777, 0o600)

    def test_credentials_only_state_preserves_web_login_and_existing_tls(self):
        from unittest.mock import patch

        credentials = {'username': 'local', 'salt': 'salt', 'password': 'hash'}
        (self.source / 'sunshine_state.json').write_text(json.dumps(credentials))
        with patch('monitorize.platform.remote_login_identity._openssl', return_value=b'matching public key'):
            migrate_identity(self.source, self.destination, uid=os.getuid())
        paths = identity_paths(self.destination)
        state = json.loads(paths.state.read_text())
        self.assertEqual({key: state[key] for key in credentials}, credentials)
        self.assertEqual(state['root']['named_devices'], [])
        self.assertEqual(paths.cert.read_bytes(), b'cert')
        self.assertEqual(paths.key.read_bytes(), b'key')
