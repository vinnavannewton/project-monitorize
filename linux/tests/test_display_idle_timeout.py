import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

from monitorize.remote_login import enrollment, daemon
from monitorize.remote_login.idle import IdleDisplay
from monitorize.remote_login.descriptors import DescriptorStore
from monitorize.remote_login.root_files import RootFiles
from monitorize.remote_login.settings import validate_idle_timeout


class TimeoutTests(unittest.TestCase):
    def test_bounds_and_types(self):
        for value in (1,20,600):self.assertEqual(validate_idle_timeout(value),value)
        for value in (True,False,0,601,20.0,'20',None):
            with self.subTest(value=value), self.assertRaises(ValueError):validate_idle_timeout(value)

    def test_live_changes_apply_only_to_new_deadlines_and_invalid_refresh_retains_last_value(self):
        now=[0];timeout=[5]
        display=IdleDisplay(Mock(return_value={}),Mock(),clock=lambda:now[0],timeout_provider=lambda:timeout[0])
        display.schedule_cleanup();self.assertEqual(display.deadline,5)
        timeout[0]=60;now[0]=4;self.assertFalse(display.due())
        now[0]=5;self.assertTrue(display.expire())
        display.schedule_cleanup();self.assertEqual(display.deadline,65)
        timeout[0]=0;display.schedule_cleanup()
        self.assertEqual(display.deadline,65);self.assertTrue(display.configuration_error)
        timeout[0]=20;display.schedule_cleanup();self.assertFalse(display.configuration_error)

    def test_custom_timeout_reconnect_duplicate_disconnect_and_multiple_clients(self):
        for seconds in (5,20,60):
            with self.subTest(seconds=seconds):
                now=[0];remove=Mock()
                display=IdleDisplay(Mock(return_value={}),remove,clock=lambda:now[0],timeout_seconds=seconds)
                def event(op,client='c'):
                    return display.request(dict(operation=op,client_id=client,request_id='r'))
                event('prepare');event('stream_started');event('prepare','second');event('stream_started','second')
                event('stream_stopped');now[0]=1000;self.assertFalse(display.due())
                event('stream_stopped','second');deadline=display.deadline
                now[0]+=seconds-1;event('stream_stopped','second');self.assertEqual(display.deadline,deadline)
                event('prepare');self.assertIsNone(display.deadline)
                event('abort');now[0]+=seconds;self.assertTrue(display.expire());remove.assert_called_once()

    def test_save_preserves_enrollment_and_does_not_restart_services(self):
        with tempfile.TemporaryDirectory() as directory:
            files=RootFiles(Path(directory),owner_uid=os.getuid())
            original=dict(uid=1000,enabled=True,manager='gdm.service',retained='keep')
            files.write('etc/monitorize/remote-login.json',json.dumps(original))
            with patch.object(enrollment,'read_settings',return_value=SimpleNamespace(uid=1000,enabled=True)),patch.object(enrollment,'RootFiles',return_value=files),patch.object(enrollment,'Commands') as commands:
                self.assertTrue(enrollment.configure_display_idle_timeout(1000,60)['success'])
                self.assertEqual(json.loads((Path(directory)/'etc/monitorize/remote-login.json').read_text()),dict(original,display_idle_timeout_seconds=60))
                with self.assertRaises(PermissionError):enrollment.configure_display_idle_timeout(1001,5)
                commands.assert_not_called()

    def test_client_uses_scoped_command_and_rejects_bad_input_before_authorization(self):
        from monitorize.platform import system_setup
        with patch.object(system_setup,'remote_login_setup_status',return_value=dict(available=True,enabled=True)),patch.object(Path,'read_text',return_value='DISPLAY_IDLE_TIMEOUT_PROTOCOL = 1'),patch.object(system_setup.shutil,'which',return_value='/usr/bin/pkexec'),patch.object(system_setup.subprocess,'run') as run:
            run.return_value=SimpleNamespace(returncode=0,stdout='{"success":true}',stderr='')
            self.assertTrue(system_setup.apply_display_idle_timeout(60)['success'])
            self.assertEqual(run.call_args.args[0],['/usr/bin/pkexec',str(system_setup.HELPER),'--remote-login','timeout','--display-idle-timeout','60'])
            run.reset_mock();self.assertFalse(system_setup.apply_display_idle_timeout(True)['success']);run.assert_not_called()


class AdoptionWiringTests(unittest.TestCase):
    def test_service_wires_real_host_adoption_before_descriptor_publication(self):
        # Unlike descriptor-only tests, inspect the actual service construction.
        host=ModuleType('monitorize_vkms');helper=ModuleType('monitorize_vkms.helper')
        helper.__file__='/usr/lib/monitorize-vkms/monitorize_vkms/helper.py'
        helper._active_session=Mock();host.helper=helper
        runtime=ModuleType('monitorize_vkms.boot_runtime')
        runtime.__file__='/usr/lib/monitorize-vkms/monitorize_vkms/boot_runtime.py'
        host.boot_runtime=runtime
        boot=ModuleType('monitorize_vkms.boot_display')
        for name in ('boot_output_status','return_to_greeter','reconnect_reserved','greeter_operation_installed'):
            setattr(boot,name,Mock())
        state={'phase':'greeter'}
        def adopt(uid,session,operation):
            self.assertEqual((uid,session,operation),(1000,'s1','adopt'))
            state['phase']='desktop'
            return {'connector':'Virtual-1'}
        boot.desktop_operation_installed=Mock(side_effect=adopt)
        settings=SimpleNamespace(uid=1000,directory=Path('/unused'),enabled=True,display_idle_timeout_seconds=20)
        with patch.dict('sys.modules',{'monitorize_vkms':host,'monitorize_vkms.helper':helper,'monitorize_vkms.boot_display':boot}),patch.object(daemon,'require_installed'),patch.object(daemon,'DescriptorStore') as store,patch.object(daemon,'EngineUnits'),patch.object(daemon,'LogindSessions'),patch('monitorize.remote_login.journal.StopJournal'):
            daemon.build_service(settings,Mock())
            adopter=store.call_args.args[2]
            self.assertEqual(adopter(1000,'s1'),{'connector':'Virtual-1'})
            self.assertEqual(state['phase'],'desktop')
            boot.desktop_operation_installed.assert_called_once()
            # Use the wired adopter with real descriptor publication and service
            # ordering: an immediate launch must see a desktop-owned lease.
            with tempfile.TemporaryDirectory() as directory:
                settings.gid=os.getgid()
                descriptors=DescriptorStore(settings,lambda:'Virtual-1',adopter,root=Path(directory)/'run',owner_uid=os.getuid())
                units=Mock();units.running.return_value=False;units.identity_free.return_value=True
                units.prepare_desktop.side_effect=lambda caller,generation,descriptor:descriptors.write(caller,generation,descriptor)
                def start(phase):
                    self.assertEqual(state['phase'],'desktop')
                    self.assertTrue((descriptors.root/'1000/desktop.json').exists())
                units.start.side_effect=start
                sessions=Mock()
                sessions.snapshot.return_value=dict(greeter_ready=False,sessions=[dict(id='s1',uid=1000,seat='seat0',active=True,remote=False,type='wayland')])
                sessions.authenticate.return_value=dict(uid=1000,session='s1')
                from monitorize.remote_login.service import HandoffService
                service=HandoffService(1000,sessions,units)
                # A bare Mock would incorrectly advertise readiness support.
                del units.readiness
                generation=service.acquire(':1.10')['generation']
                state['phase']='greeter'
                service.start_desktop(':1.10',generation,dict(capture='kms',output_name='Virtual-1'))
                units.start.assert_called_once_with('desktop')
                # No idle cleanup or sleep was needed to repair the transfer.


    def test_physical_greeter_to_desktop_listens_and_prepares_on_first_client_without_restart(self):
        from monitorize.remote_login.service import HandoffService
        from monitorize.desktop.remote_login_runtime import Participant
        with tempfile.TemporaryDirectory() as directory:
            settings=SimpleNamespace(uid=os.getuid(),gid=os.getgid())
            lease=dict(schema_version=2,protected=True,owner_kind='remote-login',uid=os.getuid(),
                       phase='greeter',session='',state='reserved')
            def adopt(uid,session):
                self.assertFalse(units.running('desktop'))
                lease.update(phase='desktop',session=session)
                return dict(lease)
            store=DescriptorStore(settings,lambda:'Virtual-0',adopt,root=Path(directory)/'run',owner_uid=os.getuid())
            running={'desktop':False,'boot':False}
            units=Mock();units.running.side_effect=lambda phase:running[phase]
            units.identity_free.side_effect=lambda:not any(running.values())
            units.prepare_desktop.side_effect=lambda caller,generation,descriptor:store.write(caller,generation,descriptor)
            units.start.side_effect=lambda phase:running.update({phase:True})
            units.stop.side_effect=lambda phase:running.update({phase:False})
            units.readiness.side_effect=lambda phase:dict(listening=running[phase],state='desktop_listening')
            sessions=Mock();sessions.snapshot.return_value=dict(greeter_ready=False,sessions=[dict(id='s1',uid=os.getuid(),seat='seat0',active=True,remote=False,type='wayland')])
            sessions.authenticate.return_value=dict(uid=os.getuid(),session='s1')
            core=HandoffService(os.getuid(),sessions,units);core.output_name=lambda:'Virtual-0'
            class Adapter:
                def GetStatus(self,**kwargs):return json.dumps(core.tick())
                def AcquireDesktop(self,**kwargs):return json.dumps(core.acquire(':1.10'))
                def PrepareDesktop(self,generation,descriptor,**kwargs):return json.dumps(core.start_desktop(':1.10',generation,json.loads(descriptor)))
            participant=Participant(factory=Adapter,output_finder=lambda:None)
            self.assertEqual(participant.poll()['state'],'desktop_listening')
            self.assertEqual(participant.poll()['state'],'desktop_listening')
            units.start.assert_called_once_with('desktop')
            self.assertEqual(lease['phase'],'desktop');self.assertNotIn('connector',lease)
            def prepare():
                self.assertEqual(lease['phase'],'desktop')
                lease.update(state='active',connector='Virtual-2')
                return dict(output_name='Virtual-2',capture='kms',width=1920,height=1080,fps=60)
            display=IdleDisplay(prepare,Mock())
            request=dict(client_id='paired-client',request_id='first-request')
            response=display.request(dict(request,operation='prepare'))
            self.assertEqual(response['target']['output_name'],'Virtual-2')
            display.request(dict(request,operation='stream_ready'))
            self.assertTrue(display.health(listening=True)['capture_ready'])


class ClientModeTests(unittest.TestCase):
    def event(self,display,operation,client='c',mode=(2340,1600,60)):
        return display.request(dict(operation=operation,client_id=client,request_id='r',width=mode[0],height=mode[1],fps=mode[2]))

    def display(self):
        prepare=Mock(side_effect=lambda mode:dict(output_name='Virtual-2',width=mode[0],height=mode[1],fps=mode[2]))
        return IdleDisplay(Mock(),Mock(),prepare_mode=prepare),prepare

    def test_client_mode_reaches_prepare_and_different_mode_requires_idle_clients(self):
        display,prepare=self.display()
        response=self.event(display,'prepare');self.assertEqual(response['target']['width'],2340)
        prepare.assert_called_once_with([2340,1600,60])
        self.event(display,'stream_ready')
        with self.assertRaises(ValueError):self.event(display,'prepare','other',(1920,1080,60))
        self.assertEqual(display.requests,{('c','r')});self.assertEqual(prepare.call_count,1)
        self.event(display,'prepare','other');self.assertEqual(prepare.call_count,1)
        self.event(display,'release','other');self.event(display,'stream_stopped')
        self.event(display,'prepare',mode=(1280,720,90));prepare.assert_called_with([1280,720,90])

    def test_bad_mode_does_not_touch_display_or_add_pending_request(self):
        display,prepare=self.display()
        for mode in ((True,1600,60),(0,1600,60),(9000,1600,60),(2340,1600,float('nan'))):
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.event(display,'prepare',mode=mode)
        prepare.assert_not_called();self.assertFalse(display.requests)
