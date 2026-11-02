"""Dedicated uinput access that coexists with other applications' udev rules.

The installed root entry point accepts no paths or commands. Only the real
kernel uinput node is changed. The ACL mask may need widening for our group;
normalize unrelated masked entries first so their effective access cannot grow.
"""
import ctypes
import fcntl
from functools import lru_cache
import grp
import os
from pathlib import Path
import pwd
import stat
import sys

PROTOCOL = 1
USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER = 1, 2, 4, 8, 16, 32
READ_WRITE = 6
ACCESS = 0x8000


@lru_cache(maxsize=1)
def library():
    lib = ctypes.CDLL('libacl.so.1', use_errno=True)
    pointer = ctypes.c_void_p
    signatures = {
        'acl_get_fd': ([ctypes.c_int], pointer),
        'acl_get_file': ([ctypes.c_char_p, ctypes.c_int], pointer),
        'acl_get_entry': ([pointer, ctypes.c_int, ctypes.POINTER(pointer)], ctypes.c_int),
        'acl_get_tag_type': ([pointer, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
        'acl_get_qualifier': ([pointer], pointer),
        'acl_get_permset': ([pointer, ctypes.POINTER(pointer)], ctypes.c_int),
        'acl_get_perm': ([pointer, ctypes.c_int], ctypes.c_int),
        'acl_from_text': ([ctypes.c_char_p], pointer),
        'acl_valid': ([pointer], ctypes.c_int),
        'acl_set_fd': ([ctypes.c_int, pointer], ctypes.c_int),
        'acl_free': ([pointer], ctypes.c_int),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, result
    return lib


def _error(operation):
    return OSError(ctypes.get_errno(), operation + ': ' + os.strerror(ctypes.get_errno()))


def _rows(acl):
    lib = library()
    if not acl:
        raise _error('Cannot read input ACL')
    try:
        rows = []
        entry = ctypes.c_void_p()
        selector = 0  # ACL_FIRST_ENTRY, then ACL_NEXT_ENTRY.
        while True:
            result = lib.acl_get_entry(acl, selector, ctypes.byref(entry))
            if result == 0:
                return rows
            if result < 0:
                raise _error('Cannot enumerate input ACL')
            selector = 1
            tag = ctypes.c_int()
            permissions = ctypes.c_void_p()
            if (lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0
                    or lib.acl_get_permset(entry, ctypes.byref(permissions)) != 0):
                raise _error('Cannot inspect input ACL')
            identifier = None
            if tag.value in (USER, GROUP):
                qualifier = lib.acl_get_qualifier(entry)
                if not qualifier:
                    raise _error('Cannot identify input ACL entry')
                try:
                    identifier = ctypes.cast(qualifier, ctypes.POINTER(ctypes.c_uint))[0]
                finally:
                    lib.acl_free(qualifier)
            bits = 0
            for bit in (4, 2, 1):
                result = lib.acl_get_perm(permissions, bit)
                if result < 0:
                    raise _error('Cannot inspect input ACL permission')
                if result:
                    bits |= bit
            rows.append((tag.value, identifier, bits))
            if len(rows) > 1024:
                raise ValueError('The input ACL has too many entries to repair safely.')
    finally:
        lib.acl_free(acl)


def read_acl(path=None, *, fd=None):
    lib = library()
    return _rows(lib.acl_get_fd(fd) if fd is not None else lib.acl_get_file(os.fsencode(path), ACCESS))


def group_has_access(rows, owning_gid, gid):
    mask = next((bits for tag, _, bits in rows if tag == MASK), 7)
    bits = 0
    for tag, identifier, permissions in rows:
        if (tag == GROUP_OBJ and owning_gid == gid) or (tag == GROUP and identifier == gid):
            bits |= permissions & mask
    return bits & READ_WRITE == READ_WRITE


def grant_rows(rows, gid, allowed_uids=()):
    mask = next((bits for tag, _, bits in rows if tag == MASK), 7)
    result = []
    for tag, identifier, bits in rows:
        if tag == MASK or (tag == GROUP and identifier == gid):
            continue
        if tag in (USER, GROUP_OBJ, GROUP):
            bits &= mask
        if tag == USER and identifier in allowed_uids:
            bits |= READ_WRITE
        result.append((tag, identifier, bits))
    result.extend(((GROUP, gid, READ_WRITE), (MASK, None, mask | READ_WRITE)))
    return result


def set_acl(fd, rows):
    tags = {USER_OBJ: 'user', USER: 'user', GROUP_OBJ: 'group', GROUP: 'group', MASK: 'mask', OTHER: 'other'}
    lines = []
    for tag, identifier, bits in rows:
        permissions = ''.join(char if bits & bit else '-' for char, bit in (('r', 4), ('w', 2), ('x', 1)))
        lines.append(f'{tags[tag]}:{identifier if identifier is not None else ""}:{permissions}')
    lib = library()
    acl = lib.acl_from_text('\n'.join(lines).encode())
    if not acl:
        raise _error('Cannot construct input ACL')
    try:
        if lib.acl_valid(acl) != 0 or lib.acl_set_fd(fd, acl) != 0:
            raise _error('Cannot grant dedicated input access')
    finally:
        lib.acl_free(acl)


def grant_fd(fd, gid, allowed_uids=()):
    original = read_acl(fd=fd)
    expected = grant_rows(original, gid, allowed_uids)
    if set(original) == set(expected):
        return False
    set_acl(fd, expected)
    if set(read_acl(fd=fd)) != set(expected):
        raise RuntimeError('The input ACL changed while verifying the grant. Retry input setup.')
    return True


def device_group_ready(node, gid):
    if not stat.S_ISCHR(node.st_mode):
        return False
    try:
        return group_has_access(read_acl('/dev/uinput'), node.st_gid, gid)
    except (OSError, ValueError):
        return False


def grant_device_access(target_uid=None):
    if os.geteuid() != 0:
        raise PermissionError('Input access repair requires administrator authorization.')
    group = grp.getgrnam('monitorize-input')
    expected = tuple(int(part) for part in Path('/sys/class/misc/uinput/dev').read_text().strip().split(':'))
    lock = os.open('/run/monitorize-input-access.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
    except BaseException:
        os.close(lock)
        raise
    try:
        return _grant_device_access(group, expected, target_uid)
    finally:
        os.close(lock)


def _grant_device_access(group, expected, target_uid):
    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        node = os.fstat(fd)
        if (not stat.S_ISCHR(node.st_mode) or node.st_uid != 0
                or (os.major(node.st_rdev), os.minor(node.st_rdev)) != expected):
            raise ValueError('Refusing input repair: /dev/uinput is not the root-owned kernel uinput device.')
        # Named user entries precede group membership in POSIX ACL evaluation.
        # Preserve other users; only already enrolled users/the authorizing user
        # may receive the same narrow input grant as monitorize-input.
        allowed = {target_uid} if target_uid is not None else set()
        for tag, identifier, _ in read_acl(fd=fd):
            if tag == USER:
                try:
                    user = pwd.getpwuid(identifier)
                    if group.gr_gid in os.getgrouplist(user.pw_name, user.pw_gid):
                        allowed.add(identifier)
                except KeyError:
                    pass
        changed = grant_fd(fd, group.gr_gid, allowed)
        if not group_has_access(read_acl(fd=fd), node.st_gid, group.gr_gid):
            raise RuntimeError('Dedicated input access could not be verified.')
        current = os.stat('/dev/uinput', follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (node.st_dev, node.st_ino):
            raise RuntimeError('The uinput device was replaced during repair. Retry input setup.')
        return changed
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise ValueError('This fixed input-access action accepts no arguments.')
        grant_device_access()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
