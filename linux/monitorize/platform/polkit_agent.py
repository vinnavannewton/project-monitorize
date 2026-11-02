"""Start a known desktop authentication agent only after an explicit agent failure."""
import os
import ctypes
from functools import lru_cache
from pathlib import Path
import stat
import subprocess
import threading
import time

MARKERS=('no authentication agent','no polkit authentication agent',
         'authentication agent is not available','authentication agent not found',
         'cannot register authentication agent','failed to register authentication agent',
         'error creating textual authentication agent')
AGENTS=(
    '/usr/libexec/kf6/polkit-kde-authentication-agent-1',
    '/usr/libexec/kf5/polkit-kde-authentication-agent-1',
    '/usr/lib64/libexec/polkit-kde-authentication-agent-1',
    '/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1',
    '/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1',
    '/usr/libexec/polkit-kde-authentication-agent-1',
    '/usr/lib/polkit-kde-authentication-agent-1',
    '/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1',
    '/usr/libexec/polkit-gnome-authentication-agent-1',
    '/usr/bin/lxqt-policykit-agent',
    '/usr/libexec/polkit-mate-authentication-agent-1',
    '/usr/lib/mate-polkit/polkit-mate-authentication-agent-1',
    '/usr/libexec/hyprpolkitagent',
    '/usr/lib/hyprpolkitagent/hyprpolkitagent',
)
_STARTED = {}
_START_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def login_library():
    library = ctypes.CDLL('libsystemd.so.0')
    library.sd_pid_get_session.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    library.sd_pid_get_owner_uid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    library.sd_uid_get_display.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    library.sd_session_get_uid.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
    library.sd_session_get_type.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    library.sd_session_is_active.argtypes = [ctypes.c_char_p]
    return library


def _take_string(value):
    if not value.value:
        return None
    libc = ctypes.CDLL(None)
    libc.free.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(value).decode('utf-8')
    finally:
        libc.free(value)


def login_session(pid=0):
    """Resolve the same logind subject as Polkit, including user-manager apps.

    POSIX getsid() has no relationship to logind sessions. Match upstream
    Polkit's process -> owner UID -> graphical Display fallback instead.
    """
    try:
        library = login_library()
        value = ctypes.c_void_p()
        if library.sd_pid_get_session(pid, ctypes.byref(value)) < 0:
            uid = ctypes.c_uint()
            if (library.sd_pid_get_owner_uid(pid, ctypes.byref(uid)) < 0
                    or uid.value != os.getuid()
                    or library.sd_uid_get_display(uid, ctypes.byref(value)) < 0):
                return None
        session = _take_string(value)
        if not session:
            return None
        encoded = session.encode('utf-8')
        uid = ctypes.c_uint()
        kind = ctypes.c_void_p()
        if (library.sd_session_get_uid(encoded, ctypes.byref(uid)) < 0
                or uid.value != os.getuid() or library.sd_session_is_active(encoded) <= 0
                or library.sd_session_get_type(encoded, ctypes.byref(kind)) < 0):
            return None
        return session if _take_string(kind) in ('wayland', 'x11') else None
    except (OSError, AttributeError, UnicodeError):
        return None


def known_agent_processes(choices):
    for process in list(Path('/proc').glob('[0-9]*'))[:8192]:
        try:
            # PR_SET_DUMPABLE=0 changes ownership of /proc/PID to root even
            # though the agent still runs as this user. Read its kernel UID
            # record instead of mistaking the proc directory owner for its UID.
            with (process/'status').open('r') as stream:
                rows = stream.read(65536).splitlines()
            uid = next(int(row.split()[2]) for row in rows if row.startswith('Uid:'))
            if uid != os.getuid():
                continue
            try:
                executable = (process/'exe').resolve(strict=True)
            except OSError:
                # Some native agents disable dumpability, hiding exe even from
                # their own UID. argv is only a conservative duplicate guard;
                # it never authorizes a privileged action or chooses a program.
                with (process/'cmdline').open('rb') as stream:
                    argv0 = stream.read(4096).split(b'\0', 1)[0].decode('utf-8')
                executable = Path(argv0)
            if executable in choices:
                yield executable, login_session(int(process.name))
        except (OSError, ValueError, UnicodeError, StopIteration, IndexError):
            continue


def agent_missing(detail):
    return any(marker in str(detail).lower() for marker in MARKERS)


def can_retry_agent(returncode, stdout, stderr):
    # pkexec's own authorization failure is 127. A helper response (even a
    # failed/partial one) means root work may have happened and must not replay.
    return returncode == 127 and not str(stdout or '').strip() and agent_missing(stderr)


def start_agent():
    with _START_LOCK:
        return _start_agent()


def _start_agent():
    """No elevated process, arbitrary command, package installation or duplicate agent."""
    if os.geteuid()==0:return False
    session = login_session()
    if not session:
        return False
    launched = _STARTED.get(session)
    if launched is not None and launched.poll() is None:
        return True  # Registration is checked by pkexec; do not spawn again.
    choices=[]
    for name in AGENTS:
        path=Path(name)
        try:
            info=path.stat()
            if info.st_uid==0 and not info.st_mode & 0o022 and stat.S_ISREG(info.st_mode) and os.access(path,os.X_OK):
                choices.append(path.resolve())
        except OSError:pass
    if not choices:return False
    elsewhere = set()
    for executable, agent_session in known_agent_processes(choices):
        if agent_session == session:
            return True
        elsewhere.add(executable)
    # Native singleton agents may replace an existing instance on the shared
    # user bus. Prefer another installed agent over disrupting another login.
    choices = [path for path in choices if path not in elsewhere]
    if not choices:return False
    desktop=os.environ.get('XDG_CURRENT_DESKTOP','').lower()
    preferred='kde' if 'kde' in desktop else 'mate' if 'mate' in desktop else 'lxqt' if 'lxqt' in desktop else 'hypr' if 'hypr' in desktop else 'gnome'
    choices.sort(key=lambda path: preferred not in str(path))
    try:
        _STARTED[session] = subprocess.Popen([str(choices[0])],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
        return True
    except OSError:return False


def run_authorized(command,**kwargs):
    """Worker-only: bounded registration retries, never a denial/cancellation."""
    for attempt in range(4):
        result = subprocess.run(command, **kwargs)
        if not can_retry_agent(result.returncode, result.stdout, result.stderr) or attempt == 3:
            return result
        if not start_agent():
            return result
        time.sleep(0.5 * (attempt + 1))
    return result
