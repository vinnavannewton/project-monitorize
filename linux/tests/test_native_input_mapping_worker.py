"""Exercise the actual embedded worker without compositor or input mutations."""
import contextlib
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'external/sunshine/src/platform/linux/monitorize_input_mapping.h'

class NativeInputWorkerTests(unittest.TestCase):
    def run_worker(self, desktop, modules=None, environment=None):
        script = SOURCE.read_text().split('R"MZPY(', 1)[1].split(')MZPY"', 1)[0]
        output = io.StringIO()
        with patch.object(sys, 'argv', ['worker', 'Virtual-1', 'touch', '19802', '4097', 'Monitorize Touch 1']), patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': desktop, 'HYPRLAND_INSTANCE_SIGNATURE': '', 'SWAYSOCK': '', **(environment or {})}), patch.dict(sys.modules, modules or {}), contextlib.redirect_stdout(output):
            try:
                exec(compile(script, '<mapping-worker>', 'exec'), {})
                code = 0
            except SystemExit as error:
                code = error.code
        return code, output.getvalue()

    def test_unsupported_desktop_uses_mouse(self):
        code, output = self.run_worker('niri')
        self.assertEqual(code, 1)
        self.assertIn('No isolated native mapping', output)

    def test_kde_maps_owned_device_and_verifies(self):
        class Device:
            output = ''
            def GetAll(self, *args, **kwargs):
                return dict(name='Monitorize Touch 1', vendor=19802, product=4097)
            def Get(self, interface, key, **kwargs):
                return ['event20'] if key == 'devicesSysNames' else self.output
            def Set(self, interface, key, value, **kwargs):
                self.output = value
        device = Device()
        bus = SimpleNamespace(get_object=lambda *args: device)
        dbus = SimpleNamespace(SessionBus=lambda: bus, Interface=lambda obj, interface: obj)
        code, output = self.run_worker('KDE', {'dbus': dbus})
        self.assertEqual(code, 0)
        self.assertEqual(device.output, 'Virtual-1')
        self.assertEqual(output.strip(), 'native')

    def test_sway_rejection_uses_mouse(self):
        import subprocess
        def run(args, **kwargs):
            if 'get_inputs' in args:
                return SimpleNamespace(stdout='[{"identifier":"owned","name":"Monitorize Touch 1","vendor":19802,"product":4097}]')
            return SimpleNamespace(stdout='[{"success":false}]')
        with patch.object(subprocess, 'run', side_effect=run):
            code, output = self.run_worker('sway', environment={'SWAYSOCK': '/unused'})
        self.assertEqual(code, 1)
        self.assertIn('Sway rejected', output)
