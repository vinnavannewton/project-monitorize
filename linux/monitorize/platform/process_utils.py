"""Shared subprocess helpers for the GUI backend."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QProcess

LINUX_DIR = str(Path(__file__).resolve().parents[2])


def gst_has_element(name):
    return shutil.which("gst-inspect-1.0") is not None and subprocess.run(
        ["gst-inspect-1.0", name], capture_output=True
    ).returncode == 0


def kill_patterns(*patterns):
    """Best-effort cleanup for Monitorize-owned orphan processes.

    This intentionally does not call broad `pkill -f`: a matching command line
    must also contain this checkout's linux directory, so unrelated GStreamer or
    Python processes are not killed.
    """
    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return
    compiled = [re.compile(pattern) for pattern in patterns]
    owned = []
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
        if LINUX_DIR not in cmdline:
            continue
        if any(pattern.search(cmdline) for pattern in compiled):
            owned.append(pid)
    _terminate_pids(owned)


def _terminate_pids(pids):
    pids = list(dict.fromkeys(pid for pid in pids if pid and pid != os.getpid()))
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    if pids:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = [pid for pid in pids if _pid_exists(pid)]
            if not alive:
                break
            time.sleep(0.05)
        for pid in pids:
            if _pid_exists(pid):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def unsafe_kill_patterns(*patterns):
    """Explicit legacy escape hatch for diagnostics; normal code must not use it."""
    for pattern in patterns:
        subprocess.run(
            ["pkill", "-9", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def stop_processes(*processes, timeout_ms=3000):
    clean = True
    for process in processes:
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            continue
        process.terminate()
        if not process.waitForFinished(timeout_ms):
            clean = False
            process.kill()
    return clean


def kill_tracked_pids(pids):
    for pid in list(pids):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    pids.clear()
