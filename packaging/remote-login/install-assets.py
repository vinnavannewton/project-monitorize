#!/usr/bin/env python3
"""Stage trusted service code/units for native packages; never activate services."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
MODULES=('remote_login','platform/remote_login_identity.py','platform/login_capture_probe.py','platform/remote_login_host.py')


def stage(destination,*,repository=ROOT):
    destination=Path(destination).absolute()
    if destination==Path('/'):raise ValueError('Use a staging/package root, never the live filesystem.')
    library=destination/'usr/lib/monitorize-remote-login/monitorize'
    library.mkdir(parents=True,exist_ok=True)
    for name in ('__init__.py','platform/__init__.py'):
        target=library/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(repository/'linux/monitorize'/name,target)
    for name in MODULES:
        source=repository/'linux/monitorize'/name;target=library/name
        if source.is_dir():
            shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    base=repository/'packaging/remote-login'
    for source,relative in [(base/'entry.py','usr/libexec/monitorize/remote-login/entry.py'),
        (base/'package-guard.py','usr/libexec/monitorize/remote-login/package-guard.py'),
        (base/'repair.py','usr/libexec/monitorize/remote-login/repair.py'),
        (repository/'scripts/install-remote-login-assets.py','usr/libexec/monitorize/remote-login/deploy.py'),
        (base/'apps.json','usr/share/monitorize/remote-login/apps.json'),
        (base/'io.github.vinnavannewton.MonitorizeRemoteLogin1.conf','usr/share/dbus-1/system.d/io.github.vinnavannewton.MonitorizeRemoteLogin1.conf')]:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for source in base.glob('*.service'):
        target=destination/'usr/lib/systemd/system'/source.name
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    # Build users may have restrictive umasks. Installed code/data must remain
    # readable to the engine UID; no state, credentials or opt-in config is staged.
    roots=[library,destination/'usr/libexec/monitorize/remote-login',destination/'usr/share/monitorize/remote-login']
    for root in roots:
        root.chmod(0o755)
        for path in root.rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)
    for target in (library,destination/'usr/lib/systemd/system',destination/'usr/share/dbus-1/system.d',
                   destination/'usr/libexec/monitorize/remote-login',destination/'usr/share/monitorize/remote-login'):
        current=target
        while current!=destination:
            current.chmod(0o755);current=current.parent
    recovery=destination/'usr/share/monitorize/recovery'
    recovery.mkdir(parents=True,exist_ok=True);recovery.chmod(0o755)
    manifest={}
    for source,name in (('packaging/fedora/monitorize.xml','firewalld.xml'),
                        ('packaging/deb/common/monitorize.ufw.profile','ufw.profile'),
                        ('packaging/fedora/70-monitorize-uinput.rules','uinput.rules')):
        data=(repository/source).read_bytes();(recovery/name).write_bytes(data)
        (recovery/name).chmod(0o644);manifest[name]=hashlib.sha256(data).hexdigest()
    (recovery/'manifest.json').write_text(json.dumps(manifest));(recovery/'manifest.json').chmod(0o644)
    target=destination/'usr/libexec/monitorize/system_recovery.py'
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(repository/'packaging/common/system_recovery.py',target);target.chmod(0o644)
    (destination/'usr/libexec/monitorize/remote-login/repair.py').chmod(0o755)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--destdir',required=True)
    stage(parser.parse_args().destdir)
