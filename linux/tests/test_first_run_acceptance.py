import runpy
import unittest
from pathlib import Path

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/check-first-run-acceptance.py'))

class AcceptanceGateTests(unittest.TestCase):
    def test_missing_or_stale_report_cannot_publish(self):
        for report in ({}, {'commit':'old'}):
            with self.assertRaises(ValueError): MODULE['validate'](report, 'current', ['fedora-44'])

    def test_all_cases_require_explicit_pass_and_evidence(self):
        entry = {'evidence':'https://example.test/report', 'cases':dict.fromkeys(MODULE['CASES'], True)}
        report = {'commit':'current','targets':{'fedora-44':entry}}
        MODULE['validate'](report,'current',['fedora-44'])
        entry['cases']['input_after_login'] = False
        with self.assertRaises(ValueError): MODULE['validate'](report,'current',['fedora-44'])

    def test_every_enabled_target_is_required(self):
        with self.assertRaises(ValueError):
            MODULE['validate']({'commit':'current','targets':{}},'current',['arch','fedora-44'])
