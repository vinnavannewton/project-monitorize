#!/usr/bin/env python3
"""Finish a source update only when enrolled remote-login assets match its engine."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linux'))
from monitorize.remote_login.capability import ENGINE,matching_engines


def check_update(source,assets,*,settings=Path('/etc/monitorize/remote-login.json'),
                 installed=ENGINE,destination=None):
    """Stage only; retain active services, enrollment, pairing and package ownership."""
    try:
        values=json.loads(Path(settings).read_text())
    except FileNotFoundError:return 0
    except (OSError,ValueError):
        print('Could not verify remote-login enrollment. Check its system setup before using boot streaming.',file=sys.stderr)
        return 1
    if not isinstance(values,dict):
        print('Invalid remote-login enrollment; repair system setup.',file=sys.stderr)
        return 1
    if values.get('enabled') is not True or values.get('uid')!=os.getuid():return 0
    if matching_engines(source,installed):return 0
    print('Desktop updated; remote-login system assets still need updating.',flush=True)
    print('Open Monitorize Settings and choose Repair remote login for graphical administrator authorization. Manual fallback follows.',flush=True)
    print('The privileged engine differs from this source build; desktop login may fail with your selected encoder.',flush=True)
    spec=importlib.util.spec_from_file_location('remote_login_installer',ROOT/'scripts/install-remote-login-assets.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    if installer.package_owner(installed):
        print('The remote-login engine belongs to a native package. Upgrade the matching Monitorize package '
              '(CUDA-enabled for NVIDIA) through the package manager. Source installation cannot replace package-owned assets.',flush=True)
        return 1
    spec=importlib.util.spec_from_file_location('stage_remote_login',ROOT/'scripts/stage-remote-login.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if destination is None:
        import secrets,tempfile
        destination=Path(tempfile.gettempdir())/('monitorize-remote-login-'+secrets.token_hex(8))
    module.prepare(destination,source,assets)
    print('Complete the printed administrator install commands, then restart the services or reboot. '
          'Keep the existing enrollment and pairing; do not enable setup again.',flush=True)
    return 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sunshine',type=Path,required=True)
    parser.add_argument('--assets',type=Path,required=True)
    args=parser.parse_args()
    try:sys.exit(check_update(args.sunshine,args.assets))
    except (OSError,ValueError) as error:parser.exit(1,str(error)+'\n')
