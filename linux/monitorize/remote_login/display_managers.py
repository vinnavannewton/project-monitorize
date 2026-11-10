"""Read-only login-manager capability discovery. Never executes configuration."""
import configparser
import os
from pathlib import Path
import shlex
import stat

SDDM_WAYLAND_PROTOCOL = 1
SDDM_X11_PROTOCOL = 1
LIGHTDM_X11_PROTOCOL = 2
GDM_WAYLAND_PROTOCOL = 1
MANAGERS=('plasmalogin.service','sddm.service','gdm.service','lightdm.service')
COMPOSITORS={'kwin_wayland':'kde','Hyprland':'hyprland','start-hyprland':'hyprland','weston':'weston'}


def sddm_configuration(root=Path('/'), *, owner_uid=0):
    root=Path(root)
    parser=configparser.ConfigParser(interpolation=None,strict=False)
    parser.optionxform=str
    files=[]
    for relative in ('usr/lib/sddm/sddm.conf.d','usr/lib64/sddm/sddm.conf.d','etc/sddm.conf.d'):
        directory=root/relative
        if directory.exists():files.extend(sorted(directory.glob('*.conf')))
    files.append(root/'etc/sddm.conf')
    for path in files:
        try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:continue
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_size>65536:
                raise ValueError('SDDM configuration is unsafe or too large: '+str(path))
            parser.read_string(stream.read(),source=str(path))
    server=parser.get('General','DisplayServer',fallback='x11').strip()
    command=parser.get('Wayland','CompositorCommand',fallback='weston --shell=kiosk').strip()
    try:args=shlex.split(command)
    except ValueError:raise ValueError('SDDM compositor command cannot be identified.') from None
    compositor=COMPOSITORS.get(Path(args[0]).name,'unknown') if args else 'unknown'
    return dict(display_server=server,compositor=compositor)


def _legacy_sddm_selected(root, owner_uid):
    """Recognize the openSUSE alternatives chain without running its scripts."""
    def trusted(path, kind):
        info = path.lstat()
        if info.st_uid != owner_uid or not kind(info.st_mode):
            return False
        if not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022:
            return False
        for parent in path.parents:
            info = parent.lstat()
            if info.st_uid != owner_uid or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
                return False
            if parent == root:
                return True
        return False

    links = (
        ('etc/systemd/system/display-manager.service', 'usr/lib/systemd/system/display-manager-legacy.service'),
        ('usr/lib/X11/displaymanagers/default-displaymanager', 'etc/alternatives/default-displaymanager'),
        ('etc/alternatives/default-displaymanager', 'usr/lib/X11/displaymanagers/sddm'),
    )
    try:
        for source, destination in links:
            path = root / source
            if not trusted(path, stat.S_ISLNK):
                return False
            target = path.readlink()
            target = root / target.relative_to('/') if target.is_absolute() else path.parent / target
            if Path(os.path.normpath(target)) != root / destination:
                return False
        return all(trusted(root / path, stat.S_ISREG) for path in (
            links[0][1], links[-1][1], 'usr/bin/sddm',
        )) and os.access(root / 'usr/bin/sddm', os.X_OK)
    except (OSError, ValueError):
        return False


def manager_capabilities(root=Path('/'), *, owner_uid=0):
    root=Path(root)
    try:unit=(root/'etc/systemd/system/display-manager.service').resolve().name
    except (OSError,RuntimeError):
        return dict(manager='',display_server='',compositor='',supported=False,validated=False,
                    message='Display-manager unit could not be resolved safely.')
    manager = {'gdm3.service': 'gdm.service'}.get(unit, unit)
    if unit == 'display-manager-legacy.service' and _legacy_sddm_selected(root.absolute(), owner_uid):
        manager = 'sddm.service'
    result=dict(manager=manager,service_unit=unit,display_server='',compositor='',supported=False,validated=False,
                message='This display manager has no remote-login adapter.')
    if manager=='plasmalogin.service':
        return dict(result,display_server='wayland',compositor='kde',supported=True,
                    message='Plasma Login Manager adapter; live validation is host-specific.')
    if manager=='gdm.service':
        return dict(result,display_server='runtime-verification-required',compositor='gnome',supported=True,
                    message='GDM adapter is experimental. A verified local Wayland greeter is required; headless bootstrap is not validated.')
    if manager == 'lightdm.service':
        for executable in ('usr/sbin/slick-greeter', 'usr/bin/xrandr'):
            if not (root / executable).is_file() or not os.access(root / executable, os.X_OK):
                return dict(result, message='LightDM remote login requires Slick Greeter and xrandr.')
        return dict(result, display_server='x11', compositor='xorg', supported=True,
                    message='LightDM X11 remote login requires an existing active Slick Greeter display.')
    if manager!='sddm.service':return result
    try:config=sddm_configuration(root,owner_uid=owner_uid)
    except (OSError,ValueError,configparser.Error) as error:return dict(result,message=str(error))
    result.update(config)
    if config['display_server'] == 'x11':
        installed = root / 'usr/bin/xrandr'
        if not installed.is_file() or not os.access(installed, os.X_OK):
            return dict(result, compositor='xorg', message='Install xrandr (xorg-xrandr on Arch) for SDDM X11 remote login.')
        return dict(result, compositor='xorg', supported=True,
                    message='SDDM X11 supports remote login on an existing display. Virtual login displays are unavailable.')
    if config['display_server']!='wayland':
        return dict(result,message='SDDM remote login requires standard X11 or a supported Wayland compositor.')
    if config['compositor']=='unknown':
        return dict(result,message='SDDM Wayland compositor is unknown or uses a wrapper. A verified compositor adapter is required.')
    return dict(result,supported=True,message='SDDM Wayland '+config['compositor']+' adapter is experimental; physical/headless live validation is required.')


def require_manager(expected=None):
    result=manager_capabilities()
    if not result['supported']:raise RuntimeError(result['message'])
    if expected and result['manager']!=expected:
        raise RuntimeError('Display manager changed after enrollment. Revalidate remote-login setup.')
    return result
