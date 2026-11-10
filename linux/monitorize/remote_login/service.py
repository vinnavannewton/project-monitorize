"""Serialized authenticated handoff effects over fixed systemd engine units.

The D-Bus adapter supplies sender names and a system-bus LogindSessions reader.
This class accepts no unit name, executable, UID or session from its desktop client.
"""
from threading import RLock
import secrets
from .supervisor import Supervisor


class HandoffService:
    def __init__(self,uid,sessions,units,*,journal=None):
        self.model=Supervisor(uid);self.sessions=sessions;self.units=units;self.epoch=secrets.token_hex(16)
        self.journal=journal
        self.boot_error='';self._diagnostic_state=None;self._observation_error=None
        if journal is not None:self.model.stopped_session=journal.load()
        if journal is not None and hasattr(journal, 'load_boot_pause'):
            self.model.boot_paused = journal.load_boot_pause() is True
        self.boot_cleanup_pending = self.model.boot_paused
        self.owner=None;self.owner_session=None;self.ready=False;self.boot_ready=False;self.boot_listening=False;self.boot_phase='';self.listening=False;self.desktop_phase='';self.startup_error='';self._lock=RLock()

    def status(self):
        with self._lock:
            state=self.model.state
            if state == 'greeter_stopped' and self.boot_cleanup_pending:
                state = 'greeter_stopping'
            if state=='desktop_owned':state=('stopping_desktop' if self.model.stopped_session==self.model.session else
                'desktop_available' if self.ready else self.desktop_phase if self.listening and self.desktop_phase else 'desktop_starting')
            if self.model.state=='desktop_owned' and self.startup_error and self.model.stopped_session!=self.model.session:
                state='error'
            if state=='greeter_listening':
                state='login_available' if self.boot_ready else self.boot_phase if self.boot_listening and self.boot_phase else state
                if self.boot_error:state='error'
            error=self.boot_error if self.model.state=='greeter_listening' else self.startup_error
            diagnostic=(state,self.model.state,self.listening,self.ready,self.boot_listening,self.boot_ready,bool(error))
            if diagnostic!=self._diagnostic_state:
                print('Remote-login state=%s coordinator=%s desktop_listening=%s desktop_capture=%s boot_listening=%s boot_capture=%s operation_error=%s'%diagnostic,flush=True)
                self._diagnostic_state=diagnostic
            return dict(display_idle_timeout_seconds=getattr(self,'grace_timeout',None) or (self.display_timeout() if hasattr(self,'display_timeout') else 20),desktop_mode_version=1,desktop_options_version=1,desktop_mirror_version=getattr(self,'desktop_mirror_version',0),startup_error=error,state=state,epoch=self.epoch,enabled=self.model.enabled,capture_ready=self.ready or self.boot_ready,
                        uid=self.model.uid,desktop_owned=bool(self.owner and self.owner_session==self.model.session),
                        session=self.model.session or '',listening=self.listening,
                        boot_paused=self.model.boot_paused,boot_listening=self.boot_listening,
                        output_name=self.output_name() if hasattr(self,'output_name') else '')

    def tick(self):
        with self._lock:
            stage='session_snapshot'
            try:
                snapshot=self.sessions.snapshot()
                stage='engine_status'
                boot=self.units.running('boot');desktop=self.units.running('desktop')
                stage='engine_health'
                if hasattr(self.units,'readiness'):
                    boot_health=self.units.readiness('boot') if boot else {}
                    self.boot_ready=bool(boot_health.get('capture_ready'))
                    self.boot_listening=bool(boot_health.get('listening'))
                    self.boot_error=str(boot_health.get('startup_error') or '')[:512]
                    self.boot_phase={'desktop_listening':'greeter_idle','disconnect_grace':'greeter_disconnect_grace','streaming':'greeter_preparing'}.get(boot_health.get('state'),'')
                    health=self.units.readiness('desktop') if desktop else {}
                    self.startup_error=str(health.get('startup_error') or '')[:512]
                    self.ready=bool(health.get('capture_ready'))
                    self.listening=bool(health.get('listening'))
                    self.desktop_phase=health.get('state','') if health.get('state') in ('desktop_listening','disconnect_grace','streaming','removing') else ''
                    active_health=health if desktop else boot_health
                    self.grace_timeout=active_health.get('display_idle_timeout_seconds') if active_health.get('state')=='disconnect_grace' else None
                else:self.boot_ready=False;self.boot_listening=False
                # After service restart an unclaimed existing engine is stopped,
                # never assigned to whichever desktop happens to call first.
                stage='ownership_reconcile'
                if desktop and self.owner is None:
                    self.units.stop('desktop')
                previous_stop=self.model.stopped_session
                identity_free = self.units.identity_free()
                result=self.model.reconcile(sessions=snapshot['sessions'],
                    greeter_ready=snapshot['greeter_ready'],boot_running=boot,
                    desktop_running=desktop,identity_free=identity_free)
                if self.journal is not None and previous_stop is not None and self.model.stopped_session is None:
                    self.journal.save(None)
                if self.owner_session!=self.model.session:
                    self.owner=None;self.owner_session=None;self.ready=False
                if not desktop:self.ready=False;self.listening=False
                stage='engine_action_'+result['action']
                if result['action']=='start_boot':self.units.start('boot')
                elif result['action']=='stop_boot':self.units.stop('boot')
                elif result['action']=='stop_desktop':self.units.stop('desktop');self.ready=False
                if (result['state'] == 'greeter_stopped' and self.boot_cleanup_pending
                        and snapshot['greeter_ready'] and identity_free):
                    stage = 'boot_display_cleanup'
                    cleanup = getattr(self, 'boot_pause_cleanup', None)
                    if cleanup is not None:
                        cleanup()
                    self.boot_cleanup_pending = False
                elif result['state'] == 'greeter_stopped' and not snapshot['greeter_ready']:
                    # Keep the reservation for the next verified greeter.
                    self.boot_cleanup_pending = False
                observer=getattr(self,'desktop_listening_observer',None)
                if (observer is not None and self.model.state=='desktop_owned' and self.owner
                        and self.owner_session==self.model.session and self.listening and not self.startup_error
                        and self.model.stopped_session!=self.model.session):
                    try:
                        observer(self.model.session)
                        self._receipt_error=None
                    except Exception as error:
                        if getattr(self,'_receipt_error',None)!=type(error).__name__:
                            print('Enrollment receipt completion pending: '+type(error).__name__,flush=True)
                            self._receipt_error=type(error).__name__
                if self._observation_error is not None:
                    print('Remote-login observation recovered.',flush=True)
                    self._observation_error=None
            except Exception as error:
                failure=(stage,type(error).__name__)
                if self._observation_error!=failure:
                    print('Remote-login observation failed at '+stage+': '+failure[1]+'. Capture suspended; observation will retry.',flush=True)
                    self._observation_error=failure
                self.boot_listening=False
                self.model.state='error';self.model.available=False;self.ready=False;self.boot_ready=False;self.listening=False
                # A failed observation is not permission to continue capture.
                for phase in ('boot','desktop'):
                    try:self.units.stop(phase)
                    except Exception:pass
            return self.status()

    def _caller(self,sender):return self.sessions.authenticate(sender,self.model.uid)

    def _owned(self,sender,generation):
        caller=self._caller(sender)
        if (sender!=self.owner or caller['session']!=self.owner_session or
            not generation or generation!=self.model.generation):
            raise PermissionError('Stale or unauthorized remote-login ownership.')
        return caller

    def acquire(self,sender):
        with self._lock:
            caller=self._caller(sender);self.tick()
            if self.owner is not None and self.owner!=sender:
                raise PermissionError('Another controller owns this desktop session.')
            if not self.model.can_acquire(caller['uid'],caller['session']):
                return dict(granted=False,state=self.status()['state'])
            token=self.model.acquire(caller['uid'],caller['session'])
            self.owner=sender;self.owner_session=caller['session']
            return dict(granted=True,generation=token,state=self.status()['state'])

    def start_desktop(self,sender,generation,descriptor):
        with self._lock:
            caller=self._owned(sender,generation)
            if self.units.running('boot'):raise RuntimeError('Login engine has not exited.')
            if self.units.running('desktop'):return self.status()
            if not self.units.identity_free():raise RuntimeError('Identity is still in use.')
            if self.model.stopped_session==caller['session']:raise RuntimeError('Desktop listening is stopped.')
            self.units.prepare_desktop(caller,generation,descriptor)
            self.units.start('desktop');self.ready=False;self.tick()
            return self.status()

    def desktop_ready(self,sender,generation):
        with self._lock:
            self._owned(sender,generation);self.tick()
            if not self.units.running('desktop') or self.model.state!='desktop_owned':
                raise RuntimeError('Desktop engine is not running in the selected session.')
            if hasattr(self.units,'readiness') and not self.ready:
                raise RuntimeError('Desktop capture health has not been verified.')
            self.ready=True;return self.status()

    def release(self,sender,generation):
        with self._lock:
            self._owned(sender,generation);self.units.stop('desktop')
            if self.units.running('desktop') or not self.units.identity_free():return False
            self.model.release(generation);self.owner=None;self.owner_session=None;self.ready=False
            return True

    def stop(self,sender,generation):
        with self._lock:
            caller=self._owned(sender,generation)
            try:
                if self.journal is not None:self.journal.save(caller['session'])
                self.model.user_stop(caller['session'])
            finally:self.units.stop('desktop');self.ready=False
            return self.tick()

    def start(self,sender):
        with self._lock:
            caller=self._caller(sender)
            if self.owner is not None and self.owner!=sender:raise PermissionError('Another controller owns this session.')
            self.tick()
            if self.journal is not None:self.journal.save(None)
            self.model.user_start(caller['session']);return self.tick()

    def set_boot_paused(self, uid, paused):
        from .policy import session_target
        with self._lock:
            if type(uid) is not int or uid != self.model.uid or not self.model.enabled:
                raise PermissionError('Only the enrolled account can control its boot listener.')
            if type(paused) is not bool:
                raise ValueError('Invalid boot pause request.')
            snapshot = self.sessions.snapshot()
            if session_target(snapshot['sessions'], uid)[0] != 'greeter':
                raise PermissionError('A desktop session now owns the seat.')
            if self.journal is not None:
                self.journal.save_boot_pause(paused)
            self.model.boot_paused = paused
            self.boot_cleanup_pending = paused
            return self.tick()

    def sender_lost(self,sender):
        with self._lock:
            if sender!=self.owner:return
            self.units.stop('desktop');self.model.release(self.model.generation)
            self.owner=None;self.owner_session=None;self.ready=False
