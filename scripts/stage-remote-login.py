#!/usr/bin/env python3
"""Prepare root-installable remote-login assets without modifying the system.

This stages service files only; it never enables the feature or installs anything.
Review and install the staging tree separately, then enroll through Settings.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linux'))
from monitorize.remote_login.capability import exact_kms_engine
from monitorize.platform.remote_login_host import REQUIRED_PROTOCOLS, _constant


def prepare(destination,binary,assets,*,vkms_source=None):
    if os.geteuid()==0:raise ValueError('Stage as your normal user, without sudo.')
    destination=Path(destination).absolute();binary=Path(binary).resolve(strict=True);assets=Path(assets).resolve(strict=True)
    if not exact_kms_engine(binary):raise ValueError('Select the built Monitorize engine with exact KMS output support.')
    if not (assets/'web').is_dir():raise ValueError('Select Sunshine assets containing web/.')
    host=None
    host_version=None
    if vkms_source is not None:
        host=Path(vkms_source).resolve(strict=True)/'monitorize_vkms'
        if not (host/'boot_display.py').is_file() or _constant((host/'helper.py').read_text(),'BOOT_DISPLAY_PROTOCOL')!=1:
            raise ValueError('Select the monitorize-vkms checkout with boot-display protocol v1.')
        if _constant((host/'boot_display.py').read_text(),'GREETER_ON_DEMAND_PROTOCOL')!=1:
            raise ValueError('Select the monitorize-vkms checkout with on-demand greeter protocol v1.')
        if _constant((host/'boot_display.py').read_text(),'IDLE_DISPLAY_PROTOCOL')!=1:
            raise ValueError('Select the monitorize-vkms checkout with idle-display protocol v1.')
        host_version=(host.parent/'VERSION').read_text().strip()
        import re
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',host_version) or tuple(map(int,host_version.split('.')))<(0,3,0):
            raise ValueError('Select monitorize-vkms 0.3.0 or newer.')
    destination.mkdir(mode=0o700)
    try:
        spec=importlib.util.spec_from_file_location('remote_assets',ROOT/'packaging/remote-login/install-assets.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.stage(destination)
        engine=destination/'usr/libexec/monitorize/sunshine';engine.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(binary,engine);engine.chmod(0o755)
        target=destination/'usr/share/monitorize/sunshine/assets'
        shutil.copytree(assets,target,symlinks=False)
        if host is not None:
            target=destination/'usr/lib/monitorize-vkms/monitorize_vkms'
            target.mkdir(parents=True)
            for source in host.glob('*.py'):
                if source.is_symlink():raise ValueError('Host Python files must not be symlinks.')
                shutil.copyfile(source,target/source.name)
            (target.parent/'VERSION').write_text(host_version+'\n')
            manifest=destination/'usr/share/monitorize-vkms/capabilities.json'
            manifest.parent.mkdir(parents=True,exist_ok=True)
            manifest.write_text(json.dumps(dict(schema_version=1,version=host_version,remote_login_protocols=REQUIRED_PROTOCOLS))+'\n')
            # The standalone installer also keeps executable copies here. Update
            # them together so ordinary cleanup cannot bypass boot-lease guards.
            for module,name in (('helper.py','monitorize-vkms-helper'),('dbus_service.py','monitorize-vkms-dbus-service'),('authorization_setup.py','monitorize-vkms-authorization')):
                source=host/module
                if source.is_file():
                    target=destination/'usr/libexec/monitorize-vkms'/name
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        if host is not None and (host/'authorization.py').is_file():
            for name,relative in (
                ('io.github.vinnavannewton.monitorize-vkms.policy','usr/share/polkit-1/actions'),
                ('io.github.vinnavannewton.MonitorizeVkms1.xml','usr/share/dbus-1/interfaces')):
                target=destination/relative/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(host.parent/'packaging'/name,target)
        helper=destination/'usr/libexec/monitorize/monitorize-system-setup'
        shutil.copyfile(ROOT/'packaging/common/monitorize-system-setup',helper);helper.chmod(0o755)
        policy=destination/'usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy'
        policy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'packaging/common/io.github.vinnavannewton.monitorize.system-setup.policy',policy)
        for source,relative in (
            ('packaging/fedora/monitorize.xml','usr/lib/firewalld/services/monitorize.xml'),
            ('packaging/deb/common/monitorize.ufw.profile','etc/ufw/applications.d/monitorize')):
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/source,target)
        for path in destination.rglob('*'):
            if path.is_dir():path.chmod(0o755)
            elif path not in (engine,helper):path.chmod(0o755 if path.parent==destination/'usr/libexec/monitorize-vkms' else 0o644)
    except Exception:
        shutil.rmtree(destination);raise
    print('Prepared service assets:',destination)
    print('No system files, capabilities, services or display settings were changed.')
    print('Host monitorize-vkms boot-display protocol v1 must be installed before enrollment.')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--sunshine',required=True);parser.add_argument('--assets',required=True)
    parser.add_argument('--vkms-source',help='Include host Python support only; never rebuilds/replaces the kernel module.')
    args=parser.parse_args()
    try:prepare(args.output,args.sunshine,args.assets,vkms_source=args.vkms_source)
    except (ValueError,OSError) as error:parser.exit(1,str(error)+'\n')
