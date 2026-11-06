"""Root-only, per-boot manual stop intent."""
import json
import os
from pathlib import Path
import re
import stat
from .root_files import RootFiles


class StopJournal:
    def __init__(self,uid,boot_id,*,files=None):
        self.uid=uid;self.boot_id=boot_id;self.files=files or RootFiles()
        self.relative='run/monitorize-remote-login/stop-state.json'

    def _load(self, relative):
        path=self.files._path(relative)
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd) as source:
                info=os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=self.files.owner_uid or info.st_mode & 0o077 or info.st_size>4096:
                    raise ValueError('Unsafe stop journal.')
                values=json.load(source)
        except FileNotFoundError:return None
        if values.get('uid')!=self.uid or values.get('boot_id')!=self.boot_id:return None
        return values

    def load(self):
        values = self._load(self.relative)
        session = values.get('session') if values else None
        if session is not None and (not isinstance(session,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',session)):
            raise ValueError('Invalid stop journal session.')
        return session

    def save(self,session):
        if session is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',session):raise ValueError('Invalid session.')
        self.files.write(self.relative,json.dumps({'uid':self.uid,'boot_id':self.boot_id,'session':session}),0o600)

    def load_boot_pause(self):
        values = self._load('run/monitorize-remote-login/boot-pause.json')
        return bool(values and values.get('paused') is True)

    def save_boot_pause(self, paused):
        self.files.write('run/monitorize-remote-login/boot-pause.json', json.dumps({
            'uid': self.uid, 'boot_id': self.boot_id, 'paused': paused,
        }), 0o600)
