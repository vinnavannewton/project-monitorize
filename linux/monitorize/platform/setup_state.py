"""Validated first-run receipts shared by the desktop and the installed helper.

A receipt records observations, not cancellation promises. A running receipt is
active only while its boot/process identity still exists; otherwise its started
actions have unknown outcomes and must be checked or safely retried.
"""

import errno
import json
import os
from pathlib import Path
import re
import stat
import time

PROTOCOL = 2
RECEIPTS = Path('/var/lib/monitorize/setup')
ACTIONS = frozenset(('input', 'firewall', 'vkms'))
REQUEST_ID = re.compile(r'[0-9a-f]{32}')
LIMIT = 65536


def process_start(pid):
    # comm may contain spaces and parentheses. Field 22 follows the last ')'.
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[19])


def process_identity():
    return {'pid': os.getpid(), 'start': process_start(os.getpid()),
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def process_alive(identity, *, owners=(0,)):
    """Return True/False for proven live/dead, None when inspection is denied."""
    try:
        if identity['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return False
        pid = identity['pid']
        if Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] in ('Z', 'X', 'x'):
            return False
        if process_start(pid) != identity['start']:
            return False
        return Path(f'/proc/{pid}').stat().st_uid in owners
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return None
        raise


def validate_receipt(value, uid):
    if not isinstance(value, dict) or value.get('protocol') != PROTOCOL:
        raise ValueError('The saved system setup receipt needs an updated helper.')
    if type(value.get('uid')) is not int or value['uid'] != uid:
        raise ValueError('The system setup receipt belongs to another account.')
    if not isinstance(value.get('request_id'), str) or not REQUEST_ID.fullmatch(value['request_id']):
        raise ValueError('Invalid system setup request identity.')
    if value.get('phase') not in ('running', 'finished'):
        raise ValueError('Invalid system setup phase.')
    identity = value.get('process')
    if (not isinstance(identity, dict) or type(identity.get('pid')) is not int
            or identity['pid'] <= 0 or type(identity.get('start')) is not int
            or identity['start'] < 0 or not isinstance(identity.get('boot'), str)
            or len(identity['boot']) != 36):
        raise ValueError('Invalid system setup process identity.')
    selected = value.get('selected')
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(name, str) or name not in ACTIONS for name in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('Invalid system setup action selection.')
    for field in ('actions', 'history'):
        actions = value.get(field)
        if not isinstance(actions, dict) or not set(actions).issubset(ACTIONS):
            raise ValueError('Invalid system setup action record.')
        for action in actions.values():
            if (not isinstance(action, dict) or action.get('state') not in
                    ('pending', 'started', 'succeeded', 'failed')
                    or type(action.get('success')) is not bool
                    or not isinstance(action.get('message'), str)):
                raise ValueError('Invalid system setup action outcome.')
            if action['success'] != (action['state'] == 'succeeded'):
                raise ValueError('Contradictory system setup action outcome.')
    if set(value['actions']) != set(selected):
        raise ValueError('System setup receipt omits a selected action.')
    if value['phase'] == 'finished' and any(action['state'] in ('pending', 'started')
                                           for action in value['actions'].values()):
        raise ValueError('System setup finished without authoritative action outcomes.')
    return value


def read_receipt(uid, *, directory=RECEIPTS, owner_uid=0):
    path = directory / (str(uid) + '.json')
    try:
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner_uid) or info.st_mode & 0o022:
                raise ValueError('Unsafe system setup receipt directory.')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
                or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > LIMIT):
            raise ValueError('Unsafe system setup receipt.')
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('System setup receipt exceeds its size limit.')
    return validate_receipt(json.loads(data), uid)


def new_receipt(uid, selected, request_id, previous=None):
    actions = {name: {'state': 'pending', 'success': False, 'message': 'Not started.'}
               for name in selected}
    value = {'protocol': PROTOCOL, 'uid': uid, 'request_id': request_id,
             'process': process_identity(), 'phase': 'running', 'selected': list(selected),
             'actions': actions, 'history': dict(previous.get('history', {})) if previous else {},
             'updated': time.time()}
    return validate_receipt(value, uid)


def observe_receipt(value):
    if value is None:
        return {'busy': False, 'interrupted': False, 'unknown_actions': [], 'completed_actions': {}}
    live = process_alive(value['process']) if value['phase'] == 'running' else False
    busy = value['phase'] == 'running' and live is not False
    unknown = [name for name, action in value['actions'].items()
               if action['state'] == 'started' and not busy]
    history = {**value['history'], **value['actions']}
    return {'busy': busy, 'interrupted': value['phase'] == 'running' and not busy,
            'unknown_actions': unknown, 'request_id': value['request_id'],
            'selected_actions': value['selected'],
            'request_succeeded': value['phase'] == 'finished' and all(
                action['success'] for action in value['actions'].values()),
            'result_message': ' '.join(action['message'] for action in value['actions'].values()
                                       if action['state'] != 'pending'),
            'completed_actions': {name: action['message'] for name, action in history.items()
                                  if action['state'] == 'succeeded'}}
