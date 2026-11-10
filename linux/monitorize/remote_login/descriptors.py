"""Root-written session descriptor for a fixed non-root desktop engine unit."""
import json
import os
from pathlib import Path
import re
import secrets
import stat
from .settings import validate_descriptor

ROOT=Path('/run/monitorize-remote-login')


class DescriptorStore:
    def __init__(self,settings,output_finder,adopter,*,root=ROOT,owner_uid=0,session_environment=None):
        self.session_environment=session_environment
        self.settings=settings;self.output_finder=output_finder;self.adopter=adopter
        self.root=Path(root);self.owner_uid=owner_uid

    def _directory(self,path,mode,gid=None):
        try:
            path.mkdir(mode=mode)
            os.chmod(path,mode)
            if os.geteuid()==0 and gid is not None:os.chown(path,0,gid)
        except FileExistsError:pass
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.owner_uid or info.st_mode & 0o022:
            raise ValueError('Unsafe remote-login runtime directory.')
        if gid is not None and info.st_gid!=gid:raise ValueError('Remote-login runtime group changed.')

    def write(self,caller,generation,descriptor):
        if caller['uid']!=self.settings.uid or not re.fullmatch(r'[0-9a-f]{32}',generation):
            raise PermissionError('Invalid descriptor owner/generation.')
        expected=self.output_finder()
        values=validate_descriptor(descriptor,self.settings,expected)
        # Resolve and validate trusted session metadata before compositor operations.
        if self.session_environment is not None:
            actual,environment=self.session_environment(caller['uid'])
            if actual!=caller['session']:
                raise PermissionError('The selected desktop session changed.')
            from .session_context import desktop_context
            context = desktop_context(environment)
            if context['desktop'] == 'X-Cinnamon' and values['capture'] != 'kms':
                raise ValueError('Cinnamon remote login requires KMS capture.')
            if context.get('session_type') == 'x11':
                values.pop('wayland_display', None)
            values.update(context)
        # Adopter rechecks active session, generation and DRM identity under its lock.
        adopted=self.adopter(caller['uid'],caller['session'])
        # Physical greeter capture never activates the reserved VKMS connector.
        # Virtual-0 is a listener-only placeholder until authenticated prepare
        # resolves the exact DRM target; it must never be treated as a live output.
        pending=(expected=='Virtual-0' and adopted.get('protected') is True
            and adopted.get('owner_kind')=='remote-login' and adopted.get('schema_version')==2
            and type(adopted.get('uid')) is int and adopted['uid']==caller['uid']
            and adopted.get('phase')=='desktop' and adopted.get('session')==caller['session']
            and adopted.get('state') in ('reserved','pending')
            and not any(key in adopted for key in ('connector','card','connector_id')))
        if adopted.get('connector')!=expected and not pending:
            raise RuntimeError('Boot output changed during adoption.')
        self._directory(self.root,0o755)
        directory=self.root/str(self.settings.uid)
        self._directory(directory,0o750,self.settings.gid)
        values.update(uid=self.settings.uid,session=caller['session'],generation=generation)
        temporary=directory/('.desktop.'+secrets.token_hex(8))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
            with os.fdopen(fd,'w') as stream:
                os.fchmod(stream.fileno(),0o640)
                if os.geteuid()==0:os.fchown(stream.fileno(),0,self.settings.gid)
                json.dump(values,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,directory/'desktop.json')
        finally:
            if temporary.exists():temporary.unlink()
        return values


def read_descriptor(path,uid,*,owner_uid=0):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd) as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_size>16384:
                raise ValueError()
            result=json.load(source)
        if result['uid']!=uid or not isinstance(result['session'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',result['session']):raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{32}',result['generation']):raise ValueError()
        return result
    except (OSError,KeyError,TypeError,ValueError):
        raise ValueError('Desktop descriptor is unsafe or does not match its account.') from None
