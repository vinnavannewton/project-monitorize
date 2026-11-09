import os
from pathlib import Path
import tempfile
import unittest
from monitorize.remote_login.display_managers import manager_capabilities,sddm_configuration

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'etc/systemd/system').mkdir(parents=True)
        (self.root/'etc/systemd/system/display-manager.service').symlink_to('/usr/lib/systemd/system/sddm.service')
    def write(self,path,text):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def capabilities(self):return manager_capabilities(self.root,owner_uid=os.getuid())
    def test_x11_default_is_not_silently_supported(self):
        self.assertFalse(self.capabilities()['supported'])
    def test_local_and_final_configuration_precedence(self):
        self.write('usr/lib/sddm/sddm.conf.d/10.conf','[General]\nDisplayServer=wayland\n[Wayland]\nCompositorCommand=kwin_wayland\n')
        self.write('etc/sddm.conf.d/20.conf','[Wayland]\nCompositorCommand=start-hyprland -- --config /some/config\n')
        self.assertEqual(self.capabilities()['compositor'],'hyprland')
        self.assertTrue(self.capabilities()['supported'])
        self.assertFalse(self.capabilities()['validated'])
        self.write('etc/sddm.conf','[General]\nDisplayServer=x11-user\n')
        self.assertFalse(self.capabilities()['supported'])
    def test_wayland_default_compositor_is_weston(self):
        self.write('etc/sddm.conf','[General]\nDisplayServer=wayland\n')
        self.assertEqual(self.capabilities()['compositor'],'weston')
    def test_wrapper_is_not_executed_or_guessed(self):
        self.write('etc/sddm.conf','[General]\nDisplayServer=wayland\n[Wayland]\nCompositorCommand=sh -c "kwin_wayland; touch /tmp/no"\n')
        self.assertFalse(self.capabilities()['supported'])
    def test_unsafe_configuration_is_rejected(self):
        self.write('etc/sddm.conf','[General]\nDisplayServer=wayland\n')
        (self.root/'etc/sddm.conf').chmod(0o666)
        self.assertFalse(self.capabilities()['supported'])
    def test_plasma_remains_recognized(self):
        link=self.root/'etc/systemd/system/display-manager.service';link.unlink();link.symlink_to('/usr/lib/systemd/system/plasmalogin.service')
        self.assertTrue(self.capabilities()['supported'])

    def use_legacy_sddm(self):
        alias = self.root / 'etc/systemd/system/display-manager.service'
        alias.unlink()
        alias.symlink_to('/usr/lib/systemd/system/display-manager-legacy.service')
        self.write('usr/lib/systemd/system/display-manager-legacy.service', '')
        self.write('usr/lib/X11/displaymanagers/sddm', '')
        self.write('usr/bin/sddm', '')
        (self.root / 'usr/bin/sddm').chmod(0o755)
        self.write('usr/bin/xrandr', '')
        (self.root / 'usr/bin/xrandr').chmod(0o755)
        selector = self.root / 'etc/alternatives/default-displaymanager'
        selector.parent.mkdir()
        selector.symlink_to('/usr/lib/X11/displaymanagers/sddm')
        (self.root / 'usr/lib/X11/displaymanagers/default-displaymanager').symlink_to(
            '/etc/alternatives/default-displaymanager')
        return selector

    def test_legacy_sddm_uses_adapter_and_retains_service_unit(self):
        self.use_legacy_sddm()
        capability = self.capabilities()
        self.assertTrue(capability['supported'])
        self.assertEqual(capability['manager'], 'sddm.service')
        self.assertEqual(capability['service_unit'], 'display-manager-legacy.service')
        self.assertEqual(capability['display_server'], 'x11')

    def test_legacy_wrapper_rejects_other_selection_and_writable_launcher(self):
        selector = self.use_legacy_sddm()
        launcher = self.root / 'usr/lib/X11/displaymanagers/sddm'
        launcher.chmod(0o666)
        self.assertFalse(self.capabilities()['supported'])
        launcher.chmod(0o644)
        selector.unlink()
        selector.symlink_to('/tmp/sddm')
        self.assertFalse(self.capabilities()['supported'])

    def test_unknown_or_symlinked_config_is_not_trusted(self):
        self.write('etc/sddm.conf','[General]\nDisplayServer=wayland\n[Wayland]\nCompositorCommand=/custom/wrapper\n')
        self.assertFalse(self.capabilities()['supported'])
        path=self.root/'etc/sddm.conf';path.unlink();path.symlink_to('/etc/passwd')
        self.assertFalse(self.capabilities()['supported'])

    def test_x11_requires_xrandr(self):
        self.write('usr/bin/xrandr', '')
        (self.root/'usr/bin/xrandr').chmod(0o755)
        capability = self.capabilities()
        self.assertTrue(capability['supported'])
        self.assertEqual(capability['compositor'], 'xorg')
        self.assertFalse(capability['validated'])


class X11OutputTests(unittest.TestCase):
    def test_selects_greeter_primary_and_current_refresh(self):
        from monitorize.remote_login.sddm_x11 import select_output

        target = select_output('Virtual-1 connected primary 1280x800+0+0 (normal)\n'
                               '   1280x800     59.91*+  60.00\n'
                               'Virtual-2 connected 1920x1080+1280+0 (normal)\n'
                               '   1920x1080    60.00*\n')
        self.assertEqual(target, dict(output_name='Virtual-1', capture='kms',
                                      width=1280, height=800, fps=59.91))

    def test_rejects_ambiguous_or_inactive_outputs(self):
        from monitorize.remote_login.sddm_x11 import select_output

        active = 'Virtual-1 connected 1280x800+0+0\n   1280x800  60.00*\n'
        with self.assertRaises(ValueError):
            select_output(active + active.replace('Virtual-1', 'Virtual-2'))
        with self.assertRaises(ValueError):
            select_output('Virtual-1 connected (normal)\n   1280x800  60.00\n')

    def test_refuses_capture_if_greeter_changes_during_probe(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from monitorize.remote_login import sddm_x11

        environment = {'DISPLAY': ':0', 'XAUTHORITY': '/tmp/greeter-auth'}
        account = SimpleNamespace(pw_uid=973, pw_gid=973)
        report = 'Virtual-1 connected 1280x800+0+0\n   1280x800  60.00*\n'
        with patch.object(sddm_x11, 'greeter_environment',
                          side_effect=[((10, 11), environment), ((12, 13), environment)]), \
             patch.object(sddm_x11.pwd, 'getpwuid', return_value=account), \
             patch.object(sddm_x11.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=report)) as probe, \
             patch.object(sddm_x11, 'verify_drm', return_value=Path('/sys/class/drm/card1-Virtual-1')), \
             patch('monitorize.remote_login.daemon.require_installed'):
            with self.assertRaisesRegex(PermissionError, 'changed'):
                sddm_x11.operation({'greeter_uid': 973}, 'prepare', Mock())
        self.assertEqual(probe.call_args.kwargs['user'], 973)
        self.assertEqual(probe.call_args.kwargs['env'], environment)
