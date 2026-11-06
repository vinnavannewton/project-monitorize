"""Deterministic remote-login ownership coordinator.

This module only decides actions; it never executes commands, grants D-Bus callers
or assumes a process stopped merely because a stop was requested. The service
adapter must authenticate the caller, observe actual engine/lock state, serialize
reconcile/acquire/release and enact only fixed installed unit operations.
"""
import secrets
from .policy import session_target


class Supervisor:
    def __init__(self,uid,*,enabled=True):
        if type(uid) is not int or uid<=0:raise ValueError('Invalid selected account.')
        self.uid=uid;self.enabled=enabled;self.session=None
        self.generation=None;self.stopped_session=None;self.available=False
        self.state='waiting_for_greeter'
        self.boot_paused=False

    def reconcile(self,*,sessions,greeter_ready,boot_running,desktop_running,identity_free):
        target,session=session_target(sessions,self.uid)
        if session!=self.session:
            self.generation=None
            if self.stopped_session!=session:self.stopped_session=None
        self.session=session;self.available=False
        action='wait'
        if not self.enabled:
            self.state='disabled';self.generation=None
            action='stop_boot' if boot_running else 'stop_desktop' if desktop_running else 'wait'
        elif target in ('error','suspended'):
            self.state=target;self.generation=None
            action='stop_boot' if boot_running else 'stop_desktop' if desktop_running else 'wait'
        elif target=='desktop':
            if boot_running:
                self.state='handoff_to_desktop';action='stop_boot'
            elif desktop_running:
                # A service restart may observe an owner without a local token.
                # Do not grant a second owner; service must reconcile authenticated
                # ownership, never infer it from a PID alone.
                self.state='desktop_owned'
            else:
                self.state='stopped' if self.stopped_session==session else 'waiting_for_desktop'
                self.available=identity_free and self.stopped_session!=session
        elif desktop_running:
            self.state='handoff_to_greeter';self.generation=None;action='stop_desktop'
        elif self.boot_paused:
            self.state='greeter_stopping' if boot_running else 'greeter_stopped'
            if boot_running:action='stop_boot'
        elif not greeter_ready:
            self.state='waiting_for_greeter'
            if boot_running:action='stop_boot'
        elif boot_running:
            self.state='greeter_listening'
        elif identity_free:
            self.state='waiting_for_greeter';action='start_boot'
        else:
            self.state='waiting_for_greeter'
        return {'state':self.state,'action':action,'session':self.session}

    def can_acquire(self,uid,session):
        return (self.enabled and uid==self.uid and bool(session) and session==self.session
                and self.stopped_session!=session and
                (self.available or (self.generation is not None and self.state=='desktop_owned')))

    def acquire(self,uid,session):
        if not self.can_acquire(uid,session):raise PermissionError('Desktop ownership is not available.')
        if self.generation is None:self.generation=secrets.token_hex(16)
        return self.generation

    def release(self,generation):
        if not self.generation or generation!=self.generation:return False
        self.generation=None;return True

    def user_stop(self,session):
        if session!=self.session:raise PermissionError('Stale desktop session.')
        self.stopped_session=session;self.available=False

    def user_start(self,session):
        if session!=self.session:raise PermissionError('Stale desktop session.')
        self.stopped_session=None
