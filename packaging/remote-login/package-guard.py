#!/usr/bin/python3 -I
"""Pre-transaction guard; no app/host imports and no enrollment mutations."""
import json
import os
from pathlib import Path
import stat
import sys


def _read(path,root,owner_uid):
    try:
        # Inspect existing ancestors even if the final file is absent.
        for parent in (path.parent,*path.parent.parents):
            if not parent.is_relative_to(root):break
            try:info=parent.lstat()
            except FileNotFoundError:continue
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022:
                raise ValueError('Unsafe remote-login state directory; repair before package changes.')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    except FileNotFoundError:return None
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_nlink!=1 or info.st_size>65536:
            raise ValueError('Unsafe remote-login state; repair before package changes.')
        raw=source.read(65537)
    if len(raw)>65536:raise ValueError('Remote-login state exceeds its size bound.')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Invalid remote-login state.')
    return value


def assert_update_safe(root=Path('/'), *, owner_uid=0):
    root=Path(root).absolute()
    settings=_read(root/'etc/monitorize/remote-login.json',root,owner_uid)
    lease=_read(root/'run/monitorize-vkms/primary.json',root,owner_uid)
    if settings is not None and type(settings.get('enabled')) is not bool:
        raise ValueError('Invalid remote-login enablement state.')
    if (settings and settings['enabled']) or (lease and lease.get('protected') is True):
        raise RuntimeError('Disable remote login in Monitorize Settings and release its reservation before updating or removing system components. Pairing is retained.')


if __name__=='__main__':
    try:
        if os.geteuid()!=0:raise PermissionError('Package guard requires root.')
        assert_update_safe()
    except (OSError,RuntimeError,ValueError) as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
