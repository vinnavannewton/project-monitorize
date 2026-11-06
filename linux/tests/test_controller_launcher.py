import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import stat

from monitorize.desktop.controller_launcher import (
    require_local_graphical_session, wait_for_graphical_session, _socket_ready,
)


class LauncherTests(unittest.TestCase):
    def test_waits_for_manager_environment_and_a_live_wayland_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[0];server=socket.socket(socket.AF_UNIX)
            self.addCleanup(server.close)
            def sleep(seconds):
                now[0]+=seconds
                if now[0]==1:
                    server.bind(str(Path(directory)/'wayland-test'));server.listen()
            environment=wait_for_graphical_session(
                {'XDG_SESSION_TYPE':'wayland','XDG_RUNTIME_DIR':directory},
                manager_environment=lambda:{'WAYLAND_DISPLAY':'wayland-test'},
                clock=lambda:now[0],sleep=sleep,timeout=3)
            self.assertEqual(now[0],1)
            self.assertEqual(environment['WAYLAND_DISPLAY'],'wayland-test')
            self.assertEqual(environment['QT_QPA_PLATFORM'],'wayland')

    def test_missing_display_times_out_without_starting_qt(self):
        now=[0]
        with self.assertRaisesRegex(RuntimeError,'graphical session'):
            wait_for_graphical_session({},manager_environment=lambda:{},
                clock=lambda:now[0],sleep=lambda seconds:now.__setitem__(0,now[0]+seconds),timeout=2)
        self.assertEqual(now[0],2)

    def test_stale_file_is_not_a_ready_wayland_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'wayland-test').touch();now=[0]
            with self.assertRaises(RuntimeError):
                wait_for_graphical_session({'XDG_RUNTIME_DIR':directory,'WAYLAND_DISPLAY':'wayland-test','XDG_SESSION_TYPE':'wayland'},
                    manager_environment=lambda:{},clock=lambda:now[0],sleep=lambda seconds:now.__setitem__(0,now[0]+seconds),timeout=1)

    def test_root_owned_xorg_socket_is_allowed_but_not_for_wayland(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as server:
            path=Path(directory)/'X0';server.bind(str(path));server.listen()
            with patch.object(Path,'stat',return_value=SimpleNamespace(st_mode=stat.S_IFSOCK,st_uid=0)):
                self.assertTrue(_socket_ready(path,allow_root=True))
                self.assertFalse(_socket_ready(path))

    def test_cli_requires_the_same_users_active_local_graphical_session(self):
        properties = (
            f"User={os.getuid()}\nSeat=seat0\nRemote=no\n"
            "Type=wayland\nClass=user\nActive=yes"
        )
        for reply in (properties, properties.replace('Remote=no', 'Remote=yes')):
            with self.subTest(reply=reply), patch('subprocess.run') as run:
                run.side_effect = [
                    SimpleNamespace(stdout='3\n'),
                    SimpleNamespace(stdout=reply),
                ]
                if 'Remote=yes' in reply:
                    with self.assertRaisesRegex(RuntimeError, 'No active local graphical session'):
                        require_local_graphical_session()
                else:
                    require_local_graphical_session()
