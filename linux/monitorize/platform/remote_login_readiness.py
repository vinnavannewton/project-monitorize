"""Read-only, bounded evidence collection for the remote-login experiment.

No display, service, permissions, credentials or configuration is changed.
Static checks cannot establish video or input at a headless greeter.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess


def _command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=4,
                                check=False)
        return result.returncode, result.stdout[:16384].strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def _text(path):
    try:
        with Path(path).open() as source:
            return source.read(4096).strip()
    except OSError:
        return None


def _present(path):
    try:
        Path(path).stat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def evaluate_readiness(facts: dict) -> dict:
    checks = []

    def check(name, value, passed, failed, unknown='Could not establish this prerequisite.'):
        checks.append(dict(name=name, status='PASS' if value is True else
                           'FAIL' if value is False else 'UNKNOWN',
                           detail=passed if value is True else failed if value is False else unknown))

    manager = facts.get('manager', '')
    check('display_manager', True if facts.get('manager_supported',manager=='plasmalogin.service') and facts.get('manager_active') is True else
          False if facts.get('manager_active') is False or facts.get('manager_supported') is False else None,
          'Active supported display-manager configuration; live validation still required.',
          facts.get('manager_detail') or 'Display manager is not active.',
          'This manager has no validated remote-login adapter: ' + str(manager or 'unknown'))
    check('vkms_module', facts.get('module'), 'Monitorize VKMS module is loaded.',
          'Monitorize VKMS module is not loaded.')
    check('vkms_bootstrap', facts.get('bootstrap'), 'Persistent VKMS topology service is active.',
          'Monitorize VKMS bootstrap service is not active.')
    check('seat', None if not facts.get('seat') else facts['seat'] == 'seat0',
          'Selected session is on seat0.', 'First implementation requires seat0.')
    check('greeter', True if facts.get('greeter') else None,
          'logind reports a greeter on seat0.', '',
          'No seat0 greeter was observed; this is normal inside a logged-in desktop.')
    connectors = [c for c in facts.get('connectors', []) if c.get('monitorize')]
    usable = [c for c in connectors if c.get('status') == 'connected' and
              c.get('enabled') == 'enabled' and c.get('edid_valid') and c.get('modes')]
    check('vkms_output', bool(usable) if facts.get('connectors') is not None else None,
          'Connected/enabled Monitorize output has valid EDID and advertised modes.',
          'Monitorize outputs are disconnected, disabled, or lack EDID/modes; a boot output is needed.',
          'No readable Monitorize DRM connector was found.')
    check('framebuffer', facts.get('framebuffer_verified'),
          'Framebuffer evidence was supplied.', 'No usable capture framebuffer.',
          'Connector status does not prove greeter rendering; verify video in the live experiment.')
    check('kms_capability', facts.get('kms_capability'), 'Selected Sunshine binary has cap_sys_admin.',
          'KMS permission missing. Apply sudo setcap cap_sys_admin+p to the exact selected Sunshine binary.',
          'Could not inspect Sunshine capability; select --sunshine with an installed or built binary.')
    check('input_access', facts.get('input_access'), 'Current process can access /dev/uinput.',
          'Current process cannot access /dev/uinput; verify the future service account and seat routing.')
    check('render_access', facts.get('render_access'), 'Current process can access a GPU render node.',
          'Current process cannot access a GPU render node; hardware encoding needs separate permissions.')
    check('identity_access', facts.get('identity_access'), 'Current process can traverse its home directory.',
          'Home is inaccessible; provision boot-accessible identity state.',
          'Account/home accessibility could not be checked.')
    check('encoder', facts.get('encoder_verified'), 'Encoder has independent live evidence.',
          'Requested encoder is unavailable.',
          'Render-node access is insufficient proof of encoding; run an actual capture/encode test.')
    # Even injected PASS evidence does not replace Moonlight/login validation.
    return dict(schema_version=1, ready=False, live_validation='REQUIRED', checks=checks,
                facts=facts, limitations=[
                    'Current-user permissions do not prove boot-service or greeter permissions.',
                    'Home access now does not prove that home will be unlocked before login.',
                    'No framebuffer, encoder, input event or headless login is exercised by this tool.',
                    'No feature support is enabled by this report.',
                ])


def collect_readiness(sunshine: str | None = None) -> dict:
    manager_link = Path('/etc/systemd/system/display-manager.service')
    manager = manager_link.resolve().name if manager_link.exists() else ''
    active_code, _ = _command(['systemctl', 'is-active', 'display-manager.service'])
    bootstrap_code, _ = _command(['systemctl', 'is-active', 'monitorize-vkms-bootstrap.service'])
    version = ''
    if manager == 'plasmalogin.service' and shutil.which('rpm'):
        _, version = _command(['rpm', '-qf', '/usr/bin/plasmalogin'])
    from monitorize.platform.login_capture_probe import read_sessions
    try:
        sessions = read_sessions()
    except (OSError, ValueError, subprocess.SubprocessError):
        sessions = []
    greeter = any(s.get('class') == 'greeter' and s.get('seat') == 'seat0'
                  for s in sessions if isinstance(s, dict))
    # Only retain role/seat evidence, never session environments or credentials.
    seat = next((s.get('seat') for s in sessions if isinstance(s, dict) and
                 s.get('uid') == os.getuid() and s.get('seat')), None)
    connectors = []
    for path in sorted(Path('/sys/class/drm').glob('card*-*'))[:64]:
        if not (path / 'status').is_file():
            continue
        device = str((path / 'device').resolve())
        monitorize = '/faux/monitorize' in device
        if not monitorize:
            continue
        try:
            with (path / 'edid').open('rb') as source:
                edid = source.read(256)
            valid = len(edid) >= 128 and edid[:8] == b'\x00\xff\xff\xff\xff\xff\xff\x00' and sum(edid[:128]) % 256 == 0
        except OSError:
            valid = None
        connectors.append(dict(name=path.name, monitorize=True,
            status=_text(path / 'status'), enabled=_text(path / 'enabled'),
            modes=(_text(path / 'modes') or '').splitlines(), edid_valid=valid))
    binary = str(Path(sunshine).resolve()) if sunshine else ''
    capability = None
    if binary and shutil.which('getcap'):
        cap_code, cap_output = _command(['getcap', '--', binary])
        if cap_code == 0:
            capability = 'cap_sys_admin' in cap_output
    render_nodes = list(Path('/dev/dri').glob('renderD*'))
    home = Path.home()
    from monitorize.remote_login.display_managers import manager_capabilities
    manager_info=manager_capabilities()
    facts = dict(manager_supported=manager_info['supported'],greeter_compositor=manager_info['compositor'],manager_validated=manager_info['validated'],manager_detail=manager_info['message'],manager=manager, manager_version=version, kernel=platform.release(),
        manager_active=None if active_code is None else active_code == 0,
        bootstrap=None if bootstrap_code is None else bootstrap_code == 0,
        module=_present('/sys/module/monitorize_vkms'), seat=seat, greeter=greeter,
        connectors=connectors, sunshine_binary=binary, kms_capability=capability,
        input_access=os.access('/dev/uinput', os.R_OK | os.W_OK),
        render_access=any(os.access(p, os.R_OK | os.W_OK) for p in render_nodes),
        identity_access=os.access(home, os.R_OK | os.X_OK),
        encoder_verified=None, framebuffer_verified=None)
    if manager_info['manager']=='gdm.service':
        from monitorize.remote_login.gdm_probe import collect
        facts['gdm_probe']=collect(capability=manager_info)
    return evaluate_readiness(facts)
