"""Read-only GDM feasibility evidence. Never connects an output or starts a service."""
from pathlib import Path
import subprocess
from .display_managers import manager_capabilities
from monitorize.platform.login_capture_probe import list_session_ids


def command(argv):
    return subprocess.run(argv,capture_output=True,text=True,check=True,timeout=4).stdout


def collect(*,run=command,drm=Path('/sys/class/drm'),capability=None):
    capability=capability or manager_capabilities()
    report=dict(manager=capability['manager'],experimental=True,live_validation='REQUIRED',
        status='BLOCKED',sessions=[],connected_outputs=[],errors=[])
    if capability['manager']!='gdm.service':
        report['errors'].append('The configured manager is not GDM. No configuration was changed.')
        return report
    try:
        for session in list_session_ids(run):
            text=run(['loginctl','show-session',session,'--no-pager',
                '--property=Type','--property=Class','--property=Active',
                '--property=Remote','--property=Seat','--property=User'])
            values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
            if values.get('Seat')=='seat0':report['sessions'].append(dict(id=session,**values))
        for path in sorted(Path(drm).glob('card*-*'))[:128]:
            if (path/'status').is_file() and (path/'status').read_text().strip()=='connected':
                report['connected_outputs'].append(path.name)
    except (OSError,ValueError,TypeError,subprocess.SubprocessError) as error:
        report['errors'].append('Could not collect bounded greeter evidence: '+type(error).__name__)
        return report
    greeters=[s for s in report['sessions'] if s.get('Class')=='greeter' and s.get('Active')=='yes' and s.get('Remote')=='no']
    users=[s for s in report['sessions'] if s.get('Class') not in ('greeter','manager')]
    if users:report['errors'].append('A user session occupies seat0; test greeter readiness before login.')
    elif len(greeters)!=1:report['errors'].append('No unique active local greeter. Zero-output GDM bootstrap is not validated; capture remains blocked.')
    elif greeters[0].get('Type')!='wayland':report['errors'].append('The actual GDM greeter uses X11; Wayland is required.')
    else:report['status']='PROBE_REQUIRED'
    report['note']='Session presence is not proof of Mutter permission, VKMS hotplug, KMS frames or working remote input.'
    return report
