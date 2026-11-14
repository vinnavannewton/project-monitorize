"""Actual enrollment -> host reservation -> desktop adoption on fake OS boundaries."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from contextlib import ExitStack, nullcontext
import unittest
from unittest.mock import patch

HOST=Path(os.environ.get('MONITORIZE_VKMS_SOURCE','/nonexistent'))


@unittest.skipUnless((HOST/'monitorize_vkms/boot_display.py').is_file(),'Set MONITORIZE_VKMS_SOURCE for cross-repository live enrollment')
class IntegratedLiveTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0,str(HOST))
        from monitorize_vkms import boot_display,boot_runtime,helper,lease
        from monitorize.remote_login import live_setup
        self.host=boot_display;self.runtime=boot_runtime;self.helper=helper;self.lease=lease;self.live=live_setup
        temporary=TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state';self.connector=self.root/'connector';self.connector.mkdir()
        for key,value in dict(status='2',enabled='1',dynamic='1',edid='',edid_enabled='1').items():
            (self.connector/key).write_text(value)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for context in (patch.object(lease,'OWNER_UID',os.getuid()),
            patch.object(boot_display,'_installed_context',return_value=(helper,boot_runtime,dict(uid=1000,enabled=True))),
            patch.object(helper,'STATE_DIRECTORY',self.state),
            patch.object(helper,'_acquire_lifecycle_lock',side_effect=lambda:os.open(self.root/'lock',os.O_CREAT|os.O_RDWR,0o600)),
            patch.object(helper,'_configfs_root',return_value=self.root),
            patch.object(helper,'_require_bootstrap',return_value=(self.root,self.connector)),
            patch.object(boot_runtime,'desktop_context',return_value='3'),
            patch.object(boot_runtime,'quiescent',side_effect=lambda uid:nullcontext())):
            self.stack.enter_context(context)
        self.boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def backend(self):
        live=self.live;fixture=self
        class Backend(live.InstalledBackend):
            def __init__(self):
                self.boot_id=fixture.boot_id;self.host=fixture.host;self.runtime=fixture.runtime;self.helper=fixture.helper
            def preflight(self,uid):pass  # Installed trust/device checks are separate unit boundaries.
            def existing(self,uid,session):return False
            def snapshot(self,uid,session):return dict(units={},active={},caps='',masks=[],lease=None)
            def configure(self,uid,journal):
                from monitorize.remote_login.enrollment import configure_next_boot
                commands=SimpleNamespace(enabled=lambda name:False,enable=lambda name:None,disable=lambda name:None,reload=lambda:None)
                configure_next_boot(dict(uid=uid,encoder='software',adapter='',enabled=True,schema_version=1),
                    'Paired PC',[],files=live.TransactionFiles(journal),commands=commands)
            def activate(self,uid,journal):
                # Execute the actual dependency entry: no seat guard bypass.
                with patch.object(os,'geteuid',return_value=0),patch.object(fixture.runtime,'require_installed'), \
                     patch.object(fixture.runtime,'read_enrollment',return_value=dict(uid=uid,enabled=True)):
                    fixture.host.main()
        return Backend()

    def test_live_enrollment_adoption_stream_idle_and_logout(self):
        from monitorize.remote_login.service import HandoffService
        from monitorize.remote_login.descriptors import DescriptorStore
        from monitorize.desktop.remote_login_runtime import Participant
        from monitorize.remote_login.idle import IdleDisplay
        backend=self.backend();result=self.live.LiveEnrollment(backend,root=self.root,owner_uid=os.getuid()).enable(1000)
        self.assertTrue(result['success']);self.assertFalse(result['reboot_required'])
        self.assertEqual((self.connector/'status').read_text(),'2')
        settings=SimpleNamespace(uid=1000,gid=os.getgid(),encoder='software',adapter='')
        store=DescriptorStore(settings,lambda:'Virtual-0',lambda uid,session:self.host.desktop_operation_installed(uid,session,'adopt'),
            root=self.root/'descriptors',owner_uid=os.getuid(),session_environment=lambda uid:('3',dict(XDG_SESSION_TYPE='wayland',XDG_CURRENT_DESKTOP='KDE',WAYLAND_DISPLAY='wayland-1')))
        store._wayland_socket = lambda uid, session, name: name
        class Sessions:
            def snapshot(self):return dict(greeter_ready=False,sessions=[dict(id='3',uid=1000,seat='seat0',active=True,remote=False,type='wayland')])
            def authenticate(self,sender,uid):return dict(uid=uid,pid=123,session='3')
        class Units:
            desktop=False
            def running(self,phase):return self.desktop if phase=='desktop' else False
            def identity_free(self):return not self.desktop
            def start(self,phase):self.desktop=True
            def stop(self,phase):self.desktop=False
            def prepare_desktop(self,caller,generation,descriptor):store.write(caller,generation,descriptor)
            def readiness(self,phase):return dict(listening=self.desktop,state='desktop_listening')
        units=Units();service=HandoffService(1000,Sessions(),units);service.output_name=lambda:'Virtual-0'
        class Interface:
            def GetStatus(self,**kwargs):return json.dumps(service.tick())
            def AcquireDesktop(self,**kwargs):return json.dumps(service.acquire(':1.10'))
            def PrepareDesktop(self,generation,descriptor,**kwargs):return json.dumps(service.start_desktop(':1.10',generation,json.loads(descriptor)))
        participant=Participant(factory=Interface,output_finder=lambda:None)
        status=participant.poll()
        self.assertTrue(status['listening']);self.assertTrue(status['desktop_owned']);self.assertFalse(status['capture_ready'])
        self.assertEqual((self.connector/'status').read_text(),'2')
        # A client activates the same reservation; the compositor/sysfs boundary is fake.
        value=self.host.boot_output_status(self.state)
        drm=self.root/'card2-Virtual-2';drm.mkdir();(drm/'enabled').write_text('enabled')
        output=dict(name='Virtual-2',card='card2',connector_id=41,display='mon1',status='connected',
            edid_sha256=value['edid_sha256'],path=str(drm))
        worker=lambda *args,**kwargs:dict(success=True,state={},details=dict(width=1920,height=1080,refresh_rate=60))
        with patch.object(self.runtime,'discover',return_value={'Virtual-2':output}),patch.object(self.helper,'_run_worker',side_effect=worker):
            clock=[0.0]
            display=IdleDisplay(lambda:self.host.desktop_operation_installed(1000,'3','prepare'),
                lambda:self.host.desktop_operation_installed(1000,'3','idle',engines_stopped=True),clock=lambda:clock[0])
            request=dict(client_id='paired',request_id='request',operation='prepare')
            display.request(request);display.request({**request,'operation':'stream_ready'})
            self.assertTrue(display.health(listening=True)['capture_ready'])
            display.request({**request,'operation':'stream_stopped'});clock[0]=21;display.expire()
            self.assertEqual((self.connector/'status').read_text(),'2')
            current=self.host.boot_output_status(self.state)
            with patch.object(self.runtime,'local_sessions',return_value=[]):
                returned=self.host.return_to_greeter(state=self.state,generation=current['generation'],boot_id=self.boot_id,
                    sessions=[],engines_stopped=True,connector=self.connector)
            self.assertEqual(returned['phase'],'greeter');self.assertEqual(returned['state'],'reserved')

    def test_service_failure_rolls_back_only_its_new_reservation(self):
        backend=self.backend()
        backend.activate=lambda *args:(_ for _ in ()).throw(RuntimeError('start failed'))
        backend.commands=SimpleNamespace(run=lambda args:'',reload=lambda:None,enable=lambda name:None,disable=lambda name:None)
        with patch.object(self.runtime,'verify_quiescent'):
            result=self.live.LiveEnrollment(backend,root=self.root,owner_uid=os.getuid()).enable(1000)
        self.assertFalse(result['success']);self.assertFalse(result['cleanup_pending'])
        self.assertIsNone(self.host.boot_output_status(self.state))
        self.assertFalse((self.root/'etc/monitorize/remote-login.json').exists())
