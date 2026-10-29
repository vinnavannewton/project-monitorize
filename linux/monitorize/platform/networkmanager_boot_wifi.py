"""Bounded NetworkManager operations on private synchronous D-Bus connections.

The Qt facade runs these operations on one dedicated worker, never the GUI
thread. No GLib watches, shell commands, passwords in diagnostics, or root helper.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import json
import tempfile
from pathlib import Path
import re
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from monitorize.platform.boot_wifi_settings import describe_profile, build_boot_wifi_update, settings_fingerprint

SERVICE='org.freedesktop.NetworkManager'
ROOT='/org/freedesktop/NetworkManager'
CONNECTION=SERVICE+'.Settings.Connection'
MESSAGES={
    'unsupported':'Wi-Fi setup requires NetworkManager 1.44 or newer with profile version support.',
    'unavailable':'NetworkManager is unavailable. Recover the configured network service, then refresh profiles.',
    'authorization':'Authorization was denied or cancelled. Ensure a Polkit authentication agent is running in your desktop.',
    'profile_changed':'The selected network profile changed or disappeared. Refresh and select it again.',
    'password':'Enter a valid Wi-Fi password for the selected security type.',
    'ineligible':'This profile cannot be converted. Select a saved personal WPA2/WPA3 infrastructure network.',
    'outcome_unknown':'NetworkManager did not confirm the result. The change may have completed; refresh before trying again.',
    'failed':'NetworkManager could not complete Wi-Fi setup. Check the selected profile and try refreshing.',
    'verification':'The profile was updated but its saved settings could not be confirmed. Refresh before retrying.',
    'closed':'Wi-Fi setup is no longer available.',
}


class NmProblem(Exception):
    def __init__(self,code):
        self.code=code if code in MESSAGES else 'failed'
        super().__init__(MESSAGES[self.code])


def _problem(error, *, mutation=False):
    if isinstance(error,NmProblem):return error
    name=error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
    lower=name.lower()
    if any(word in lower for word in ('permission','accessdenied','notauthorized','authfailed','cancelled','canceled')):
        return NmProblem('authorization')
    if 'version' in lower:return NmProblem('profile_changed')
    if any(word in lower for word in ('unknownmethod','unknownproperty','invalidsignature')):return NmProblem('unsupported')
    if any(word in lower for word in ('noreply','timeout','timedout','disconnected','namehasnoowner','serviceunknown')):
        return NmProblem('outcome_unknown' if mutation else 'unavailable')
    # Never forward an exception body: NM may echo arguments, including PSKs.
    return NmProblem('failed')


class NmTransport:
    def __init__(self,bus=None):
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        self.bus=bus or dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        self.deadline=time.monotonic()+25
        try:self.owner=str(self.bus.get_name_owner(SERVICE))
        except Exception as error:
            self.close()
            raise _problem(error) from None

    def close(self):
        self.bus.close()

    def call(self,path,interface,method,args=(),signature='',*,interactive=False,timeout=5):
        import dbus.lowlevel
        mutation=method=='Update2'
        message=None
        try:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise NmProblem('unavailable')
            message=dbus.lowlevel.MethodCallMessage(self.owner,path,interface,method)
            message.set_allow_interactive_authorization(interactive)
            if signature:message.append(*args,signature=signature)
            response=self.bus.send_message_with_reply_and_block(message,min(timeout,remaining))
            return response.get_args_list(byte_arrays=True)
        except Exception as error:
            raise _problem(error,mutation=mutation) from None
        finally:
            message=None
            args=()

    def properties(self,path,interface):
        return self.call(path,'org.freedesktop.DBus.Properties','GetAll',(interface,),'s')[0]


class WifiOperations:
    def __init__(self,factory=NmTransport,receipt_path=None):
        self.factory=factory
        self.receipt_path=Path(receipt_path) if receipt_path is not None else Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'monitorize/boot-wifi-confirmations.json'

    def _receipts(self):
        try:
            values=json.loads(self.receipt_path.read_text())
            return values if isinstance(values,dict) else {}
        except (OSError,ValueError):return {}

    def _remember(self,uuid,settings):
        values=self._receipts();values[uuid]=settings_fingerprint(settings)
        self.receipt_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=self.receipt_path.parent,delete=False) as stream:
                temporary=Path(stream.name)
                json.dump(values,stream)
            temporary.replace(self.receipt_path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)


    def _manager(self,bus):
        properties=bus.properties(ROOT,SERVICE)
        match=re.match(r'^(\d+)\.(\d+)',str(properties.get('Version','')))
        if not match or tuple(map(int,match.groups()))<(1,44):raise NmProblem('unsupported')
        return properties

    def _paths(self,bus):
        paths=bus.call(ROOT+'/Settings',SERVICE+'.Settings','ListConnections')[0]
        if len(paths)>64:raise NmProblem('unsupported')
        return [str(path) for path in paths]

    def _read(self,bus,path):
        before=bus.properties(path,CONNECTION)
        version=int(before.get('VersionId',0))
        if version<=0:raise NmProblem('unsupported')
        settings=bus.call(path,CONNECTION,'GetSettings')[0]
        after=bus.properties(path,CONNECTION)
        if int(after.get('VersionId',0))!=version:raise NmProblem('profile_changed')
        persistent=bool(after.get('Filename')) and not bool(after.get('Unsaved',True))
        return settings,version,persistent

    def discover(self):
        bus=None
        try:
            if os.path.isfile('/.flatpak-info'):raise NmProblem('unsupported')
            bus=self.factory()
            manager=self._manager(bus)
            active=set();ethernet=False
            for active_path in manager.get('ActiveConnections',[]):
                values=bus.properties(str(active_path),SERVICE+'.Connection.Active')
                if values.get('Type')=='802-11-wireless':active.add(str(values.get('Connection','')))
                if values.get('Type')=='802-3-ethernet':ethernet=True
            profiles=[];receipts=self._receipts()
            for path in self._paths(bus):
                settings,version,persistent=self._read(bus,path)
                if settings.get('connection',{}).get('type')!='802-11-wireless':continue
                description=describe_profile(settings,active=path in active,version=version,persistent=persistent)
                description['setup_saved']=bool(description['configuration']=='configuration_matches' and persistent
                    and receipts.get(description['uuid'])==settings_fingerprint(settings))
                profiles.append(description)
            profiles.sort(key=lambda p:(not p['active'],p['name'],p['uuid']))
            message=('Ethernet is connected; Wi-Fi setup is optional.' if ethernet else
                     'Select the connected Wi-Fi.')
            if not manager.get('WirelessEnabled',True):message+=' Wi-Fi radio is currently off.'
            if not profiles:message='No saved Wi-Fi profiles found. Connect using your network settings first; Ethernet needs no Wi-Fi conversion.'
            return dict(profiles=profiles,status=dict(code='selection_required' if profiles else 'no_wifi',message=message))
        except Exception as error:
            problem=_problem(error)
            return dict(profiles=[],status=dict(code=problem.code,message=str(problem)))
        finally:
            if bus is not None:bus.close()

    def configure(self,uuid,expected_version,password):
        bus=None;updated=None;dispatched=False
        result=dict(success=False,uuid=str(uuid),configured=False,boot_verified=False)
        try:
            if os.path.isfile('/.flatpak-info'):raise NmProblem('unsupported')
            if not str(expected_version).isdigit() or not 0<int(expected_version)<2**64:
                raise NmProblem('profile_changed')
            bus=self.factory();self._manager(bus)
            chosen=None
            for path in self._paths(bus):
                settings,version,persistent=self._read(bus,path)
                if str(settings.get('connection',{}).get('uuid',''))==uuid:
                    chosen=(path,settings,version,persistent)
                    break
            if chosen is None:raise NmProblem('profile_changed')
            path,settings,version,persistent=chosen
            if version!=int(expected_version):raise NmProblem('profile_changed')
            description=describe_profile(settings,active=False,version=version,persistent=persistent)
            if not description['eligible']:raise NmProblem('ineligible')
            try:updated=build_boot_wifi_update(settings,password)
            except ValueError:raise NmProblem('password') from None
            import dbus
            # Keep every unknown field and NM D-Bus type; only replacements get
            # explicit signatures, especially the empty permissions string array.
            updated['connection']['autoconnect']=dbus.Boolean(True)
            updated['connection']['permissions']=dbus.Array([],signature='s')
            updated['802-11-wireless-security']['psk-flags']=dbus.UInt32(0)
            updated['802-11-wireless-security']['psk']=dbus.String(password)
            password=''
            args=(updated,dbus.UInt32(1),dbus.Dictionary({'version-id':dbus.UInt64(version)},signature='sv'))
            if hasattr(bus,'deadline'):bus.deadline=time.monotonic()+90
            dispatched=True
            bus.call(path,CONNECTION,'Update2',args,'a{sa{sv}}ua{sv}',interactive=True,timeout=90)
            args=()
            updated=None
            if hasattr(bus,'deadline'):bus.deadline=time.monotonic()+15
            try:
                saved,new_version,persistent=self._read(bus,path)
                verified=describe_profile(saved,active=False,version=new_version,persistent=persistent)
                if (verified['uuid']!=uuid or not persistent or
                        verified['configuration']!='configuration_matches'):
                    raise NmProblem('verification')
            except Exception:raise NmProblem('verification') from None
            try:
                self._remember(uuid,saved)
                message='✓ Authorized and configured for automatic connection before login.'
            except OSError:
                message='Configured for boot, but local confirmation could not be saved. Refresh or retry setup.'
            result.update(success=True,code='configured',configured=True,message=message)
        except Exception as error:
            problem=_problem(error,mutation=dispatched)
            result.update(code=problem.code,message=str(problem))
        finally:
            password='';updated=None
            if bus is not None:bus.close()
        return result


class NetworkManagerBootWifi(QObject):
    profilesChanged=pyqtSignal('QVariantList')
    statusChanged=pyqtSignal('QVariantMap')
    operationFinished=pyqtSignal('QVariantMap')
    busyChanged=pyqtSignal()
    _completed=pyqtSignal(int,str,object)

    def __init__(self,parent=None,operations=None):
        super().__init__(parent)
        self.operations=operations or WifiOperations()
        self.busy=False;self.closed=False;self.visible=False;self.serial=0
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='monitorize-wifi')
        self._completed.connect(self._finish)
        self._timer=QTimer(self);self._timer.setInterval(10000)
        self._timer.timeout.connect(self.refresh)

    def _submit(self,kind,operation,*args):
        if self.closed or self.busy:return
        self.busy=True;self.busyChanged.emit()
        self.serial+=1;serial=self.serial
        def run():
            # Catch/sanitize inside the worker so Future never retains a
            # traceback with the password or a raw D-Bus exception.
            try:return operation(*args)
            except Exception:
                return dict(profiles=[],status=dict(code='failed',message=MESSAGES['failed'])) if kind=='refresh' else dict(success=False,code='failed',message=MESSAGES['failed'],configured=False,boot_verified=False)
        future=self._executor.submit(run)
        def done(completed):
            if not self.closed:
                try:self._completed.emit(serial,kind,completed.result())
                except RuntimeError:pass
        future.add_done_callback(done)

    @pyqtSlot()
    def refresh(self):
        self._submit('refresh',self.operations.discover)

    def recover_service(self):
        from monitorize.platform.system_setup import recover_networkmanager
        self._submit('configure',recover_networkmanager)

    def set_visible(self,visible):
        self.visible=bool(visible)
        if self.visible and not self.closed:
            self._timer.start();self.refresh()
        else:self._timer.stop()

    def configure(self,uuid,version,password):
        self._submit('configure',self.operations.configure,uuid,version,password)

    def _finish(self,serial,kind,result):
        if self.closed or serial!=self.serial:return
        self.busy=False;self.busyChanged.emit()
        if kind=='refresh':
            self.profilesChanged.emit(result['profiles']);self.statusChanged.emit(result['status'])
        else:
            self.operationFinished.emit(result)
            if self.visible:self.refresh()

    def close(self):
        self.closed=True;self._timer.stop()
        self._executor.shutdown(wait=False,cancel_futures=True)
