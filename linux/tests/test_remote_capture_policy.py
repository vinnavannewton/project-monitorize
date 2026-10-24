"""Auto remains saved as Auto while remote-login services use explicit KMS."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from monitorize.desktop.backend import MonitorizeBackend


class RemoteCapturePolicyTests(unittest.TestCase):
    def candidate(self,capture):
        return {'primary':{'sunshine_capture':capture},'second':{},'streaming_backend':'sunshine'}

    def validate(self,candidate):
        before=copy.deepcopy(candidate)
        backend=SimpleNamespace(_remote_login=SimpleNamespace(status={'desktop_options_version':1}),
            configuration_state=SimpleNamespace(applied=self.candidate('kms')))
        MonitorizeBackend._validate_remote_candidate(backend,candidate)
        self.assertEqual(candidate,before)

    def test_auto_and_kms_are_valid_without_rewriting_saved_choice(self):
        for capture in ('auto','kms'):self.validate(self.candidate(capture))

    def test_other_explicit_capture_methods_are_rejected(self):
        for capture in ('kwin','portal','wlr','x11','nvfbc','pipewire_node'):
            with self.assertRaisesRegex(ValueError,'KMS or Monitorize Auto'):
                self.validate(self.candidate(capture))
