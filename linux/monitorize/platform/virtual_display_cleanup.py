"""Remove Monitorize virtual outputs without touching physical displays."""

import json
import os
from pathlib import Path
import signal
import sys
import time

from monitorize.platform.display_controller import DisplayController
from monitorize.platform.monitorize_vkms_cli import MonitorizeVkmsClient


def _is_display_owner(args):
    """Match helper commands, never arbitrary text in a shell command line."""
    if not args:
        return False
    if Path(args[0]).name == 'monitorize-kde-virtual-output':
        return True
    return any(
        args[index:index + 2] == ['-m', 'monitorize.streaming.headless_virtual_display']
        for index in range(1, len(args) - 1)
    ) and Path(args[0]).name.startswith('python')


def _display_owners():
    owners = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            args = entry.joinpath('cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if _is_display_owner(args):
                owners.append(int(entry.name))
        except OSError:
            continue
    return owners


def _close_display_owners():
    """Close orphaned GNOME/KDE sessions and allow VKMS owners to clean up."""
    owners = set(_display_owners())
    errors = []
    for pid in owners:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f'Could not stop virtual display helper {pid}: {exc}')
    deadline = time.monotonic() + 20
    while owners and time.monotonic() < deadline:
        remaining = owners.intersection(_display_owners())
        if not remaining:
            return len(owners), errors
        time.sleep(0.1)
    remaining = owners.intersection(_display_owners())
    if remaining:
        errors.append('Some virtual display helpers did not stop; try closing their Monitorize session.')
    return len(owners - remaining), errors


def remove_virtual_displays(desktop):
    """Clean up every Monitorize method and report partial failures."""
    removed, errors = _close_display_owners()
    try:
        removed += DisplayController(desktop).remove_stagnant_virtual_displays()
    except Exception as exc:
        errors.append(f'Compositor cleanup failed: {exc}')

    client = MonitorizeVkmsClient()
    if client.is_available():
        try:
            status = client.get_status()
            topology = status.get('topology', {})
            connected = topology.get('connector0_connected') or any(
                entry.get('status') == 'connected'
                for entry in status.get('drm', {}).get('active_connectors', [])
            )
            if connected:
                result = client.remove_display()
                if result.get('success', False):
                    removed += 1
                else:
                    errors.append(result.get('message') or 'VKMS display could not be removed')
        except Exception as exc:
            errors.append(f'VKMS cleanup failed: {exc}')
    if errors:
        return {'success': False, 'message': 'Some virtual displays could not be removed. ' + ' '.join(errors)}
    return {'success': True, 'message': 'Removed virtual displays' if removed else 'No virtual displays found'}


if __name__ == '__main__':
    try:
        result = remove_virtual_displays(sys.argv[1] if len(sys.argv) > 1 else '')
    except Exception as exc:
        result = {'success': False, 'message': f'Virtual display cleanup failed: {exc}'}
    print('MONITORIZE_CLEANUP ' + json.dumps(result), flush=True)
    sys.exit(0 if result['success'] else 1)
