"""System-bus adapter for the fixed remote-login handoff service.

No root process starts on import. Configuration/setup provides a HandoffService
whose effects are limited to fixed installed engine units and validated descriptors.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import dbus
import dbus.service
from .logind import CallerAuthenticationError

NAME='io.github.vinnavannewton.MonitorizeRemoteLogin1'
PATH='/io/github/vinnavannewton/MonitorizeRemoteLogin1'
INTERFACE=NAME


class Rejected(dbus.DBusException):
    _dbus_error_name=NAME+'.Rejected'


class AuthenticationFailed(Rejected):
    _dbus_error_name=NAME+'.AuthenticationFailed'


class Unavailable(dbus.DBusException):
    _dbus_error_name=NAME+'.Unavailable'


class SystemTransport:
    def __init__(self,bus):self.bus=bus

    def _destination(self,interface):
        for name in ('org.freedesktop.login1','org.freedesktop.systemd1','org.freedesktop.DBus'):
            if interface==name or interface.startswith(name+'.'):return name
        raise ValueError('Unsupported system service.')

    def call(self,path,interface,method,*args):
        if interface=='org.freedesktop.login1.Manager' and method in ('GetSessionByPID','GetUserByPID'):
            args=(dbus.UInt32(args[0]),)
        proxy=self.bus.get_object(self._destination(interface),path,introspect=False)
        return proxy.get_dbus_method(method,interface)(*args,timeout=3)

    def properties(self,path,interface):
        proxy=self.bus.get_object(self._destination(interface),path,introspect=False)
        return proxy.get_dbus_method('GetAll','org.freedesktop.DBus.Properties')(interface,timeout=3)


class ExportedService(dbus.service.Object):
    def __init__(self,bus,core):
        self.core=core;self.bus=bus;self.name=dbus.service.BusName(NAME,bus=bus,do_not_queue=True)
        super().__init__(bus,PATH)
        self._last=None
        self._workers=ThreadPoolExecutor(max_workers=4,thread_name_prefix='remote-display')
        self._slots=BoundedSemaphore(4)
        self.watch=bus.add_signal_receiver(self._name_changed,signal_name='NameOwnerChanged',
            dbus_interface='org.freedesktop.DBus',bus_name='org.freedesktop.DBus')

    def _invoke(self,method,*args):
        try:return json.dumps(method(*args),separators=(',',':'))
        except CallerAuthenticationError:raise AuthenticationFailed('Caller cannot be verified as the enrolled active graphical session. Launch Monitorize through its desktop controller service.') from None
        except PermissionError:raise Rejected('Caller/session or ownership token is not authorized.') from None
        except Exception as failure:
            print('Remote-login '+method.__name__+' failed: '+type(failure).__name__+': '+str(failure)[:512],flush=True)
            raise Unavailable('Remote-login operation could not complete; inspect service status.') from None

    @dbus.service.method(INTERFACE,in_signature='',out_signature='s')
    def GetStatus(self):return self._invoke(self.core.status)

    @dbus.service.method(INTERFACE, in_signature='b', out_signature='s', sender_keyword='sender')
    def SetBootPaused(self, paused, sender=None):
        # Only the enrolled account can pause its own service-owned listener.
        # Desktop ownership still requires the full local graphical-session gate.
        if not sender or not str(sender).startswith(':'):
            raise Rejected('A verified system-bus caller is required.')
        uid = int(self.bus.get_unix_user(sender))
        return self._invoke(self.core.set_boot_paused, uid, bool(paused))

    @dbus.service.method(INTERFACE,in_signature='',out_signature='s',sender_keyword='sender')
    def AcquireDesktop(self,sender=None):return self._invoke(self.core.acquire,str(sender))

    @dbus.service.method(INTERFACE,in_signature='ss',out_signature='s',sender_keyword='sender')
    def PrepareDesktop(self,generation,descriptor,sender=None):
        if len(descriptor)>16384:raise Rejected('Desktop descriptor is too large.')
        try:
            values=json.loads(descriptor)
            if not isinstance(values,dict):raise ValueError()
        except (ValueError,TypeError):raise Rejected('Invalid desktop descriptor.') from None
        return self._invoke(self.core.start_desktop,str(sender),str(generation),values)

    @dbus.service.method(INTERFACE,in_signature='s',out_signature='s',sender_keyword='sender')
    def DesktopReady(self,generation,sender=None):return self._invoke(self.core.desktop_ready,str(sender),str(generation))

    @dbus.service.method(INTERFACE,in_signature='s',out_signature='s',sender_keyword='sender')
    def ReleaseDesktop(self,generation,sender=None):return self._invoke(self.core.release,str(sender),str(generation))

    @dbus.service.method(INTERFACE,in_signature='s',out_signature='s',sender_keyword='sender')
    def Stop(self,generation,sender=None):return self._invoke(self.core.stop,str(sender),str(generation))

    @dbus.service.method(INTERFACE,in_signature='',out_signature='s',sender_keyword='sender')
    def Start(self,sender=None):return self._invoke(self.core.start,str(sender))

    def _async(self,method,args,reply,error):
        from gi.repository import GLib
        if not self._slots.acquire(blocking=False):
            error(Unavailable('Remote display operation is busy.'));return
        def work():
            try:
                value=json.dumps(method(*args),separators=(',',':'));callback=reply
            except CallerAuthenticationError:
                value=AuthenticationFailed('Caller cannot be verified as the enrolled active graphical session. Launch Monitorize through its desktop controller service.');callback=error
            except PermissionError as failure:
                reason=('desktop reservation was not adopted before engine startup'
                        if str(failure)=='Adopt the desktop reservation before starting its engine.'
                        else 'caller, session or display ownership validation failed')
                print('Remote display operation rejected: '+reason,flush=True)
                value=Rejected('Caller/session or engine ownership is not authorized.');callback=error
            except Exception as failure:
                # Fixed root operations contain no client password/token in errors.
                print('Remote display operation failed: '+type(failure).__name__+': '+str(failure)[:512],flush=True)
                value=Unavailable('Remote display operation failed; inspect service/runner journals.');callback=error
            finally:self._slots.release()
            GLib.idle_add(lambda:(callback(value),False)[1])
        self._workers.submit(work)

    @dbus.service.method(INTERFACE,in_signature='',out_signature='s',sender_keyword='sender',async_callbacks=('reply','error'))
    def BootContext(self,sender=None,reply=None,error=None):
        self._async(self.core.boot_context,(str(sender),),reply,error)

    @dbus.service.method(INTERFACE,in_signature='ss',out_signature='s',sender_keyword='sender',async_callbacks=('reply','error'))
    def EngineDisplay(self,generation,operation,sender=None,reply=None,error=None):
        self._async(self.core.engine_display,(str(sender),str(generation),str(operation)),reply,error)



    @dbus.service.method(INTERFACE,in_signature='ssiidb',out_signature='s',sender_keyword='sender',async_callbacks=('reply','error'))
    def EngineDisplayMode(self,generation,operation,width,height,fps,capture_idle,sender=None,reply=None,error=None):
        self._async(self.core.engine_display,(str(sender),str(generation),str(operation),[int(width),int(height),float(fps)],bool(capture_idle)),reply,error)

    @dbus.service.method(INTERFACE,in_signature='',out_signature='s',sender_keyword='sender',async_callbacks=('reply','error'))
    def RemoveDisplay(self,sender=None,reply=None,error=None):
        self._async(self.core.remove_display,(str(sender),),reply,error)

    @dbus.service.signal(INTERFACE,signature='s')
    def StateChanged(self,status):pass

    def _name_changed(self,name,old,new):
        if str(name).startswith(':') and old and not new:
            try:self.core.sender_lost(str(name))
            except Exception:pass  # Tick will reconcile actual units/lock state.

    def tick(self):
        status=self.core.tick();serialized=json.dumps(status,separators=(',',':'))
        if serialized!=self._last:self.StateChanged(serialized);self._last=serialized
        return True
