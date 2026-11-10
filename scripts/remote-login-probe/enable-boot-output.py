#!/usr/bin/python3 -I
"""EXPERIMENT ONLY: connect fixed mon1 before the display manager starts.

Install as root-owned code before use. No CLI overrides; no live cleanup.
Remove the opt-in unit and reboot to restore normal disconnected bootstrap.
"""
import json
import os
from pathlib import Path
import sys

CONNECTOR = Path('/sys/kernel/config/monitorize-vkms/monitorize/connectors/connector0')
LOCK = Path('/run/lock/monitorize-vkms.lock')
STATE = Path('/run/monitorize-vkms')


def enable_output(connector, state, sessions, generate_edid):
    """Caller holds lifecycle lock; refuse any existing session/output owner."""
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise RuntimeError('Could not verify logind sessions.')
    if any(s.get('class') != 'manager' for s in sessions):
        raise RuntimeError('Probe must run before login; a session is already present.')
    for lease in ('primary.json', 'mon2.json'):
        if (state / lease).exists() or (state / lease).is_symlink():
            raise RuntimeError('An existing VKMS lease prevents the boot probe.')
    for filename in ('status', 'enabled', 'dynamic', 'edid', 'edid_enabled'):
        path = connector / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Trusted dynamic VKMS topology is unavailable: ' + filename)
    if ((connector / 'status').read_text().strip() != '2' or
            (connector / 'enabled').read_text().strip() != '1' or
            (connector / 'dynamic').read_text().strip() != '1'):
        raise RuntimeError('Probe requires a registered, disconnected dynamic mon1.')
    edid = generate_edid(1920, 1080, 60, 'mon1')
    if len(edid) != 128 or sum(edid) % 256 or edid[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00':
        raise RuntimeError('Invalid generated boot EDID.')
    (connector / 'edid').write_bytes(edid)
    (connector / 'edid_enabled').write_text('1')
    (connector / 'status').write_text('1')
    if (connector / 'status').read_text().strip() != '1':
        raise RuntimeError('VKMS connector did not connect.')
    return dict(experimental=True, slot='mon1', mode='1920x1080@60', connected=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit('This experiment must be installed and run by the administrator.')
    import fcntl
    import stat
    # Lock file is created by the installed bootstrap. Never follow a symlink.
    descriptor = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeError('VKMS lifecycle lock must be root-owned and mode 0600.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Only import the root-installed host package, never this checkout.
        sys.path.insert(0, '/usr/lib/monitorize-vkms')
        from monitorize_vkms.boot_runtime import local_sessions
        from monitorize_vkms.edid import generate_edid
        print(json.dumps(enable_output(CONNECTOR, STATE, local_sessions(), generate_edid)))
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
