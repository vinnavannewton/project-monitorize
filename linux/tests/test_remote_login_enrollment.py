import json
import os
from pathlib import Path
import tempfile
import unittest
from monitorize.remote_login.enrollment import configure_next_boot,configure_disabled,NEW_UNITS,OLD_UNITS
from monitorize.remote_login.root_files import RootFiles


class Commands:
    def __init__(self,fail=False):self.states={name:True for name in OLD_UNITS};self.events=[];self.fail=fail
    def enabled(self,name):return self.states.get(name,False)
    def enable(self,name):
        self.events.append(('enable',name))
        if self.fail and name==NEW_UNITS[-1]:self.fail=False;raise RuntimeError('fixture')
        self.states[name]=True
    def disable(self,name):self.events.append(('disable',name));self.states[name]=False
    def reload(self):self.events.append(('reload',))


class EnrollmentTests(unittest.TestCase):
    values=dict(uid=1000,encoder='software',adapter='',schema_version=1,enabled=True)
    def test_enable_is_next_boot_only_and_boot_config_is_restricted(self):
        with tempfile.TemporaryDirectory() as d:
            commands=Commands();files=RootFiles(Path(d),os.getuid())
            configure_next_boot(self.values,'Same PC',[39,104],files=files,commands=commands)
            root=Path(d)
            boot=(root/'etc/monitorize/remote-login/boot.conf').read_text()
            self.assertIn('capture = kms',boot)
            self.assertIn('file_apps = /usr/share/monitorize/remote-login/apps.json',boot)
            self.assertIn('log_path = /var/lib/monitorize/remote-login/1000/logs/boot.log',boot)
            self.assertIn('stream_audio = disabled',boot)
            self.assertEqual(json.loads((root/'etc/monitorize/remote-login.json').read_text()),self.values)
            self.assertTrue(all(commands.states[name] for name in NEW_UNITS))
            self.assertFalse(any(commands.states[name] for name in OLD_UNITS))
            self.assertTrue(all(event[0] in ('enable','disable','reload') for event in commands.events))

    def test_partial_failure_restores_prior_configs_and_enablement(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);original=RootFiles(root,os.getuid())
            original.write('etc/monitorize/remote-login.json','original',0o600)
            commands=Commands(fail=True)
            with self.assertRaisesRegex(RuntimeError,'rollback'):
                configure_next_boot(self.values,'PC',[],files=RootFiles(root,os.getuid()),commands=commands)
            self.assertEqual((root/'etc/monitorize/remote-login.json').read_text(),'original')
            self.assertEqual((root/'etc/monitorize/remote-login.json').stat().st_mode & 0o777,0o600)
            self.assertFalse((root/'etc/monitorize/remote-login/boot.conf').exists())
            self.assertTrue(all(commands.states[name] for name in OLD_UNITS))
            self.assertFalse(any(commands.states[name] for name in NEW_UNITS))

    def test_disable_failure_restores_configuration_and_enablement_without_starting(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);files=RootFiles(root,os.getuid())
            files.write('etc/monitorize/remote-login.json','original',0o600)
            commands=Commands()
            commands.states={name:True for name in NEW_UNITS}
            original_disable=commands.disable
            def fail_once(name):
                if name==NEW_UNITS[-1] and not getattr(commands,'failed',False):
                    commands.failed=True;raise RuntimeError('fixture')
                original_disable(name)
            commands.disable=fail_once
            with self.assertRaisesRegex(RuntimeError,'rollback'):
                configure_disabled(dict(self.values,enabled=False),files=RootFiles(root,os.getuid()),commands=commands)
            self.assertEqual((root/'etc/monitorize/remote-login.json').read_text(),'original')
            self.assertTrue(all(commands.states[name] for name in NEW_UNITS))
            self.assertTrue(all(event[0] in ('enable','disable','reload') for event in commands.events))

    def test_disable_preserves_old_experiment_enablement(self):
        with tempfile.TemporaryDirectory() as d:
            commands=Commands();commands.states.update({name:True for name in NEW_UNITS})
            configure_disabled(dict(self.values,enabled=False),files=RootFiles(Path(d),os.getuid()),commands=commands)
            self.assertFalse(any(commands.states[name] for name in NEW_UNITS))
            self.assertTrue(all(commands.states[name] for name in OLD_UNITS))

class RetryIdentityTests(unittest.TestCase):
    def test_retry_uses_root_pending_record_and_preserves_retained_identity(self):
        from monitorize.remote_login.enrollment import provision_identity
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';source.mkdir();destination=root/'identity'
            record=RootFiles(root,os.getuid())
            def migrate(source,destination,uid):(destination / "identity").mkdir(parents=True)
            with patch('monitorize.remote_login.enrollment.migrate_identity',side_effect=migrate) as copy:
                provision_identity(source,destination,1000,files=record)
                provision_identity(source,destination,1000,files=RootFiles(root,os.getuid()))
                self.assertEqual(copy.call_count,1)
            with self.assertRaisesRegex(RuntimeError,'retained identity'):
                provision_identity(root/'other',destination,1000,files=RootFiles(root,os.getuid()))
    def test_unmarked_retained_directory_is_never_adopted(self):
        from monitorize.remote_login.enrollment import provision_identity
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);destination=root/'identity';destination.mkdir()
            with self.assertRaisesRegex(RuntimeError,'retained identity'):
                provision_identity(root/'source',destination,1000,files=RootFiles(root,os.getuid()))

    def test_rollback_only_discards_an_unmodified_new_identity(self):
        from monitorize.platform.remote_login_identity import identity_paths
        from monitorize.remote_login.enrollment import discard_unmodified_identity, provision_identity
        import json

        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                uid = os.getuid()
                files = RootFiles(root, uid)
                source = root / 'source'
                source.mkdir()
                (source / 'sunshine_state.json').write_text(json.dumps(
                    {'root': {'uniqueid': 'existing-host', 'named_devices': []}}))
                (source / 'cacert.pem').write_bytes(b'cert')
                (source / 'cakey.pem').write_bytes(b'key')
                destination = root / 'var/lib/monitorize/remote-login' / str(uid)
                destination.parent.mkdir(parents=True)
                provision_identity(source, destination, uid, files=files)
                if changed:
                    identity_paths(destination).state.write_text(json.dumps(
                        {'root': {'uniqueid': 'existing-host', 'named_devices': [{'uuid': 'new-client'}]}}))
                discard_unmodified_identity(uid, files=files)
                self.assertEqual(destination.exists(), changed)
                self.assertTrue((source / 'sunshine_state.json').exists())
