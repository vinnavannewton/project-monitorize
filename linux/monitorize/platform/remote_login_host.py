"""Read-only compatibility discovery; never executes host code or requests Polkit."""
import ast
import json
import os
from pathlib import Path
import re
import stat

REQUIRED_PROTOCOLS = dict(boot_display=1, greeter_on_demand=1, idle_display=1)


def _read(path, root, owner_uid, maximum=262144):
    for item in (path.parent, *path.parent.parents):
        if not item.is_relative_to(root):
            break
        info=item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022:
            raise ValueError('Unsafe installed directory.')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_nlink!=1 or info.st_size>maximum:
            raise ValueError('Unsafe installed file.')
        data=source.read(maximum+1)
    if len(data)>maximum:raise ValueError('Installed file exceeds its size bound.')
    return data


def _constant(source,name):
    # Static parsing avoids importing privileged host implementation into the GUI.
    values=[]
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            values.append(ast.literal_eval(node.value))
    if len(values)!=1 or type(values[0]) is not int:return 0
    return values[0]


def host_readiness(root=Path('/'), *, owner_uid=0) -> dict:
    root=Path(root).absolute()
    protocols={}
    def result(reason,message):
        return dict(available=reason=='ready',reason=reason,message=message,protocols=protocols)
    assets=('usr/libexec/monitorize/remote-login/entry.py','usr/libexec/monitorize/monitorize-system-setup',
            'usr/lib/systemd/system/monitorize-vkms-boot-display.service',
            'usr/lib/monitorize-remote-login/monitorize/remote_login/display_managers.py')
    try:
        for name in assets:_read(root/name,root,owner_uid)
    except (OSError,ValueError):
        return result('missing_assets','Install the Monitorize remote-login service assets from a native package or the source deployment installer.')
    package=root/'usr/lib/monitorize-vkms/monitorize_vkms'
    if not (package/'helper.py').exists():
        return result('host_missing','Install monitorize-vkms 0.3.0 or newer on the host, then refresh setup.')
    try:
        helper=_read(package/'helper.py',root,owner_uid)
        executable=_read(root/'usr/libexec/monitorize-vkms/monitorize-vkms-helper',root,owner_uid)
        boot=_read(package/'boot_display.py',root,owner_uid)
        version=_read(root/'usr/lib/monitorize-vkms/VERSION',root,owner_uid,128).decode().strip()
        manifest=json.loads(_read(root/'usr/share/monitorize-vkms/capabilities.json',root,owner_uid,4096))
        if helper!=executable:
            raise ValueError('Installed helper copies differ.')
    except (OSError,ValueError):
        return result('host_incomplete','The VKMS host installation is incomplete or mixed. Reinstall or upgrade monitorize-vkms as one complete installation.')
    try:
        protocols=manifest['remote_login_protocols']
        declared=dict(boot_display=_constant(helper,'BOOT_DISPLAY_PROTOCOL'),
                      greeter_on_demand=_constant(boot,'GREETER_ON_DEMAND_PROTOCOL'),
                      idle_display=_constant(boot,'IDLE_DISPLAY_PROTOCOL'))
        if (type(manifest.get('schema_version')) is not int or manifest['schema_version']!=1
            or manifest.get('version')!=version or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version)
            or tuple(map(int,version.split('.')))<(0,3,0) or not isinstance(protocols,dict)
            or any(type(protocols.get(k)) is not int or protocols[k]!=v or declared[k]!=v for k,v in REQUIRED_PROTOCOLS.items())):
            raise ValueError()
    except (ValueError,TypeError,KeyError,SyntaxError):
        protocols={}
        return result('host_incompatible','Upgrade monitorize-vkms to a compatible release with boot-display, on-demand greeter, and idle-display protocol v1.')
    from monitorize.remote_login.capability import exact_kms_engine
    engine=root/'usr/libexec/monitorize/sunshine'
    try:
        # The engine can be large; validate trusted path metadata without copying it.
        for item in (engine,*engine.parents):
            if not item.is_relative_to(root):break
            info=item.lstat()
            if info.st_uid!=owner_uid or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):raise ValueError()
        if not engine.is_file() or not exact_kms_engine(engine):raise ValueError()
    except (OSError,ValueError):
        return result('engine_incompatible','Install the compatible Monitorize Sunshine engine with exact KMS output support.')
    return result('ready','Remote-login service assets and VKMS host protocols are installed. Display-manager live validation is separate.')
