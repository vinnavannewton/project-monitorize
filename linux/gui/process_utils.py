"""Shared subprocess helpers for the GUI backend."""

import os
import shutil
import subprocess

from PyQt6.QtCore import QProcess


def gst_has_element(name):
    return shutil.which("gst-inspect-1.0") is not None and subprocess.run(
        ["gst-inspect-1.0", name], capture_output=True
    ).returncode == 0


def kill_patterns(*patterns):
    for pattern in patterns:
        subprocess.run(
            ["pkill", "-9", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def stop_processes(*processes):
    for process in processes:
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            continue
        process.terminate()
        if not process.waitForFinished(3000):
            process.kill()


def kill_tracked_pids(pids):
    for pid in list(pids):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    pids.clear()

