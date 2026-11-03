"""Actual QML keeps transaction explanations above firewall scope warnings."""
import os
import subprocess
import sys
import unittest

from linux.tests.test_system_setup_completion_ui import PROGRAM


class InterruptedMessageTests(unittest.TestCase):
    def test_authoritative_state_survives_initial_and_late_scope_warnings(self):
        for busy, interrupted, expected in ((True, False, 'still running'),
                                             (False, True, 'interrupted')):
            with self.subTest(busy=busy, interrupted=interrupted):
                status = dict(available=True, busy=busy, interrupted=interrupted,
                              input_ready=False, logout_required=False,
                              completed_actions={'firewall': 'old success'},
                              firewall_scope_current=False,
                              firewall_scope_message='generic firewall warning')
                program = PROGRAM.split("page.setProperty('applying',True)")[0]
                program = program.replace(
                    "def getSystemSetupStatus(self):return dict(available=True,input_ready=False,input_configured=True,logout_required=True,input_message='Log out and back in to enable input.')",
                    'def getSystemSetupStatus(self):return self.status')
                program = program.replace('backend=Backend()',
                                          'backend=Backend();backend.status=' + repr(status))
                program += "\nassert " + repr(expected) + " in page.property('statusMessage')\n"
                program += "assert not page.property('statusSucceeded')\n"
                program += "page.receiveScope({'zones':[], 'scope_current':False, 'message':'late generic firewall warning'});app.processEvents()\n"
                program += "assert " + repr(expected) + " in page.property('statusMessage')\n"
                result = subprocess.run(
                    [sys.executable, '-c', program],
                    env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                         'QT_QUICK_BACKEND': 'software', 'PYTHONDONTWRITEBYTECODE': '1'},
                    text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
