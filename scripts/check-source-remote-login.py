#!/usr/bin/env python3
"""Finish a source update only when enrolled remote-login assets match its engine."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linux'))
from monitorize.remote_login.capability import ENGINE,matching_engines


def check_update(source, assets, *, settings=Path('/etc/monitorize/remote-login.json'), installed=ENGINE):
    """Update enrolled service assets through the installed repair helper."""
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
    from monitorize.platform.remote_login_repair import repair_remote_login
    print('Updating remote-login service files. Approve the administrator prompt if asked.', flush=True)
    result = repair_remote_login(repository=ROOT, terminal=True, bundle=(source, assets))
    if not result.get('success'):
        print(result['message'], file=sys.stderr)
        return 1
    if not matching_engines(source, installed):
        print('Remote-login repair completed, but the installed engine still differs from the build.', file=sys.stderr)
        return 1
    print(result['message'], flush=True)
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sunshine',type=Path,required=True)
    parser.add_argument('--assets',type=Path,required=True)
    args=parser.parse_args()
    try:sys.exit(check_update(args.sunshine,args.assets))
    except (OSError,ValueError) as error:parser.exit(1,str(error)+'\n')
