"""Bounded engine readiness evidence scoped to the current boot and unit PID."""
from monitorize.platform.sunshine_ports import sunshine_port_conflict

import json
import os
from pathlib import Path
import re
import stat
import time


def missing_library_error(text):
    match = re.search(
        rb': error while loading shared libraries: ([A-Za-z0-9_.+-]{1,128}): '
        rb'cannot open shared object file: No such file or directory(?:\r?$)',
        text, re.MULTILINE)
    if match:
        library = match[1].decode('ascii')
        return (f'Sunshine cannot start because {library} is missing. '
                'For a source install, rebuild Sunshine for this system and then run Repair remote login. '
                'For a native install, update Monitorize and its dependencies through the package manager.')
    return ''


def startup_failure(log,status):
    """Summarize fixed failure markers from this attempt, never arbitrary log text."""
    try:
        with Path(log).open('rb') as source:
            source.seek(0,os.SEEK_END)
            source.seek(max(0,source.tell()-32768))
            text=source.read(32768)
        if error := missing_library_error(text):return error
        conflict = sunshine_port_conflict(text.decode('utf-8', 'replace'))
        if conflict:return conflict
        if b'MONITORIZE_STRICT_SELECTION_FAILED: requested encoder [nvenc] is unavailable' in text:
            return ('NVIDIA NVENC is unavailable in the installed remote-login engine. '
                    'Source installs: rebuild with --cuda=on and redeploy remote-login service assets. '
                    'Native installs: install the CUDA-enabled Monitorize package. '
                    'Also check the NVIDIA driver and GPU access. See desktop-console.log.')
    except OSError:pass
    return f'Sunshine exited (status {status}). See desktop-console.log.'


def inspect_startup(log,offset,output):
    try:
        with Path(log).open('rb') as source:
            source.seek(offset);lines=source.read(131072).decode('utf-8','replace').splitlines()
        failed=any('MONITORIZE_STRICT_' in line for line in lines)
        listening=any('Configuration UI available' in line for line in lines)
        encoded=any('Found H.264 encoder:' in line for line in lines)
        targeted=any((f"Mapped '{output}'" in line or f'Streaming display: {output} ' in line) for line in lines)
        return dict(listening=listening and not failed,capture_ready=listening and encoded and targeted and not failed)
    except OSError:return dict(listening=False,capture_ready=False)


def write_health(path,values):
    path=Path(path);temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1:raise ValueError('Unsafe health file.')
        os.ftruncate(fd,0);os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as stream:
            fd=None;json.dump(values,stream)
        os.replace(temporary,path)
    finally:
        if fd is not None:os.close(fd)


def read_health(path,*,uid,pid,boot_id,now=None):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd) as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode & 0o077 or info.st_size>4096:return {}
            values=json.load(source)
        now=time.monotonic() if now is None else now
        if (type(values.get('pid')) is not int or values['pid']!=pid or values.get('boot_id')!=boot_id or
            type(values.get('time')) not in (int,float) or not 0<=now-values['time']<3):return {}
        return values
    except (OSError,ValueError,TypeError):return {}


def rotate_startup_log(path):
    """Only while holding identity lock and before the successor engine starts."""
    path=Path(path)
    try:info=path.lstat()
    except FileNotFoundError:return
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1:
        raise ValueError('Unsafe managed startup log.')
    os.replace(path,path.with_suffix(path.suffix+'.previous'))


class ListenerEvidence:
    """Incremental bounded startup scan; readiness belongs to one engine process."""
    def __init__(self):
        self.offset=0;self.pending=b'';self.listening=False
    def read(self,path):
        try:
            with Path(path).open('rb') as source:
                source.seek(self.offset);chunk=source.read(131072)
            self.offset+=len(chunk)
            lines=(self.pending+chunk).split(b'\n')
            self.pending=lines.pop()[-4096:]
            if any(b'Configuration UI available' in line for line in lines):self.listening=True
        except OSError:pass
        return self.listening
