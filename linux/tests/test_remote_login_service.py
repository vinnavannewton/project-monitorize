import unittest
from monitorize.remote_login.service import HandoffService


class Sessions:
    def __init__(self):self.user=False;self.other=False
    def snapshot(self):
        return {'greeter_ready':not self.user,'sessions':([] if not self.user else
            [dict(id='s1',uid=1001 if self.other else 1000,seat='seat0',active=True,remote=False,type='wayland')])}
    def authenticate(self,sender,uid):
        if not self.user or self.other or sender not in (':1.10',':1.11'):raise PermissionError()
        return dict(uid=1000,pid=1234,session='s1')


class Units:
    def __init__(self):self.boot=False;self.desktop=False;self.events=[];self.stop_delayed=False;self.free=True
    def running(self,phase):return getattr(self,phase)
    def start(self,phase):self.events.append(('start',phase));setattr(self,phase,True);self.free=False
    def stop(self,phase):
        self.events.append(('stop',phase))
        if not self.stop_delayed:setattr(self,phase,False);self.free=True
    def identity_free(self):return self.free
    def prepare_desktop(self,caller,generation,descriptor):self.events.append(('prepare',caller['session']))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.sessions=Sessions();self.units=Units();self.service=HandoffService(1000,self.sessions,self.units)
    def login(self):
        self.service.tick();self.sessions.user=True
        self.service.tick();return self.service.acquire(':1.10')

    def test_engine_startup_error_is_visible_and_clears_when_listening(self):
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.units.readiness=lambda phase:({'listening':False,'startup_error':'Sunshine exited (status 0)'} if phase=='desktop' else {})
        self.service.tick()
        self.assertEqual(self.service.status()['state'],'error')
        self.assertIn('status 0',self.service.status()['startup_error'])
        self.units.readiness=lambda phase:({'listening':True,'state':'desktop_listening'} if phase=='desktop' else {})
        self.service.tick()
        self.assertEqual(self.service.status()['state'],'desktop_listening')
        self.assertEqual(self.service.status()['startup_error'],'')

    def test_completion_requires_same_session_fresh_listener(self):
        from unittest.mock import Mock
        observer=Mock();self.service.desktop_listening_observer=observer
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        observer.assert_not_called()
        self.units.readiness=lambda phase:({'listening':True,'state':'desktop_listening'} if phase=='desktop' else {})
        self.service.tick();observer.assert_called_with('s1')
        self.assertEqual(self.service.status()['uid'],1000)
        self.assertTrue(self.service.status()['desktop_owned'])

    def test_receipt_observer_failure_does_not_stop_a_healthy_listener(self):
        from unittest.mock import Mock
        self.service.desktop_listening_observer=Mock(side_effect=OSError('private'))
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.units.readiness=lambda phase:({'listening':True,'state':'desktop_listening'} if phase=='desktop' else {})
        self.service.tick();self.assertTrue(self.units.desktop)
        self.assertEqual(self.service.status()['state'],'desktop_listening')

    def test_boot_display_error_is_visible_without_claiming_capture_ready(self):
        self.service.tick()
        self.units.readiness=lambda phase:({'listening':True,'startup_error':'Remote display preparation failed.'} if phase=='boot' else {})
        status=self.service.tick()
        self.assertEqual(status['state'],'error')
        self.assertIn('preparation failed',status['startup_error'])
        self.assertFalse(status['capture_ready'])

    def test_complete_login_and_logout_cycle(self):
        result=self.login();self.assertTrue(result['granted']);token=result['generation']
        self.service.start_desktop(':1.10',token,{})
        self.assertEqual(self.service.status()['state'],'desktop_starting')
        self.service.desktop_ready(':1.10',token)
        self.assertEqual(self.service.status()['state'],'desktop_available')
        self.sessions.user=False;self.service.tick();self.service.tick()
        self.assertTrue(self.units.boot);self.assertFalse(self.units.desktop)
        self.assertEqual(self.units.events[:2],[('start','boot'),('stop','boot')])

    def test_boot_pause_is_account_scoped_and_leaves_enrollment_enabled(self):
        cleanups = []
        self.service.boot_pause_cleanup = lambda: cleanups.append('idle')
        self.service.tick()
        self.units.stop_delayed = True
        with self.assertRaises(PermissionError):
            self.service.set_boot_paused(1001, True)
        self.assertEqual(self.service.set_boot_paused(1000, True)['state'], 'greeter_stopping')
        self.assertEqual(cleanups, [])
        self.units.boot = False
        self.units.free = True
        self.assertEqual(self.service.tick()['state'], 'greeter_stopped')
        self.service.tick()
        self.assertEqual(cleanups, ['idle'])
        self.assertTrue(self.service.status()['enabled'])
        self.assertEqual(self.units.events.count(('start', 'boot')), 1)
        self.service.set_boot_paused(1000, False)
        self.assertEqual(self.units.events.count(('start', 'boot')), 2)

    def test_boot_pause_cannot_stop_a_desktop_owner(self):
        token = self.login()['generation']
        self.service.start_desktop(':1.10', token, {})
        with self.assertRaises(PermissionError):
            self.service.set_boot_paused(1000, True)
        self.assertTrue(self.units.desktop)

    def test_stop_request_is_not_exit(self):
        self.service.tick();self.units.stop_delayed=True;self.sessions.user=True
        result=self.service.acquire(':1.10');self.assertFalse(result['granted'])
        self.assertEqual(self.units.events.count(('start','desktop')),0)
        self.units.boot=False;self.units.free=False
        self.assertFalse(self.service.acquire(':1.10')['granted'])
        self.units.free=True;self.assertTrue(self.service.acquire(':1.10')['granted'])

    def test_second_sender_cannot_steal_or_reuse_token(self):
        token=self.login()['generation']
        with self.assertRaises(PermissionError):self.service.acquire(':1.11')
        with self.assertRaises(PermissionError):self.service.start_desktop(':1.11',token,{})
        with self.assertRaises(PermissionError):self.service.start_desktop(':1.10','stale',{})

    def test_stop_cancels_desktop_restart_until_explicit_start(self):
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.service.stop(':1.10',token)
        self.assertFalse(self.service.acquire(':1.10')['granted'])
        self.service.start(':1.10');self.assertTrue(self.service.acquire(':1.10')['granted'])

    def test_owner_disconnect_stops_engine_before_regrant(self):
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.units.stop_delayed=True;self.service.sender_lost(':1.10')
        self.assertFalse(self.service.acquire(':1.11')['granted'])
        self.units.desktop=False;self.units.free=True
        self.assertTrue(self.service.acquire(':1.11')['granted'])

    def test_other_user_forces_stop_and_cannot_acquire(self):
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.sessions.other=True;self.service.tick()
        self.assertFalse(self.units.desktop)
        with self.assertRaises(PermissionError):self.service.acquire(':1.10')

    def test_restart_cannot_assume_unknown_lock_is_free(self):
        self.sessions.user=True;self.units.desktop=True;self.units.free=False
        result=self.service.acquire(':1.10')
        self.assertFalse(result['granted']);self.assertIn(('stop','desktop'),self.units.events)
        self.assertTrue(self.service.acquire(':1.10')['granted'])

    def test_snapshot_error_stops_capture_without_leaking_error(self):
        self.service.tick()
        def fail():raise RuntimeError('private-fixture-data')
        self.sessions.snapshot=fail;self.service.tick()
        self.assertFalse(self.units.boot);self.assertEqual(self.service.status()['state'],'error')
        self.assertNotIn('private',str(self.service.status()))

    def test_readiness_expires_even_while_engine_process_stays_alive(self):
        healthy={'capture_ready':True}
        self.units.readiness=lambda phase:healthy if phase=='desktop' else {}
        token=self.login()['generation'];self.service.start_desktop(':1.10',token,{})
        self.assertTrue(self.service.status()['capture_ready'])
        healthy.clear();self.service.tick()
        self.assertFalse(self.service.status()['capture_ready'])
        self.assertEqual(self.service.status()['state'],'desktop_starting')
        with self.assertRaisesRegex(RuntimeError,'health'):
            self.service.desktop_ready(':1.10',token)

class RestartStopTests(unittest.TestCase):
    def test_manual_stop_survives_service_restart_but_not_logout(self):
        class Journal:
            session=None
            def load(self):return self.session
            def save(self,session):self.session=session
        journal=Journal();sessions=Sessions();units=Units();sessions.user=True
        first=HandoffService(1000,sessions,units,journal=journal)
        token=first.acquire(':1.10')['generation'];first.stop(':1.10',token)
        second=HandoffService(1000,sessions,units,journal=journal)
        self.assertFalse(second.acquire(':1.10')['granted'])
        self.assertEqual(second.status()['state'],'stopped')
        sessions.user=False;second.tick();self.assertIsNone(journal.session)
        sessions.user=True;second.tick();self.assertTrue(second.acquire(':1.10')['granted'])


class GreeterHealthTests(unittest.TestCase):
    def test_listener_without_frame_is_idle_not_capture_ready(self):
        sessions=Sessions();units=Units();units.boot=True
        units.readiness=lambda phase:dict(listening=True,capture_ready=False,state='desktop_listening') if phase=='boot' else {}
        service=HandoffService(1000,sessions,units)
        result=service.tick()
        self.assertEqual(result['state'],'greeter_idle');self.assertFalse(result['capture_ready'])
