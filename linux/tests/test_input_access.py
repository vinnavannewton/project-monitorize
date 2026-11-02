"""Exercise real POSIX ACLs without touching the host's input devices."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from monitorize.platform import input_access as access


def effective_entries(rows):
    mask = next((bits for tag, _, bits in rows if tag == access.MASK), 7)
    return {(tag, identifier): bits & mask if tag in (access.USER, access.GROUP_OBJ, access.GROUP) else bits
            for tag, identifier, bits in rows if tag != access.MASK}


class InputAccessTests(unittest.TestCase):
    def test_named_group_repairs_other_ownership_without_widening_masked_entries(self):
        # Use IDs different from the running user/group; no account changes.
        monitorize_gid, other_gid, other_uid = 32001, 32002, 32003
        with tempfile.TemporaryFile() as stream:
            original = [(access.USER_OBJ, None, 6), (access.USER, other_uid, 7),
                        (access.GROUP_OBJ, None, 6), (access.GROUP, other_gid, 7),
                        (access.MASK, None, 4), (access.OTHER, None, 0)]
            access.set_acl(stream.fileno(), original)
            before = os.fstat(stream.fileno())
            self.assertTrue(access.grant_fd(stream.fileno(), monitorize_gid))
            rows = access.read_acl(fd=stream.fileno())
            after = os.fstat(stream.fileno())
            self.assertEqual((before.st_uid, before.st_gid), (after.st_uid, after.st_gid))
            self.assertTrue(access.group_has_access(rows, after.st_gid, monitorize_gid))
            expected = effective_entries(original)
            actual = effective_entries(rows)
            for identity, permissions in expected.items():
                self.assertEqual(actual[identity], permissions, identity)
            self.assertFalse(access.grant_fd(stream.fileno(), monitorize_gid), 'Repeated grants should be idempotent')

    def test_restrictive_group_mode_does_not_grant_the_existing_group_new_access(self):
        with tempfile.TemporaryFile() as stream:
            os.fchmod(stream.fileno(), 0o600)
            group = os.fstat(stream.fileno()).st_gid
            access.grant_fd(stream.fileno(), 32001)
            rows = access.read_acl(fd=stream.fileno())
            self.assertFalse(access.group_has_access(rows, group, group))
            self.assertTrue(access.group_has_access(rows, group, 32001))
            self.assertEqual(effective_entries(rows)[(access.OTHER, None)], 0)

    def test_only_enrolled_named_user_exceptions_are_repaired(self):
        original = [(access.USER_OBJ, None, 6), (access.USER, 32003, 0),
                    (access.USER, 32004, 0), (access.GROUP_OBJ, None, 0),
                    (access.MASK, None, 0), (access.OTHER, None, 0)]
        with tempfile.TemporaryFile() as stream:
            access.set_acl(stream.fileno(), original)
            access.grant_fd(stream.fileno(), 32001, {32003})
            permissions = effective_entries(access.read_acl(fd=stream.fileno()))
        self.assertEqual(permissions[(access.USER, 32003)], 6)
        self.assertEqual(permissions[(access.USER, 32004)], 0)

    def test_acl_mask_bits_do_not_prove_owning_group_access(self):
        rows = [(access.USER_OBJ, None, 6), (access.GROUP_OBJ, None, 0),
                (access.USER, 32003, 6), (access.MASK, None, 6), (access.OTHER, None, 0)]
        node = type('Node', (), {'st_mode': stat.S_IFCHR | 0o660, 'st_gid': 32001})()
        with patch.object(access, 'read_acl', return_value=rows):
            self.assertFalse(access.device_group_ready(node, 32001))

    def test_root_entry_point_does_not_accept_arbitrary_file_paths(self):
        with patch.object(access.os, 'geteuid', return_value=1000):
            with self.assertRaises(PermissionError):
                access.grant_device_access()

    def test_device_identity_is_verified_before_acl_mutation(self):
        from types import SimpleNamespace
        node = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
        with patch.object(access.os, 'open', return_value=12345), \
             patch.object(access.os, 'close'), patch.object(access.os, 'fstat', return_value=node), \
             patch.object(access, 'grant_fd') as grant:
            with self.assertRaises(ValueError):
                access._grant_device_access(SimpleNamespace(gr_gid=32001), (10, 223), None)
        grant.assert_not_called()
