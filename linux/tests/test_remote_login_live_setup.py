"""Root live setup orchestration with a fake backend and private temporary files."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import Mock, patch

from monitorize.remote_login import live_setup as live


class Backend:
    boot_id = 'boot-a'
    def __init__(self): self.events=[]; self.fail=''; self.session='3'; self.lease=None
    def preflight(self, uid): self.events.append('preflight')
    def context(self, uid): return self.session
    def existing(self, uid, session): return False
    def snapshot(self, uid, session): return dict(units={},caps='',masks=[],lease=None)
    def configure(self, uid, journal):
        self.events.append('configure')
        live.TransactionFiles(journal).write('etc/monitorize/remote-login.json','new')
        if self.fail=='configure': raise RuntimeError('configuration failure')
    def reserve(self, uid, session, generation):
        self.events.append('reserve')
        self.lease=dict(uid=uid,session=session,generation=generation)
    def activate(self, uid, journal):
        self.events.append('start')
        if self.fail=='start': raise RuntimeError('service start failed')
    def verify(self, uid, session):
        if self.session != session: raise PermissionError('session changed')
    def rollback(self, uid, journal):
        self.events.append('rollback')
        if self.fail=='rollback': raise RuntimeError('capture did not exit')
        self.lease=None


class LiveSetupTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.backend=Backend()
    def run_setup(self):
        return live.LiveEnrollment(self.backend,root=self.root,owner_uid=os.getuid()).enable(1000)
    def test_live_enable_starts_fixed_units_without_display_manager_restart(self):
        result=self.run_setup()
        self.assertTrue(result['success']);self.assertTrue(result['configured'])
        self.assertFalse(result['activated']);self.assertFalse(result['reboot_required'])
        self.assertEqual(self.backend.events,['preflight','configure','reserve','start'])
        receipt=live.SetupReceipt(1000,root=self.root,owner_uid=os.getuid()).load()
        self.assertEqual(receipt['stage'],'waiting_for_controller')
        self.assertEqual(receipt['session'],'3')
    def test_service_start_failure_restores_prior_config(self):
        path=self.root/'etc/monitorize/remote-login.json';path.parent.mkdir(parents=True);path.write_text('old');path.chmod(0o600)
        self.backend.fail='start';result=self.run_setup()
        self.assertFalse(result['success']);self.assertFalse(result['cleanup_pending'])
        self.assertEqual(path.read_text(),'old');self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertIsNone(self.backend.lease)
    def test_interrupted_setup_resumes_same_generation(self):
        first=self.run_setup();generation=self.backend.lease['generation']
        self.backend.events.clear()
        self.run_setup()
        self.assertEqual(self.backend.lease['generation'],generation)
        self.assertEqual(self.backend.events,['preflight','configure','reserve','start'])
    def test_repeated_enable_reuses_owned_listener_without_reconfiguration(self):
        self.backend.existing=lambda *args:True
        result=self.run_setup()
        self.assertTrue(result['success']);self.assertEqual(self.backend.events,['preflight'])
    def test_healthy_retry_reconciles_interrupted_start_receipt(self):
        self.run_setup()
        receipt=live.SetupReceipt(1000,root=self.root,owner_uid=os.getuid())
        value=receipt.load();value['stage']='starting';receipt.save(value)
        self.backend.existing=lambda *args:True
        self.backend.verify_receipt=lambda uid,session,generation:self.assertEqual(generation,self.backend.lease['generation'])
        self.assertTrue(self.run_setup()['reused'])
        self.assertEqual(receipt.load()['stage'],'waiting_for_controller')
        # A verified healthy next-boot service supersedes an old interrupted receipt.
        self.backend.boot_id='boot-b'
        self.assertTrue(self.run_setup()['success'])
        self.assertEqual(receipt.load()['stage'],'complete')
        self.backend.existing=lambda *args:False
        self.assertTrue(self.run_setup()['success'])
    def test_failed_retry_reports_restored_enabled_enrollment(self):
        self.backend.snapshot=lambda *args:dict(units={},caps='',masks=[],lease=None,enrolled=True)
        self.backend.fail='start'
        result=self.run_setup()
        self.assertFalse(result['success']);self.assertTrue(result['configured'])
        self.assertFalse(result['partial'])

    def test_verified_masks_are_unmasked_before_enable_and_remasked_after_rollback(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        backend=object.__new__(live.InstalledBackend)
        masked={'monitorize-remote-login.service'};events=[]
        def run(args):
            if args[1]=='unmask':masked.difference_update(args[3:]);events.append('unmask')
            if args[1]=='mask':masked.update(args[3:]);events.append('mask')
        def enable(name):
            if name in masked:raise RuntimeError('Unit is masked')
            events.append('enable')
        backend.commands=SimpleNamespace(run=run,enable=enable,disable=Mock())
        backend.host=Mock();backend.host.boot_output_status.return_value=None
        backend.helper=Mock();backend.runtime=Mock()
        journal=SimpleNamespace(value=dict(stage='preparing',system=dict(active={},units={'monitorize-remote-login.service':True},
            caps='',masks=list(masked),lease=None)))
        with patch('monitorize.remote_login.enrollment.enable',side_effect=lambda *args,**kwargs:enable('monitorize-remote-login.service')), \
             patch.object(live,'TransactionFiles'):
            backend.configure(1000,journal)
        self.assertEqual(events,['unmask','enable'])
        # Retry rollback after an interrupted attempt which already restored its masks.
        masked.add('monitorize-remote-login.service');events.clear()
        backend.rollback(1000,journal)
        self.assertEqual(events,['unmask','enable','mask'])
        self.assertEqual(masked,{'monitorize-remote-login.service'})
    def test_old_boot_receipt_is_not_replayed(self):
        self.run_setup();self.backend.boot_id='boot-b';self.backend.fail='configure'
        result=self.run_setup()
        self.assertFalse(result['success'])
        self.assertTrue(result['cleanup_pending'])
        self.assertEqual((self.root/'etc/monitorize/remote-login.json').read_text(),'new')
    def test_cleanup_failure_retains_receipt_and_config(self):
        self.backend.fail='rollback'
        self.backend.activate=lambda *args: (_ for _ in ()).throw(RuntimeError('start failed'))
        result=self.run_setup()
        self.assertTrue(result['cleanup_pending']);self.assertTrue(result['partial'])
        self.assertEqual((self.root/'etc/monitorize/remote-login.json').read_text(),'new')
    def test_setup_lock_rejects_parallel_setup(self):
        with live.setup_lock(root=self.root,owner_uid=os.getuid()):
            with self.assertRaises(BlockingIOError):
                with live.setup_lock(root=self.root,owner_uid=os.getuid()): pass
    def test_receipt_rejects_unsafe_paths_and_writable_files(self):
        self.run_setup();receipt=live.SetupReceipt(1000,root=self.root,owner_uid=os.getuid())
        data=receipt.load();data['files']['etc/passwd']=None
        with self.assertRaises(ValueError): receipt.save(data)
        receipt.path.chmod(0o666)
        with self.assertRaises(ValueError): receipt.load()

    def test_receipt_rejects_arbitrary_system_units(self):
        self.run_setup();receipt=live.SetupReceipt(1000,root=self.root,owner_uid=os.getuid())
        data=receipt.load();data['system']['units']['display-manager.service']=True
        with self.assertRaises(ValueError):receipt.save(data)

    def test_configure_failure_before_reservation_needs_no_host_release(self):
        backend=object.__new__(live.InstalledBackend)
        backend.commands=Mock();backend.runtime=Mock();backend.host=Mock();backend.helper=Mock()
        backend.host.boot_output_status.return_value=None
        journal=Mock();journal.value=dict(stage='preparing',session='3',generation='a'*32,
            system=dict(active={},units={},caps='',masks=[],lease=None))
        backend.rollback(1000,journal)
        backend.host.release_live_desktop_installed.assert_not_called()
        backend.runtime.verify_quiescent.assert_not_called()

    def test_owned_mask_requires_original_enablement_and_rejects_custom_masks(self):
        import json
        from unittest.mock import patch
        from types import SimpleNamespace
        names=['monitorize-remote-login.service', 'monitorize-remote-login-boot@1000.service',
               'monitorize-remote-login-desktop@1000.service']
        backend=object.__new__(live.InstalledBackend);backend.boot_id='boot-a';backend.commands=Mock()
        backend.commands.run.return_value='masked'
        grant=dict(schema_version=1,uid=1000,session='3',boot_id='boot-a',owned_runtime_masks=names,
                   unit_enablement={name:name==names[0] for name in names})
        target=Mock();target.lstat.return_value=SimpleNamespace(st_mode=0o120777,st_uid=0)
        path=Mock();path.__truediv__=Mock(return_value=target)
        with patch.object(live,'RootFiles') as files,patch.object(live,'Path',return_value=path), \
             patch.object(live.os,'readlink',return_value='/dev/null'):
            files.return_value._read.side_effect=lambda *args:(json.dumps(grant).encode(),0o644)
            self.assertEqual(backend.fallback_masks(1000,'3'),names)
            self.assertTrue(backend._fallback_enablement[names[0]])
            grant['owned_runtime_masks']=names[1:]
            with self.assertRaises(PermissionError):backend.fallback_masks(1000,'3')
            grant['owned_runtime_masks']=names;del grant['unit_enablement']
            with self.assertRaises(PermissionError):backend.fallback_masks(1000,'3')

    def test_verified_listener_completes_receipt_left_at_starting(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        self.run_setup();receipt=live.SetupReceipt(1000,root=self.root,owner_uid=os.getuid())
        value=receipt.load();value['stage']='starting';receipt.save(value)
        backend=Mock();backend.context.return_value='3'
        backend.host.boot_output_status.return_value={**self.backend.lease,'boot_id':'boot-a'}
        path=Mock();path.read_text.return_value='boot-a';path.__truediv__=Mock(return_value=path);path.exists.return_value=True
        with patch.object(live,'Path',return_value=path),patch.object(live,'SetupReceipt',return_value=receipt), \
             patch.object(live,'setup_lock',return_value=nullcontext()),patch.object(live,'InstalledBackend',return_value=backend):
            live.complete_for_session(1000,'3')
        self.assertEqual(receipt.load()['stage'],'complete')


class InstalledRecoveryTests(unittest.TestCase):
    def test_owned_active_supervisor_can_be_snapshotted_without_manual_stop(self):
        from types import SimpleNamespace
        backend=object.__new__(live.InstalledBackend)
        backend.boot_id='boot-a'
        backend.commands=Mock()
        backend.commands.enabled.return_value=True
        backend.commands.run.side_effect=lambda args:'active' if 'show' in args else 'cap_sys_admin=p'
        backend.status=Mock(return_value=dict(uid=1000,session='3'))
        backend.host=Mock();backend.host.boot_output_status.return_value=None
        backend.helper=SimpleNamespace(STATE_DIRECTORY='/fixture')
        backend.fallback_masks=Mock(return_value=[])
        with TemporaryDirectory() as directory, \
             patch('monitorize.remote_login.settings.SETTINGS',Path(directory)/'absent'), \
             patch('monitorize.remote_login.enrollment._no_engines') as no_engines:
            result=backend.snapshot(1000,'3')
        self.assertTrue(result['maintenance_required'])
        no_engines.assert_not_called()
        backend.status.return_value=dict(uid=2000,session='3')
        with self.assertRaises(PermissionError):backend.snapshot(1000,'3')

    def test_idle_maintenance_is_prepared_before_reconfiguring_an_active_service(self):
        backend=object.__new__(live.InstalledBackend)
        lifecycle=Mock();lifecycle.load.side_effect=[None,{'masks':['monitorize-remote-login.service']}]
        backend.maintenance=Mock(return_value=lifecycle)
        backend.commands=Mock()
        journal=Mock();journal.value={'system':{'maintenance_required':True,'masks':[]}}
        with patch('monitorize.remote_login.enrollment._no_engines') as no_engines, \
             patch('monitorize.remote_login.enrollment.enable') as enable, \
             patch('monitorize.remote_login.live_setup.TransactionFiles'):
            backend.configure(1000,journal)
        lifecycle.prepare.assert_called_once_with('upgrade',include_idle_services=True)
        lifecycle.backend.unmask.assert_called_once_with(['monitorize-remote-login.service'])
        self.assertTrue(journal.value['system']['maintenance_started'])
        no_engines.assert_called_once();enable.assert_called_once()

    def test_foreign_pending_maintenance_is_not_adopted(self):
        backend=object.__new__(live.InstalledBackend)
        lifecycle=Mock();lifecycle.load.return_value={'uid':2000}
        backend.maintenance=Mock(return_value=lifecycle)
        journal=Mock();journal.value={'system':{'maintenance_required':True,'masks':[]}}
        with self.assertRaisesRegex(RuntimeError,'maintenance is pending'):
            backend.configure(1000,journal)
        lifecycle.prepare.assert_not_called()
        self.assertNotIn('maintenance_started',journal.value['system'])
