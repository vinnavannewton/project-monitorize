"""Pre-login capture must never silently select another output/session."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from monitorize.platform.login_capture_probe import (
    session_phase, find_boot_output, build_config, validate_identity,
)


class LoginCaptureProbeTest(unittest.TestCase):
    def test_greeter_is_distinct_from_user_and_lock(self):
        self.assertEqual(session_phase([{'seat': 'seat0', 'class': 'greeter'}]), 'greeter')
        self.assertEqual(session_phase([{'seat': 'seat0', 'class': 'user', 'uid': 1000}]), 'user')
        self.assertEqual(session_phase([{'seat': 'seat0', 'class': 'user', 'uid': 2000}]), 'user')
        self.assertEqual(session_phase([{'class': 'manager'}]), 'waiting')
        self.assertEqual(session_phase([{'seat': 'seat1', 'class': 'greeter'}]), 'waiting')
        with self.assertRaises(ValueError):
            session_phase({'not': 'sessions'})

    def test_identity_snapshot_preserves_uuid_and_pairings(self):
        state = {'root': {'uniqueid': 'existing-host', 'named_devices': [{'cert': 'paired-client'}]}, 'username': 'admin'}
        raw = json.dumps(state).encode()
        self.assertEqual(validate_identity(raw), raw)
        for invalid in [b'{}', b'not json', b'{"root":{"uniqueid":""}}']:
            with self.assertRaises(ValueError):
                validate_identity(invalid)

    def test_boot_config_is_kms_only_without_commands_or_audio(self):
        config = build_config(Path('/var/lib/monitorize-login-capture/1000'), 'My PC Monitor 1', 'vaapi', '/dev/dri/renderD128')
        self.assertIn('capture = kms', config)
        self.assertIn('stream_audio = disabled', config)
        self.assertIn('origin_web_ui_allowed = pc', config)
        self.assertIn('controller = disabled', config)
        self.assertIn('file_state = /var/lib/monitorize-login-capture/1000/identity/sunshine_state.json', config)
        for bad in ['pc\ncapture = portal', 'pc\rencoder = nvenc']:
            with self.assertRaises(ValueError):
                build_config(Path('/safe'), bad, 'vaapi', '')

    def test_only_active_mon1_edid_on_monitorize_device_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            device=root/'faux/monitorize'
            device.mkdir(parents=True)
            connector=root/'card7-Virtual-4'
            connector.mkdir()
            (connector/'device').symlink_to(device)
            (connector/'status').write_text('connected')
            (connector/'enabled').write_text('enabled')
            edid=bytearray(128)
            edid[:8]=b'\x00\xff\xff\xff\xff\xff\xff\x00'
            edid[77:81]=b'mon1'
            edid[-1]=(-sum(edid)) % 256
            (connector/'edid').write_bytes(edid)
            self.assertEqual(find_boot_output(root), 'Virtual-4')
            (connector/'enabled').write_text('disabled')
            self.assertIsNone(find_boot_output(root))
            self.assertEqual(find_boot_output(root,require_enabled=False),'Virtual-4')
            (connector/'enabled').write_text('enabled')
            (connector/'enabled').write_text('disabled')
            self.assertIsNone(find_boot_output(root))

    def test_runner_stops_at_user_login_and_inherits_no_desktop_environment(self):
        from unittest.mock import Mock, patch
        from monitorize.platform import login_capture_probe as probe
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'runtime').mkdir()
            process=Mock(pid=12345, returncode=None)
            process.poll.return_value=None
            phase=Mock(side_effect=['greeter','user'])
            with patch.object(probe,'require_root_file'), patch.object(probe.os,'geteuid',return_value=os.getuid()), \
                 patch.object(probe.os,'access',return_value=True), \
                 patch.object(probe.subprocess,'run',return_value=Mock(stdout='sunshine cap_sys_admin=p')), \
                 patch.object(probe.subprocess,'Popen',return_value=process) as launch, \
                 patch.object(probe,'stop_engine') as stop, patch.object(probe.signal,'signal'):
                # File-lock ownership remains that of the current fixture user.
                self.assertEqual(probe.run_capture(root,phase_reader=phase,output_finder=lambda:'Virtual-4'),0)
            stop.assert_called_once_with(process)
            argv=launch.call_args.args[0]
            self.assertEqual(argv[-1],'output_name=Virtual-4')
            env=launch.call_args.kwargs['env']
            for key in ('WAYLAND_DISPLAY','DISPLAY','DBUS_SESSION_BUS_ADDRESS','LD_LIBRARY_PATH'):
                self.assertNotIn(key,env)
            self.assertEqual(env['MONITORIZE_REQUIRE_EXACT_OUTPUT'],'1')

    def test_existing_user_prevents_any_engine_start(self):
        from unittest.mock import Mock, patch
        from monitorize.platform import login_capture_probe as probe
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'runtime').mkdir()
            with patch.object(probe,'require_root_file'), patch.object(probe.os,'geteuid',return_value=os.getuid()), \
                 patch.object(probe.os,'access',return_value=True), \
                 patch.object(probe.subprocess,'run',return_value=Mock(stdout='sunshine cap_sys_admin=p')), \
                 patch.object(probe.subprocess,'Popen') as launch:
                self.assertEqual(probe.run_capture(root,phase_reader=lambda:'user'),0)
            launch.assert_not_called()

    def test_missing_greeter_output_times_out_without_fallback(self):
        from unittest.mock import Mock, patch
        from monitorize.platform import login_capture_probe as probe
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'runtime').mkdir()
            with patch.object(probe,'require_root_file'), patch.object(probe.os,'geteuid',return_value=os.getuid()), \
                 patch.object(probe.os,'access',return_value=True), \
                 patch.object(probe.subprocess,'run',return_value=Mock(stdout='sunshine cap_sys_admin=p')), \
                 patch.object(probe.subprocess,'Popen') as launch, \
                 patch.object(probe.time,'monotonic',side_effect=[0,0,46]), patch.object(probe.time,'sleep'):
                with self.assertRaisesRegex(ValueError,'No enabled mon1'):
                    probe.run_capture(root,phase_reader=lambda:'greeter',output_finder=lambda:None)
            launch.assert_not_called()

    def test_root_process_is_refused(self):
        from unittest.mock import patch
        from monitorize.platform import login_capture_probe as probe
        with patch.object(probe.os,'geteuid',return_value=0), self.assertRaisesRegex(ValueError,'non-root'):
            probe.run_capture(Path('/unused'))

    def test_capture_apps_schema_and_cached_desktop_identity(self):
        import hashlib
        import ctypes
        import zlib
        root=Path(__file__).resolve().parents[2]
        apps=json.loads((root/'scripts/remote-login-probe/capture-apps.json').read_text())
        self.assertEqual(apps['env'],{})  # Sunshine requires this node even empty.
        desktop=next(a for a in apps['apps'] if a['name']=='Desktop')
        self.assertEqual(desktop['image-path'],'desktop.png')
        for app in apps['apps']:
            self.assertLessEqual(set(app),{'name','image-path'})
        image=root/'external/sunshine/build/assets/desktop.png'
        if image.is_file():
            app_id=abs(ctypes.c_int32(zlib.crc32(('Desktop'+hashlib.sha256(image.read_bytes()).hexdigest()).encode())).value)
            self.assertEqual(app_id,881448767)

    def test_named_mon1_label_cannot_be_selected_as_reserved_boot_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            device=root/('faux/monitorize-named-1000-'+'a'*32)
            device.mkdir(parents=True)
            connector=root/'card8-Virtual-5';connector.mkdir()
            (connector/'device').symlink_to(device)
            (connector/'status').write_text('connected')
            (connector/'enabled').write_text('enabled')
            edid=bytearray(128);edid[:8]=b'\x00\xff\xff\xff\xff\xff\xff\x00'
            edid[77:81]=b'mon1';edid[-1]=(-sum(edid))%256
            (connector/'edid').write_bytes(edid)
            self.assertIsNone(find_boot_output(root))
