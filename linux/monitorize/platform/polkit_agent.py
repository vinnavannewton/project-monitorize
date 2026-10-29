"""Start a known desktop authentication agent only after an explicit agent failure."""
import os
from pathlib import Path
import stat
import subprocess
import time

MARKERS=('no authentication agent','no polkit authentication agent',
         'authentication agent is not available','authentication agent not found',
         'cannot register authentication agent','failed to register authentication agent',
         'error creating textual authentication agent')
AGENTS=(
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


def agent_missing(detail):
    return any(marker in str(detail).lower() for marker in MARKERS)


def start_agent():
    """No elevated process, arbitrary command, package installation or duplicate agent."""
    if os.geteuid()==0:return False
    choices=[]
    for name in AGENTS:
        path=Path(name)
        try:
            info=path.stat()
            if info.st_uid==0 and not info.st_mode & 0o022 and stat.S_ISREG(info.st_mode) and os.access(path,os.X_OK):
                choices.append(path.resolve())
        except OSError:pass
    if not choices:return False
    for process in list(Path('/proc').glob('[0-9]*'))[:8192]:
        try:
            if process.stat().st_uid==os.getuid() and (process/'exe').resolve() in choices:
                return False  # An existing agent must not be duplicated/replaced.
        except OSError:pass
    desktop=os.environ.get('XDG_CURRENT_DESKTOP','').lower()
    preferred='kde' if 'kde' in desktop else 'mate' if 'mate' in desktop else 'lxqt' if 'lxqt' in desktop else 'hypr' if 'hypr' in desktop else 'gnome'
    choices.sort(key=lambda path: preferred not in str(path))
    try:
        subprocess.Popen([str(choices[0])],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
        return True
    except OSError:return False


def run_authorized(command,**kwargs):
    """Worker-only: retry once for a missing agent, never a denial/cancellation."""
    result=subprocess.run(command,**kwargs)
    detail=str(result.stderr or '')+str(result.stdout or '')
    if result.returncode not in (0,126) and agent_missing(detail) and start_agent():
        time.sleep(.5)  # Agent registration; pkexec itself verifies the registered agent.
        result=subprocess.run(command,**kwargs)
    return result
