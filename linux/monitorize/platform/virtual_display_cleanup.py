"""Remove Monitorize virtual outputs without touching physical displays."""

import json
import os
import re
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from monitorize.platform.display_controller import DisplayController
from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient


def _is_display_owner(args, include_independent=False):
    """Match helper commands, never arbitrary text in a shell command line."""
    if not args:
        return False
    if Path(args[0]).name == 'monitorize-kde-virtual-output':
        return include_independent or len(args) < 2 or not args[1].startswith("Monitorize-Independent-")
    if len(args) > 6 and args[1:3] == ['-m', 'monitorize.streaming.headless_virtual_display']:
        if not include_independent and args[6].startswith("independent-"):
            return False
    return any(
        args[index:index + 2] == ['-m', 'monitorize.streaming.headless_virtual_display']
        for index in range(1, len(args) - 1)
    ) and Path(args[0]).name.startswith('python')


def _display_owners(include_independent=False):
    owners = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            args = entry.joinpath('cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if _is_display_owner(args, include_independent):
                owners.append(int(entry.name))
        except OSError:
            continue
    return owners


def _close_display_owners(include_independent=False):
    """Close orphaned GNOME/KDE sessions and allow VKMS owners to clean up."""
    find_owners = lambda: _display_owners(True) if include_independent else _display_owners()
    owners = set(find_owners())
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
        remaining = owners.intersection(find_owners())
        if not remaining:
            return len(owners), errors
        time.sleep(0.1)
    remaining = owners.intersection(find_owners())
    if remaining:
        errors.append('Some virtual display helpers did not stop; try closing their Monitorize session.')
    return len(owners - remaining), errors


def remove_virtual_displays(desktop, *, include_vkms=True, include_independent=False):
    """Clean outputs, optionally leaving the independently managed VKMS host alone.

    Source uninstall restores its own layout and remote-login reservation first;
    it must not depend on standalone VKMS readiness or remove its other outputs.
    The Settings cleanup action retains the full VKMS cleanup by default.
    """
    removed, errors = _close_display_owners(True) if include_independent else _close_display_owners()
    removed_independent_ids = []
    removed_managed_ids = []
    try:
        removed += DisplayController(desktop).remove_stagnant_virtual_displays()
    except Exception as exc:
        errors.append(f'Compositor cleanup failed: {exc}')

    legacy_recovery = Path.home() / '.config' / 'monitorize' / 'stock-vkms-recovery.json'
    if legacy_recovery.exists():
        errors.append(
            'A legacy stock VKMS recovery record remains. This version will not '
            'modify stock VKMS outputs. Disable the output in desktop display '
            'settings, then remove the stale recovery record after verifying it.'
        )

    command = shutil.which('monitorize-vkms') if include_vkms else None
    if command:
        # The CLI also routes reserved remote-login outputs through their owner.
        # Ordinary remove_all deliberately cannot remove those protected outputs.
        try:
            completed = subprocess.run([command, 'remove', '--managed', '--json'], capture_output=True,
                                       text=True, timeout=120, check=False)
            if not include_independent and completed.returncode == 2 and '--managed' in completed.stderr:
                completed = subprocess.run([command, 'remove', '--json'], capture_output=True,
                                           text=True, timeout=120, check=False)
            result = json.loads(completed.stdout)
            if not isinstance(result, dict):raise ValueError('Invalid VKMS cleanup response')
            removed += int(bool(result.get('removed')))
            if include_independent:
                outcomes = result.get('results', {})
                removed_managed_ids = [
                    display_id for display_id in (1, 2)
                    if outcomes.get(f'mon{display_id}', {}).get('changed') is True
                ]
            if completed.returncode or not result.get('success', False):
                details = [str(item['error']) for item in result.get('results', {}).values()
                           if isinstance(item, dict) and item.get('error')]
                errors.append('VKMS cleanup failed: ' + ' '.join([str(result.get('message') or 'Removal failed'), *details]))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append(f'VKMS cleanup failed: {exc}')
    elif include_vkms:
        client = MonitorizeVkmsClient()
        if client.is_available():
            try:
                status = client.get_status()
                connected = any(entry.get('connector_connected') for entry in status.get('displays', {}).values())
                if connected:
                    result = client.remove_all()
                    outcomes = result.get('results', {})
                    removed += sum(bool(item.get('changed')) for item in outcomes.values())
                    for display, item in outcomes.items():
                        if item.get('error'):
                            errors.append(f"{display}: {item['error']}")
                    if not result.get('success', False) and not any(item.get('error') for item in outcomes.values()):
                        errors.append(result.get('message') or 'VKMS displays could not be removed')
            except Exception as exc:
                errors.append(f'VKMS cleanup failed: {exc}')
    if command and include_independent:
        from monitorize.platform.monitorize_vkms_cli import MonitorizeVkmsClient as NamedVkmsClient
        client = NamedVkmsClient(executable=command)
        try:
            displays = client._run_cli(["list", "--json"], timeout=120)["displays"]
            for display in displays:
                name = display.get("name", "")
                match = re.fullmatch(r"Monitorize Display ([1-9][0-9]*)", name)
                if not match or display.get("managed") is not False:
                    continue
                try:
                    client._run_cli(["remove", name, "--json"], timeout=120)
                    removed += 1
                    removed_independent_ids.append(int(match[1]))
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        except Exception as exc:
            errors.append(f"Could not check standalone VKMS displays: {exc}")
    if include_independent:
        result = {"removed_independent_ids": removed_independent_ids, "removed_managed_ids": removed_managed_ids}
    else:
        result = {}
    if errors:
        message = ('Removed virtual displays, but some cleanup steps failed. ' if removed
                   else 'Could not complete virtual display cleanup. ')
        return {**result, 'success': False, 'message': message + ' '.join(errors)}
    return {**result, 'success': True, 'message': 'Removed virtual displays' if removed else 'No virtual displays found'}


if __name__ == '__main__':
    try:
        result = remove_virtual_displays(sys.argv[1] if len(sys.argv) > 1 else '',
                                         include_independent='--include-independent' in sys.argv[2:])
    except Exception as exc:
        result = {'success': False, 'message': f'Virtual display cleanup failed: {exc}'}
    print('MONITORIZE_CLEANUP ' + json.dumps(result), flush=True)
    sys.exit(0 if result['success'] else 1)
