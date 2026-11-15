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


def _remove_managed_vkms_with_cli(command, errors, *, allow_legacy=False):
    removed = 0
    removed_managed_ids = []
    # The CLI also routes reserved remote-login outputs through their owner.
    # Ordinary remove_all deliberately cannot remove those protected outputs.
    try:
        completed = subprocess.run([command, 'remove', '--managed', '--json'], capture_output=True,
                                   text=True, timeout=120, check=False)
        if allow_legacy and completed.returncode == 2 and '--managed' in completed.stderr:
            completed = subprocess.run([command, 'remove', '--json'], capture_output=True,
                                       text=True, timeout=120, check=False)
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError('Invalid VKMS cleanup response')
        removed = int(bool(result.get('removed')))
        outcomes = result.get('results', {})
        removed_managed_ids = [
            display_id for display_id in (1, 2)
            if outcomes.get(f'mon{display_id}', {}).get('changed') is True
        ]
        if completed.returncode or not result.get('success', False):
            details = [str(item['error']) for item in outcomes.values()
                       if isinstance(item, dict) and item.get('error')]
            errors.append('VKMS cleanup failed: ' + ' '.join([str(result.get('message') or 'Removal failed'), *details]))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        errors.append(f'VKMS cleanup failed: {exc}')
    return removed, removed_managed_ids


def _remove_settings_vkms(command, errors):
    client = MonitorizeVkmsClient()
    removed = 0
    managed_ids = []
    independent_ids = []
    pending = []
    try:
        try:
            slots = client.get_status()["displays"]
            if any(slot.get("protected") for slot in slots.values()):
                removed, managed_ids = _remove_managed_vkms_with_cli(command, errors)
            elif any(slot.get("connector_connected") for slot in slots.values()):
                pending.append((None, ""))
        except Exception as exc:
            errors.append(f"VKMS cleanup failed: {exc}")

        try:
            for display in client.list_named_displays():
                name = display.get("name", "")
                match = re.fullmatch(r"Monitorize Display ([1-9][0-9]*)", name)
                if match and display.get("managed") is False:
                    pending.insert(0, (int(match[1]), name))
        except Exception as exc:
            errors.append(f"Could not check standalone VKMS displays: {exc}")

        # Removing an interior GNOME output can leave a gap in the layout.
        # Retry only after another output was removed and the layout changed.
        while pending:
            retry = []
            failures = []
            previous_count = removed
            for display_id, name in pending:
                if display_id is not None:
                    try:
                        client.remove_named_display(name)
                        independent_ids.append(display_id)
                        removed += 1
                    except Exception as exc:
                        message = f"{name}: {exc}"
                        if "GNOME did not confirm removal" in str(exc):
                            retry.append((display_id, name))
                            failures.append(message)
                        else:
                            errors.append(message)
                    continue

                try:
                    result = client.remove_all()
                    changed = [
                        index for index in (1, 2)
                        if result["results"][f"mon{index}"].get("changed") is True
                    ]
                    managed_ids.extend(changed)
                    removed += len(changed)
                    if not result["success"]:
                        details = [str(item["error"]) for item in result["results"].values()
                                   if item.get("error")]
                        message = "VKMS cleanup failed: " + " ".join(
                            details or [result.get("message") or "Managed removal failed."]
                        )
                        if details and all("GNOME did not confirm removal" in item for item in details):
                            retry.append((None, ""))
                            failures.append(message)
                        else:
                            errors.append(message)
                except Exception as exc:
                    errors.append(f"VKMS cleanup failed: {exc}")
            if removed == previous_count:
                errors.extend(failures)
                break
            pending = retry
    finally:
        client.close()
    return removed, managed_ids, independent_ids


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
    if command and include_independent:
        count, removed_managed_ids, removed_independent_ids = _remove_settings_vkms(command, errors)
        removed += count
    elif command:
        count, _ = _remove_managed_vkms_with_cli(command, errors, allow_legacy=True)
        removed += count
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
