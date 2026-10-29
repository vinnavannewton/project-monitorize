#!/usr/bin/env python3
"""Unprivileged launcher for the two fixed source-installer maintenance actions."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linux'))
from monitorize.platform.polkit_agent import run_authorized


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('cache','legacy-cleanup'))
    action=parser.parse_args().action
    pkexec=shutil.which('pkexec')
    if not pkexec:print('Polkit (pkexec) is required for source maintenance.',file=sys.stderr);return 1
    result=run_authorized([pkexec,'/usr/bin/python3','-I',str(ROOT/'scripts/source-maintenance.py'),action],
                          capture_output=True,text=True,check=False)
    if result.stdout:print(result.stdout.strip())
    if result.stderr:print(result.stderr.strip(),file=sys.stderr)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
