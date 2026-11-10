"""Read-only selection of an authenticated greeter's existing primary output."""
import json
import math
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

LOGIN_DISPLAY_POLICY_PROTOCOL = 1


def validate_target(target):
    if not isinstance(target,dict):raise ValueError('Login primary display response is invalid.')
    name=target.get('output_name');width=target.get('width');height=target.get('height');rate=target.get('fps')
    if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',name)
            or type(width) is not int or not 0<width<=16384
            or type(height) is not int or not 0<height<=16384
            or type(rate) not in (int,float) or not math.isfinite(rate) or not 0<rate<=1000
            or target.get('capture')!='kms'):
        raise ValueError('Login primary display has an invalid connector or mode.')
    return dict(output_name=name,capture='kms',width=width,height=height,fps=rate)


def select_primary(state):
    try:
        physical,logical=state[1:3]
        if len(physical)>64 or len(logical)>64:raise ValueError()
        primary=[row for row in logical if row[4]]
        if len(primary)!=1 or not primary[0][5]:raise ValueError()
        # Mirrored outputs share the login form. Capture the first physical
        # member of that primary logical monitor, never an unrelated secondary.
        name=str(primary[0][5][0][0])
        matches=[row for row in physical if str(row[0][0])==name]
        if len(matches)!=1:raise ValueError()
        modes=[mode for mode in matches[0][1] if mode[6].get('is-current')]
        if len(modes)!=1:raise ValueError()
        mode=modes[0]
        return validate_target(dict(output_name=name,capture='kms',width=int(mode[1]),height=int(mode[2]),fps=float(mode[3])))
    except (IndexError,KeyError,TypeError,AttributeError,ValueError,OverflowError):
        raise ValueError('GDM has no unique enabled primary display. Connect or enable a primary display before streaming the login screen.') from None


def select_kwin_primary(state):
    try:
        outputs=state['outputs']
        if not isinstance(outputs,list) or len(outputs)>64:raise ValueError()
        enabled=[item for item in outputs if item.get('enabled') is True and item.get('connected') is True]
        primary=[item for item in enabled if type(item.get('priority')) is int and item['priority']==1]
        if not primary and len(enabled)==1:primary=enabled
        if len(primary)!=1:raise ValueError()
        output=primary[0];modes=output['modes']
        if not isinstance(modes,list) or len(modes)>1024:raise ValueError()
        current=[mode for mode in modes if mode['id']==output['currentModeId']]
        if len(current)!=1:raise ValueError()
        mode=current[0]
        return validate_target(dict(capture='kms',output_name=output['name'],
            width=mode['size']['width'],height=mode['size']['height'],fps=mode['refreshRate']))
    except (KeyError,TypeError,AttributeError,ValueError):
        raise ValueError('The login screen has no unique enabled primary display. Connect a display or enable virtual-display login.') from None


def query(compositor='gnome'):
    """Demoted child: read one immutable bus owner's display state."""
    if compositor not in ('gnome','kde'):raise ValueError('Unknown greeter compositor.')
    import dbus
    bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
    try:
        daemon=dbus.Interface(bus.get_object('org.freedesktop.DBus','/org/freedesktop/DBus',introspect=False),'org.freedesktop.DBus')
        service='org.gnome.Mutter.DisplayConfig' if compositor=='gnome' else 'org.kde.KWin'
        name=daemon.GetNameOwner(service,timeout=2)
        pid=int(daemon.GetConnectionUnixProcessID(name,timeout=2))
        if compositor=='gnome':
            interface=dbus.Interface(bus.get_object(name,'/org/gnome/Mutter/DisplayConfig',introspect=False),'org.gnome.Mutter.DisplayConfig')
            target=select_primary(interface.GetCurrentState(timeout=2))
        else:
            result=subprocess.run(['/usr/bin/kscreen-doctor','--json'],capture_output=True,text=True,timeout=3,check=True)
            if len(result.stdout)>262144:raise ValueError('KWin output response exceeds its bound.')
            target=select_kwin_primary(json.loads(result.stdout))
        if daemon.GetNameOwner(service,timeout=2)!=name:
            raise PermissionError('Greeter compositor changed while selecting the primary display.')
        return dict(pid=pid,target=target)
    finally:bus.close()


def verify_drm(target,*,root=Path('/sys/class/drm')):
    connector_id = target.get('connector_id') if isinstance(target, dict) else None
    target = validate_target(target)
    if connector_id is not None and (type(connector_id) is not int or connector_id <= 0):
        raise ValueError('Invalid DRM connector ID.')
    enabled = []
    candidates = []
    for path in root.glob('card*-*'):
        match = re.fullmatch(r'card[0-9]+-([A-Za-z][A-Za-z0-9_-]{0,63})', path.name)
        if not match:
            continue
        try:
            if ((path/'status').read_text().strip() != 'connected'
                    or (path/'enabled').read_text().strip() != 'enabled'):
                continue
            enabled.append(path)
            canonical = match[1]
            alias = canonical.replace('HDMI-A-', 'HDMI-', 1) if canonical.startswith('HDMI-A-') else canonical
            if connector_id is None:
                matches = target['output_name'] in (canonical, alias)
            else:
                matches = (path/'connector_id').read_text().strip() == str(connector_id)
            if matches:
                candidates.append(path)
        except FileNotFoundError:
            continue
    if len(candidates) != 1:
        raise PermissionError('Login primary display does not resolve to one enabled DRM connector.')
    name = candidates[0].name.split('-', 1)[1]
    if sum(path.name.split('-', 1)[1] == name for path in enabled) != 1:
        raise PermissionError('Login primary DRM connector name is ambiguous across GPUs.')
    return candidates[0]


def operation(settings,context,operation,helper,check):
    if operation not in ('prepare','idle'):
        raise ValueError('Existing login-screen capture requires a prepare or idle request.')
    check()
    if operation=='idle':
        # The physical display belongs to the greeter. Never change its configuration.
        return {}
    from .gdm_context import socket_owner
    if settings.manager=='gdm.service':
        from .gdm_context import verify
        compositor='gnome'
    elif settings.manager in ('plasmalogin.service','sddm.service'):
        from .kwin_context import verify
        if settings.manager=='sddm.service':
            from .display_managers import require_manager
            if require_manager(settings.manager)['compositor']!='kde':
                raise ValueError('Existing-display capture requires a verified KWin or GNOME greeter. Enable virtual-display login for this compositor.')
        from .daemon import require_installed
        require_installed(Path('/usr/bin/kscreen-doctor'))
        compositor='kde'
    else:raise ValueError('This greeter has no existing-display capture adapter. Enable virtual-display login.')
    session=context['session'];uid=context['greeter_uid']
    environment=verify(context,session,{'XDG_SESSION_TYPE':'wayland'},helper)
    runtime=Path(environment['XDG_RUNTIME_DIR']);socket=runtime/environment['WAYLAND_DISPLAY']
    expected_pid=socket_owner(socket,uid,runtime)
    account=pwd.getpwuid(uid)
    def read():
        result=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'query',compositor],
            env=environment,user=uid,group=account.pw_gid,extra_groups=os.getgrouplist(account.pw_name,account.pw_gid),
            capture_output=True,text=True,timeout=8,check=False)
        if result.returncode:
            raise RuntimeError('The existing login display could not be read. Connect a display or enable virtual-display login.')
        if len(result.stdout)>4096:raise ValueError('Login primary display response is too large.')
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or type(value.get('pid')) is not int or value['pid']!=expected_pid:
            raise PermissionError('Primary display state came from a different greeter compositor.')
        return validate_target(value.get('target'))
    target=read()
    connector=verify_drm(target)
    check()
    verify(context,session,environment,helper)
    if socket_owner(socket,uid,runtime)!=expected_pid:
        raise PermissionError('Greeter compositor changed during primary display selection.')
    if read()!=target:
        raise PermissionError('Login primary display changed during capture selection; retry the connection.')
    check()
    if verify_drm(target)!=connector:
        raise PermissionError('Primary DRM connector changed during capture selection.')
    return validate_target(dict(target,output_name=connector.name.split('-',1)[1]))


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='query' or sys.argv[2] not in ('gnome','kde'):raise SystemExit(2)
    if os.geteuid()==0:raise SystemExit('Primary display query must run as the greeter.')
    print(json.dumps(query(sys.argv[2])))
