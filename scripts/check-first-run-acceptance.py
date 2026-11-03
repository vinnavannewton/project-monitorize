#!/usr/bin/env python3
"""Reject native publication without installed-package acceptance for its commit.

MONITORIZE_FIRST_RUN_ACCEPTANCE is a repository variable containing JSON:
{"commit":"<full tag commit>","targets":{"fedora-45":{"evidence":"<report URL>",
"cases":{"authorization":true,...}}}}
Every enabled target and every required case must have a recorded pass.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path

CASES = ('authorization', 'input_after_login', 'firewall_runtime',
         'firewall_persistent', 'moonlight_input', 'partial_retry',
         'interrupted_reopen', 'upgrade')


def validate(report, commit, targets):
    if not isinstance(report, dict) or report.get('commit') != commit:
        raise ValueError('First-run acceptance is missing or belongs to another commit.')
    results = report.get('targets', {})
    for target in targets:
        entry = results.get(target, {})
        if not entry.get('evidence') or any(entry.get('cases', {}).get(case) is not True for case in CASES):
            raise ValueError('Installed-package acceptance is incomplete for ' + target + '.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('package_assets', Path(__file__).with_name('package-assets.py'))
    assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assets)
    try:
        report = json.loads(os.environ.get('MONITORIZE_FIRST_RUN_ACCEPTANCE', '{}'))
        validate(report, args.commit, [name for name, target in assets.TARGETS.items() if target.cd_enabled])
    except (ValueError, TypeError, AttributeError) as exc:
        parser.exit(1, str(exc) + ' Record the per-distro VM report in MONITORIZE_FIRST_RUN_ACCEPTANCE before publication.\n')


if __name__ == '__main__':
    main()
