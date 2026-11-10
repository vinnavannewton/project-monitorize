"""Root policy and bounded desktop descriptor validation; no secret retrieval."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import pwd
import re
import stat

SETTINGS=Path('/etc/monitorize/remote-login.json')


@dataclass(frozen=True)
class Settings:
    enabled: bool
    uid: int
    gid: int
    username: str
    home: str
    profile: str
    encoder: str
    adapter: str
    manager: str = "plasmalogin.service"
    create_login_virtual_display: bool = False
    display_idle_timeout_seconds: int = 20

    @property
    def login_primary_display(self):return not self.create_login_virtual_display

    @property
    def directory(self):return Path('/var/lib/monitorize/remote-login')/str(self.uid)
    @property
    def runtime(self):return Path('/run/monitorize-remote-login')/str(self.uid)


def validate_idle_timeout(value):
    if type(value) is not int or not 1 <= value <= 600:
        raise ValueError('Display removal timeout must be an integer from 1 to 600 seconds.')
    return value


def read_settings(path=SETTINGS,*,owner_uid=0,account_lookup=pwd.getpwuid):
    path=Path(path)
    try:
        parent=path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=owner_uid or parent.st_mode & 0o022:
            raise ValueError()
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd) as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_size>4096:
                raise ValueError()
            values=json.load(source)
        if not isinstance(values,dict) or values.get('schema_version')!=1:raise ValueError()
        uid=values['uid'];gid=values['gid'];enabled=values['enabled']
        if type(uid) is not int or uid<=0 or type(gid) is not int or gid<0 or type(enabled) is not bool:raise ValueError()
        account=account_lookup(uid)
        if (account.pw_uid!=uid or account.pw_gid!=gid or account.pw_name!=values['username'] or
            account.pw_dir!=values['home']):raise ValueError()
        from .display_managers import MANAGERS
        if values['manager'] not in MANAGERS:raise ValueError()
        timeout=validate_idle_timeout(values.get('display_idle_timeout_seconds',20))
        # Missing positive field migrates every legacy enrollment to physical capture.
        virtual=values.get('create_login_virtual_display',False)
        if type(virtual) is not bool:raise ValueError()
        if 'login_primary_display' in values and type(values['login_primary_display']) is not bool:raise ValueError()
        encoder=values['encoder'];adapter=values['adapter'];profile=values['profile']
        if encoder not in ('software','vaapi','nvenc'):raise ValueError()
        if not isinstance(adapter,str) or (adapter and not re.fullmatch(r'/dev/dri/renderD[0-9]+',adapter)):raise ValueError()
        if not isinstance(profile,str) or not Path(profile).is_absolute() or '\n' in profile or '\r' in profile:raise ValueError()
        # Compatibility only: retire an existing experimental enrollment without
        # losing its original encoder. There is no persistent runtime anymore.
        mode=values.get('transition_mode','reconnect')
        if mode not in ('reconnect','seamless-experimental'):raise ValueError()
        if mode=='seamless-experimental':
            encoder=values.get('reconnect_encoder','software')
            adapter=values.get('reconnect_adapter','')
            if encoder not in ('software','vaapi','nvenc'):raise ValueError()
            if not isinstance(adapter,str) or (adapter and not re.fullmatch(r'/dev/dri/renderD[0-9]+',adapter)):raise ValueError()
        return Settings(enabled,uid,gid,account.pw_name,account.pw_dir,profile,encoder,adapter,values['manager'],virtual,timeout)
    except (OSError,ValueError,TypeError,KeyError):
        raise ValueError('Remote-login configuration is unsafe, unsupported or no longer matches its account.') from None


def validate_display_mode(mode):
    if (not isinstance(mode,(list,tuple)) or len(mode)!=3 or type(mode[0]) is not int or type(mode[1]) is not int
        or not 1<=mode[0]<=4095 or not 1<=mode[1]<=4095 or type(mode[2]) not in (int,float)
        or not math.isfinite(mode[2]) or not 24<=mode[2]<=240):
        raise ValueError('Display mode must be 1–4095 pixels and 24–240 Hz.')
    return list(mode)


def validate_descriptor(values,settings,expected_output):
    allowed={'capture','output_name','pipewire_node','wayland_display','display','stream_options','client_display_mode','display_mode','mirror_output'}
    if not isinstance(values,dict) or set(values)-allowed:raise ValueError('Unsupported desktop descriptor fields.')
    capture=values.get('capture');output=values.get('output_name')
    if capture not in ('kms','kwin') or output!=expected_output or not isinstance(output,str) or not re.fullmatch(r'Virtual-[0-9]+',output):
        raise ValueError('Desktop capture must target the reserved virtual output.')
    result={'capture':capture,'output_name':output}
    if 'mirror_output' in values:
        mirror = values['mirror_output']
        if (capture != 'kms' or not isinstance(mirror, str) or mirror == output
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', mirror)):
            raise ValueError('Mirror requires a valid X11 output distinct from the reserved display.')
        result['mirror_output'] = mirror
    if capture=='kwin':
        node=values.get('pipewire_node')
        if type(node) is not int or not 0<node<=2**32-1:raise ValueError('KWin capture requires a valid PipeWire node.')
        result['pipewire_node']=node
    elif 'pipewire_node' in values:raise ValueError('KMS capture does not use a PipeWire node.')
    for name,pattern in (('wayland_display',r'wayland-[0-9]+'),('display',r':[0-9]+(?:\.[0-9]+)?')):
        if name in values:
            value=values[name]
            if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ValueError('Invalid graphical session endpoint.')
            result[name]=value
    if 'client_display_mode' in values:
        if type(values['client_display_mode']) is not bool:raise ValueError('Invalid client display setting.')
        result['client_display_mode']=values['client_display_mode']
    if 'display_mode' in values:result['display_mode']=validate_display_mode(values['display_mode'])
    if 'stream_options' in values:
        options=values['stream_options']
        enums={'encoder':('', 'nvenc', 'vaapi', 'vulkan', 'software'),
               'hevc_mode':('0','1','2'), 'av1_mode':('0','1','2'),
               'native_pen_touch':('enabled','disabled'), 'stream_audio':('enabled','disabled')}
        if not isinstance(options,dict) or set(options)-set(enums)-{'adapter_name'}:
            raise ValueError('Unsupported desktop streaming options.')
        for key,value in options.items():
            if key=='adapter_name':
                if not isinstance(value,str) or (value and not re.fullmatch(r'/dev/dri/renderD[0-9]{1,5}',value)):
                    raise ValueError('Invalid encoding adapter.')
            elif value not in enums[key]:raise ValueError('Invalid desktop streaming option.')
        result['stream_options']=dict(options)
    return result
