"""Sandboxed removal tests: no commands or deletes against the host installation."""
import contextlib
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import types
import sys
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('source_uninstall_tests', ROOT / 'scripts/uninstall-source.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def write(path, value='fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


class SourceUninstallerTests(unittest.TestCase):
    def test_keep_and_purge_preserve_unrelated_files_and_checkout(self):
        for mode in ('keep', 'all'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); home = root / 'home'; repo = root / 'repo'
                prefs = write(home / '.config/monitorize/settings.ini')
                pairing = write(home / '.config/monitorize/sunshine-1/credentials/cakey.pem')
                sunshine = write(home / '.config/sunshine/sunshine.conf')
                unrelated = write(home / '.local/share/applications/sunshine.desktop')
                launcher = write(home / '.local/share/applications/monitorize.desktop')
                autostart = write(home / '.config/autostart/monitorize.desktop')
                installed = write(repo / 'linux/venv/bin/monitorize')
                build = write(repo / 'external/sunshine/build/sunshine')
                cache = write(repo / 'external/sunshine/node_modules/cache')
                bytecode = write(repo / 'linux/monitorize/__pycache__/old.pyc')
                source = write(repo / 'linux/monitorize/my-edits.py')
                for target in u.user_targets(mode, home=home, environment={}, repository=repo):
                    u.erase(target)
                # A second pass must be harmless.
                for target in u.user_targets(mode, home=home, environment={}, repository=repo):
                    u.erase(target)
                for path in (launcher, autostart, installed, build, cache, bytecode):
                    self.assertFalse(path.exists(), path)
                for path in (sunshine, unrelated, source):
                    self.assertTrue(path.exists(), path)
                self.assertEqual(prefs.exists(), mode == 'keep')
                self.assertEqual(pairing.exists(), mode == 'keep')

    def test_custom_xdg_and_legacy_roots_both_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / 'home'
            environment = {f'XDG_{kind}_HOME': str(root / kind) for kind in ('CONFIG', 'DATA', 'STATE', 'CACHE')}
            saved = [write(Path(base) / 'monitorize/file') for base in environment.values()]
            saved += [write(home / '.config/monitorize/settings.ini'), write(home / '.local/state/monitorize/monitorize.log')]
            for path in u.user_targets('all', home=home, environment=environment, repository=root / 'repo'):
                u.erase(path)
            self.assertFalse(any(path.exists() for path in saved))

    def test_leaf_symlink_never_deletes_external_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); outside = write(root / 'personal/file')
            target = root / 'monitorize'; target.symlink_to(outside.parent, target_is_directory=True)
            u.erase(target)
            self.assertTrue(outside.exists())
            self.assertFalse(target.is_symlink())

    def test_symlink_ancestor_is_rejected_even_when_child_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); outside = write(root / 'personal/file')
            redirect = root / 'config'; redirect.symlink_to(outside.parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'ancestor'):
                u.erase(redirect / 'monitorize')
            self.assertTrue(outside.exists())

    def test_package_owned_assets_block_removal_before_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); asset = write(root / 'assets/entry.py')
            with patch.object(u, 'check_system_parents'):
                with self.assertRaisesRegex(ValueError, 'Package-owned'):
                    u.package_check([asset.parent], lambda path: 'monitorize')
            self.assertTrue(asset.exists())

    def test_system_target_modes_keep_identity_and_never_target_host_package(self):
        keep = u.system_targets(1000, 'keep')
        purge = u.system_targets(1000, 'all')
        self.assertNotIn(Path('/etc/monitorize'), keep)
        self.assertIn(Path('/etc/monitorize'), purge)
        self.assertIn(Path('/var/lib/monitorize'), purge)
        self.assertNotIn(Path('/var/lib/monitorize'), keep)
        for target in (*keep, *purge):
            self.assertFalse(str(target).startswith('/usr/libexec/monitorize-vkms/'))
            self.assertFalse(str(target).startswith('/usr/lib/monitorize-vkms/'))
            self.assertNotEqual(target, Path('/run/monitorize-vkms'))

    def test_other_system_account_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = write(root / 'etc/monitorize/remote-login.json', json.dumps({'uid': 1001}))
            with self.assertRaisesRegex(ValueError, 'another account'):
                u.check_system_account(1000, root)
            self.assertTrue(saved.exists())

    def test_other_saved_identity_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = write(root / 'var/lib/monitorize/remote-login/1001/identity/key')
            with self.assertRaisesRegex(ValueError, 'another account'):
                u.check_system_account(1000, root)
            self.assertTrue(saved.exists())

    def test_process_matching_does_not_match_shell_text_or_personal_sunshine(self):
        self.assertFalse(u.process_matches(['bash', '-c', 'python -m monitorize']))
        self.assertFalse(u.process_matches(['/usr/bin/sunshine', '/home/a/.config/sunshine/sunshine.conf']))
        self.assertTrue(u.process_matches(['/usr/bin/python3', '-m', 'monitorize']))
        self.assertTrue(u.process_matches(['/usr/bin/python3', '-m', 'monitorize.streaming.recovery_guardian']))
        self.assertTrue(u.process_matches(['/usr/bin/sunshine', '/owned/sunshine.conf'], configs=['/owned/sunshine.conf']))

    def test_incomplete_shutdown_keeps_all_files_and_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = write(Path(tmp) / 'settings.ini')
            with patch.object(u.os, 'geteuid', return_value=1000), patch.object(u.sys, 'argv', ['uninstall-source.py', 'all']), \
                    patch.object(u, 'user_targets', return_value=[saved]), \
                    patch.object(u, 'stop_desktop', side_effect=RuntimeError('busy')), \
                    patch.object(u, 'run') as command, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(u.main(), 1)
            self.assertTrue(saved.exists()); command.assert_not_called()

    def test_privileged_failure_keeps_user_files_and_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = write(Path(tmp) / 'settings.ini')
            with patch.object(u.os, 'geteuid', return_value=1000), patch.object(u.sys, 'argv', ['uninstall-source.py', 'all']), \
                    patch.object(u, 'ROOT', Path(tmp)), patch.object(u, 'user_targets', return_value=[saved]), \
                    patch.object(u, 'stop_desktop'), patch.object(u, 'source_system_present', return_value=True), \
                    patch.object(u, 'run', side_effect=RuntimeError('authorization cancelled')), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(u.main(), 1)
            self.assertTrue(saved.exists()); self.assertIn('incomplete', errors.getvalue())

    def test_inactive_ufw_removes_only_named_monitorize_rule(self):
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            return "ufw allow 47989/tcp\nufw allow 'Monitorize'\n" if argv[-2:] == ['show', 'added'] else ''
        with patch.object(u.shutil, 'which', side_effect=lambda name: '/usr/sbin/ufw' if name == 'ufw' else None), patch.object(u, 'run', side_effect=command):
            u.remove_firewall()
        self.assertEqual(commands[-1], ['/usr/sbin/ufw', '--force', 'delete', 'allow', 'Monitorize'])
        self.assertFalse(any('47989/tcp' in command for command in commands))

    def test_unused_ufw_does_not_try_to_delete_nonexistent_profile(self):
        with patch.object(u.shutil, 'which', side_effect=lambda name: '/usr/sbin/ufw' if name == 'ufw' else None), patch.object(u, 'run', return_value='') as command:
            u.remove_firewall()
        command.assert_called_once_with(['/usr/sbin/ufw', 'show', 'added'])

    def test_old_installer_removal_arguments_are_rejected(self):
        for argument in ('remove', 'uninstall'):
            result = subprocess.run(['bash', str(ROOT / 'linux/scripts/install.sh'), argument], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_quit_ipc_uses_normal_layout_restoring_shutdown(self):
        # Execute the actual handler independently of GUI/Qt initialization.
        tree = ast.parse((ROOT / 'linux/monitorize/desktop/main_window.py').read_text())
        handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_handle_instance_command')
        namespace = {}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), '<instance-handler>', 'exec'), namespace)
        peer = Mock()
        peer.property.return_value = b''
        peer.readAll.return_value = b'quit'
        server = Mock()
        server.hasPendingConnections.side_effect = [True, False]
        server.nextPendingConnection.return_value = peer
        window = Mock()
        namespace['_handle_instance_command'](server, window)
        window._quit_app.assert_called_once_with()
        window._restore_from_tray.assert_not_called()
        peer.disconnectFromServer.assert_called_once_with()

    def test_system_cleanup_finishes_lifecycle_before_assets_and_purges_data_last(self):
        for mode in ('keep', 'all'):
            with self.subTest(mode=mode):
                self.run_system_fixture(mode)

    def run_system_fixture(self, mode, *, fail_disable=False, fail_reload=False):
        events = []
        asset, data = Path('/fixture/asset'), Path('/fixture/data')
        backend = Mock()
        backend.state.return_value = ({'uid': 1000, 'enabled': True}, True)
        backend.properties.return_value = {'LoadState': 'not-found'}
        def disable(uid):
            events.append('disable-and-release')
            return {'success': not fail_disable, 'message': 'release failed'}
        lifecycle = Mock(); lifecycle.load.return_value = None
        modules = {
            'monitorize.remote_login.live_setup': types.SimpleNamespace(setup_lock=contextlib.nullcontext),
            'monitorize.remote_login.package_lifecycle': types.SimpleNamespace(InstalledBackend=lambda: backend, PackageLifecycle=lambda value: lifecycle),
            'monitorize.remote_login.enrollment': types.SimpleNamespace(disable=disable, restore_linger=Mock(), Commands=Mock()),
        }
        def command(argv, **kwargs):
            events.append('reload')
            if fail_reload:
                raise RuntimeError('reload failed')
        with patch.dict(sys.modules, modules), patch.object(u, 'module', return_value=Mock()), \
                patch.object(u, 'system_targets', side_effect=lambda uid, selected: [asset] + ([data] if selected == 'all' else [])), \
                patch.object(u, 'package_check'), patch.object(u, 'check_system_account'), \
                patch.object(u, 'remove_firewall'), patch.object(u, 'remove_input_membership'), \
                patch.object(u, 'remove_empty_system_dirs'), patch.object(u, 'remove_source_bytecode'), patch.object(u.shutil, 'which', return_value=None), \
                patch.object(u, 'erase', side_effect=lambda path: events.append(path)), patch.object(u, 'run', side_effect=command):
            if fail_disable or fail_reload:
                with self.assertRaises(RuntimeError):
                    u.system_cleanup(1000, mode)
                self.assertNotIn(data, events)
                if fail_disable:
                    self.assertNotIn(asset, events)
            else:
                self.assertTrue(u.system_cleanup(1000, mode)['success'])
                self.assertLess(events.index('disable-and-release'), events.index(asset))
                if mode == 'all':
                    self.assertLess(events.index('reload'), events.index(data))
                else:
                    self.assertNotIn(data, events)

    def test_failed_display_release_prevents_system_file_deletion(self):
        self.run_system_fixture('all', fail_disable=True)

    def test_failed_service_reload_retains_pairing_and_recovery_data(self):
        self.run_system_fixture('all', fail_reload=True)

    def test_menu_selects_each_mode_using_arrow_keys_and_enter(self):
        # Exercise the actual shell TUI, replacing only the Python backend with
        # an argv recorder; no application deletion or privilege prompt occurs.
        for keys, mode in ((b'\r', 'keep'), (b'\x1b[B\r', 'all')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); fake = write(root / 'python3', '#!/bin/sh\nprintf "SELECTED:%s\\n" "$3"\n')
                fake.chmod(0o755)
                master, slave = pty.openpty()
                # Keep the real TUI and dispatch. In root CI containers, disable
                # only the guard in a disposable copy (tested separately).
                script = (ROOT / 'linux/scripts/uninstall.sh').read_text()
                script = script.replace('if (( EUID == 0 )); then', 'if false; then')
                fixture = write(root / 'uninstall.sh', script)
                child = subprocess.Popen(['bash', str(fixture)],
                    stdin=slave, stdout=slave, stderr=slave, env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH']})
                os.close(slave)
                output = b''
                try:
                    for _ in range(50):
                        if select.select([master], [], [], .1)[0]:
                            output += os.read(master, 65536)
                            if b'Cancel' in output:
                                break
                    os.write(master, keys)
                    child.wait(timeout=5)
                    while select.select([master], [], [], .1)[0]:
                        try: output += os.read(master, 65536)
                        except OSError: break
                    self.assertEqual(child.returncode, 0, output.decode(errors='replace'))
                    self.assertIn(('SELECTED:' + mode).encode(), output)
                finally:
                    if child.poll() is None: child.kill(); child.wait()
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
