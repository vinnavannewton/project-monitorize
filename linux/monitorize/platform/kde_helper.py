"""Lifecycle helpers for Monitorize's native KWin virtual-output owner."""

import json
import os
import select
import shutil
import sys
import time
from pathlib import Path


HELPER_NAME = "monitorize-kde-virtual-output"
HELPER_EVENT_TIMEOUT = 10


def find_helper():
    override = os.environ.get("MONITORIZE_KDE_HELPER", "").strip()
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        override,
        str(Path(sys.executable).with_name(HELPER_NAME)),
        str(repo_root / "venv" / "bin" / HELPER_NAME),
        str(repo_root / "linux" / "venv" / "bin" / HELPER_NAME),
        str(repo_root / "native" / "kde_virtual_output" / "build" / HELPER_NAME),
        str(repo_root / "linux" / "native" / "kde_virtual_output" / "build" / HELPER_NAME),
        "/usr/local/bin/" + HELPER_NAME,
        "/usr/bin/" + HELPER_NAME,
    ]
    from_path = shutil.which(HELPER_NAME)
    if from_path:
        candidates.append(from_path)
    return next(
        (
            path
            for path in candidates
            if path and os.path.isfile(path) and os.access(path, os.X_OK)
        ),
        "",
    )


def read_helper_event(process, expected, timeout=HELPER_EVENT_TIMEOUT):
    deadline = time.monotonic() + timeout
    last_line = ""
    while time.monotonic() < deadline:
        ready, _write, _error = select.select(
            [process.stdout], [], [], max(0, deadline - time.monotonic())
        )
        if not ready:
            break
        line = process.stdout.readline()
        if not line:
            break
        last_line = line.strip()
        try:
            event = json.loads(last_line)
        except ValueError:
            continue
        if event.get("event") == "error":
            raise RuntimeError(event.get("message") or "KWin helper failed")
        if event.get("event") == expected:
            return event
    detail = last_line or f"helper exited with code {process.poll()}"
    raise RuntimeError(f"Timed out waiting for {expected}: {detail}")


def stop_process(process, timeout=2):
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except Exception:
        process.kill()
        process.wait()


def stop_helper(process):
    if not process or process.poll() is not None:
        return
    try:
        process.stdin.write("quit\n")
        process.stdin.flush()
        process.wait(timeout=2)
    except Exception:
        stop_process(process)
