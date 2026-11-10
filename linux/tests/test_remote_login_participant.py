import json
import unittest
from unittest.mock import patch
from monitorize.desktop.remote_login_runtime import Participant


class Interface:
    def __init__(self):self.state='waiting_for_desktop';self.calls=[]
    def GetStatus(self,**kwargs):return json.dumps({'state':self.state,'capture_ready':self.state=='desktop_available'})
    def AcquireDesktop(self,**kwargs):self.calls.append('acquire');return json.dumps({'granted':True,'generation':'a'*32})
    def PrepareDesktop(self,token,descriptor,**kwargs):
        self.calls.append(('prepare',json.loads(descriptor)));self.state='desktop_starting';return self.GetStatus()
    def Stop(self,token,**kwargs):self.calls.append('stop');self.state='stopped';return self.GetStatus()
    def Start(self,**kwargs):self.calls.append('start');self.state='waiting_for_desktop';return self.GetStatus()
    def ReleaseDesktop(self,token,**kwargs):return 'true'


class ParticipantTests(unittest.TestCase):
    def test_stale_source_engine_is_reported_before_ownership_or_prepare(self):
        interface=Interface()
        participant=Participant(factory=lambda:interface,output_finder=lambda:'Virtual-4')
        participant.interface=interface;participant.factory=None
        with patch('monitorize.desktop.remote_login_runtime.source_engine_update_error',
                   return_value='Update remote-login service assets with stage-remote-login.py'):
            status=participant.poll()
        self.assertEqual(status['state'],'error')
        self.assertIn('stage-remote-login.py',status['startup_error'])
        self.assertEqual(interface.calls,[])

    def test_physical_greeter_handoff_uses_root_reservation_without_connected_virtual_output(self):
        interface=Interface();original=interface.GetStatus
        interface.GetStatus=lambda **kwargs:json.dumps({**json.loads(original()),'output_name':'Virtual-0'})
        finder=__import__('unittest.mock',fromlist=['Mock']).Mock(return_value='Virtual-9')
        participant=Participant(factory=lambda:interface,output_finder=finder)
        participant.poll();participant.poll()
        self.assertIn(('prepare',{'capture':'kms','output_name':'Virtual-0'}),interface.calls)
        self.assertEqual(len([c for c in interface.calls if isinstance(c,tuple)]),1)
        finder.assert_not_called()

    def test_mirror_selection_reaches_the_desktop_engine_in_either_order(self):
        from unittest.mock import Mock

        for start_first in (False, True):
            with self.subTest(start_first=start_first):
                interface = Interface()
                original = interface.GetStatus
                interface.GetStatus = lambda **kwargs: json.dumps({
                    **json.loads(original()), 'desktop_mirror_version': 1,
                    'desktop_options_version': 1, 'output_name': 'Virtual-1',
                })
                participant = Participant(factory=lambda: interface)
                participant.interface = interface
                participant.factory = None
                participant.stream_options = {}
                participant.layout = Mock()
                participant.layout_mode = 'virtual_only'
                environment = {'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': 'X-Cinnamon'}
                saved = {'display_type': 'Mirror' if start_first else 'Extend', 'mirror_output': 'eDP-1'}
                with patch.dict('os.environ', environment), \
                     patch('monitorize.desktop.remote_login_runtime.source_engine_update_error', return_value=''), \
                     patch('monitorize.desktop.remote_login_runtime.engine_launch_error', return_value=''), \
                     patch('monitorize.config.settings.load_display_settings', return_value=saved):
                    if start_first:
                        participant.poll()
                    else:
                        participant.start(mirror_output='eDP-1')
                    interface.state = 'desktop_available'
                    participant.poll()
                descriptor = next(call[1] for call in interface.calls if isinstance(call, tuple))
                self.assertEqual(descriptor['mirror_output'], 'eDP-1')
                self.assertEqual(descriptor['output_name'], 'Virtual-1')
                participant.layout.update.assert_called_with('extend', True, 'Virtual-1')

    def test_desktop_descriptor_carries_saved_client_mode_policy(self):
        interface=Interface();participant=Participant(factory=lambda:interface,output_finder=lambda:'Virtual-4')
        # This descriptor test targets the current service API. Advertising the
        # legacy API would read/write an installed profile on the test machine.
        original=interface.GetStatus
        interface.GetStatus=lambda **kwargs:json.dumps({**json.loads(original()),'desktop_options_version':1})
        participant.interface=interface;participant.factory=None;participant.stream_options={}
        with patch('monitorize.desktop.remote_login_runtime.source_engine_update_error',return_value=''), \
             patch('monitorize.config.settings.load_display_settings',return_value=dict(use_client_display_settings=True,resolution='2340x1600',fps='90')), \
             patch('monitorize.desktop.remote_login_runtime.legacy_stream_options') as legacy:
            participant.poll()
        legacy.assert_not_called()
        values=[call[1] for call in interface.calls if isinstance(call,tuple)][0]
        self.assertTrue(values['client_display_mode']);self.assertEqual(values['display_mode'],[2340,1600,90.0])

    def test_removed_output_can_restart_listener_from_reserved_service_identity(self):
        interface=Interface()
        original=interface.GetStatus
        interface.GetStatus=lambda **kwargs:json.dumps({**json.loads(original()),'output_name':'Virtual-4'})
        participant=Participant(factory=lambda:interface,output_finder=lambda:None)
        participant.poll()
        self.assertIn(('prepare',{'capture':'kms','output_name':'Virtual-4'}),interface.calls)

    def test_adoption_can_request_connected_output_before_compositor_enables_it(self):
        interface=Interface()
        with patch('monitorize.desktop.remote_login_runtime.find_boot_output',return_value='Virtual-4') as finder:
            participant=Participant(factory=lambda:interface)
            participant.poll()
        finder.assert_called_once_with(require_enabled=False)
        self.assertIn(('prepare',{'capture':'kms','output_name':'Virtual-4'}),interface.calls)

    def test_one_sender_and_no_duplicate_prepare(self):
        interface=Interface();connections=[]
        def factory():connections.append(1);return interface
        participant=Participant(factory=factory,output_finder=lambda:'Virtual-4')
        participant.poll();participant.poll()
        self.assertEqual(len(connections),1)
        self.assertEqual(interface.calls,['acquire',('prepare',{'capture':'kms','output_name':'Virtual-4'})])

    def test_stop_survives_polling_and_start_is_explicit(self):
        interface=Interface();participant=Participant(factory=lambda:interface,output_finder=lambda:'Virtual-4')
        participant.poll();participant.stop();participant.poll();participant.poll()
        self.assertEqual(interface.calls.count('stop'),1)
        self.assertEqual(len([c for c in interface.calls if isinstance(c,tuple)]),1)
        participant.start()
        self.assertEqual(len([c for c in interface.calls if isinstance(c,tuple)]),2)

    def test_missing_output_does_not_start_or_capture_other_screen(self):
        interface=Interface();participant=Participant(factory=lambda:interface,output_finder=lambda:None)
        self.assertEqual(participant.poll()['state'],'waiting_for_desktop')
        self.assertEqual(interface.calls,['acquire'])


class RuntimeStateTests(unittest.TestCase):
    def test_missing_or_failed_service_is_not_an_active_stream(self):
        from monitorize.desktop.remote_login_runtime import RemoteLoginRuntime
        from types import SimpleNamespace
        for state in ('unavailable','error','stopped','disabled'):
            self.assertFalse(RemoteLoginRuntime.active.fget(SimpleNamespace(enabled=True,status={'state':state})))
        for state in ('desktop_listening','desktop_available','disconnect_grace','desktop_starting'):
            self.assertTrue(RemoteLoginRuntime.active.fget(SimpleNamespace(enabled=True,status={'state':state})))


class LegacyStreamOptionsTests(unittest.TestCase):
    def test_codec_profile_update_is_atomic_and_preserves_identity(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from monitorize.desktop.remote_login_runtime import legacy_stream_options
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sunshine.conf'
            path.write_text('hevc_mode = 2\nav1_mode = 1\ncert = keep-me\n')
            settings=SimpleNamespace(profile=directory,encoder='software',adapter='')
            options=dict(encoder='software',adapter_name='',hevc_mode='0',av1_mode='0')
            with patch('monitorize.desktop.remote_login_runtime.read_settings',return_value=settings):
                legacy_stream_options(options)
                self.assertIn('hevc_mode = 2',path.read_text())
                legacy_stream_options(options,write=True)
                self.assertIn('hevc_mode = 0',path.read_text())
                self.assertIn('cert = keep-me',path.read_text())
                with self.assertRaisesRegex(ValueError,'encoder and GPU'):
                    legacy_stream_options(dict(options,encoder='nvenc'),write=True)


class ResumeOwnershipTests(unittest.TestCase):
    def setUp(self):
        from monitorize.remote_login.service import HandoffService
        from linux.tests.test_remote_login_service import Sessions,Units
        self.sessions=Sessions();self.sessions.user=True
        self.active=True
        original=self.sessions.snapshot
        def snapshot():
            values=original()
            for session in values['sessions']:session['active']=self.active
            return values
        self.sessions.snapshot=snapshot
        self.units=Units();self.service=HandoffService(1000,self.sessions,self.units)
        self.service.tick();service=self.service
        self.acquisitions=0
        test=self
        class Adapter:
            def GetStatus(self,**kw):return json.dumps(service.status())
            def AcquireDesktop(self,**kw):
                test.acquisitions+=1
                return json.dumps(service.acquire(':1.10'))
            def PrepareDesktop(self,generation,descriptor,**kw):
                return json.dumps(service.start_desktop(':1.10',generation,json.loads(descriptor)))
            def Stop(self,generation,**kw):return json.dumps(service.stop(':1.10',generation))
            def Start(self,**kw):return json.dumps(service.start(':1.10'))
        self.participant=Participant(factory=Adapter,output_finder=lambda:'Virtual-1')
        mocked=patch('monitorize.desktop.remote_login_runtime.saved_stream_options',return_value={})
        mocked.start();self.addCleanup(mocked.stop)

    def test_resume_reacquires_revoked_token_without_service_restart(self):
        self.participant.poll();first=self.participant.generation;epoch=self.service.epoch
        self.assertTrue(self.units.desktop)
        self.active=False;self.service.tick()
        self.assertFalse(self.units.desktop)
        # App need not observe the suspended state before the laptop freezes.
        self.active=True;self.service.tick()
        self.assertEqual(self.participant.poll()['state'],'desktop_starting')
        self.assertTrue(self.units.desktop)
        self.assertNotEqual(first,self.participant.generation)
        self.assertEqual(self.acquisitions,2)
        self.assertEqual(epoch,self.service.epoch)

    def test_multiple_sleep_cycles_recover_once_each(self):
        self.participant.poll()
        generations={self.participant.generation}
        for _ in range(3):
            self.active=False;self.service.tick()
            self.participant.poll()
            self.assertEqual(self.participant.generation,'')
            self.active=True;self.service.tick()
            self.assertEqual(self.participant.poll()['state'],'desktop_starting')
            generations.add(self.participant.generation)
            self.participant.poll()
        self.assertEqual(self.acquisitions,4)
        self.assertEqual(len(generations),4)

    def test_other_account_is_not_captured_after_sleep(self):
        self.participant.poll()
        self.active=False;self.service.tick()
        self.sessions.other=True;self.active=True;self.service.tick()
        self.assertEqual(self.participant.poll()['state'],'suspended')
        self.assertFalse(self.units.desktop)
        self.assertEqual(self.acquisitions,1)

    def test_intentional_stop_is_preserved_across_suspend_resume(self):
        self.participant.poll();self.participant.stop()
        self.active=False;self.service.tick()
        self.active=True;self.service.tick()
        self.assertEqual(self.participant.poll()['state'],'stopped')
        self.assertFalse(self.units.desktop)
        self.assertEqual(self.acquisitions,1)
        self.participant.start()
        self.assertTrue(self.units.desktop)

    def test_authentication_failure_is_explicit_for_poll_and_restart(self):
        from monitorize.remote_login.dbus_service import AuthenticationFailed
        from monitorize.desktop.remote_login_runtime import RemoteLoginRuntime
        from types import SimpleNamespace
        with patch.object(self.participant,'_call',side_effect=AuthenticationFailed('invalid caller scope')):
            for result in (self.participant.poll(),self.participant.start()):
                self.assertEqual(result['state'],'authentication_error')
                self.assertIn('authenticate',result['control_error'])
                self.assertFalse(result['listening'])
                self.assertFalse(RemoteLoginRuntime.active.fget(SimpleNamespace(enabled=True,status=result)))
        self.assertEqual(self.participant.generation,'')

    def test_rejected_token_does_not_claim_service_is_offline(self):
        from monitorize.remote_login.dbus_service import Rejected
        self.participant.poll()
        with patch.object(self.participant,'_call',side_effect=Rejected('ownership revoked')):
            self.assertEqual(self.participant.poll()['state'],'ownership_wait')
        self.assertEqual(self.participant.generation,'')
