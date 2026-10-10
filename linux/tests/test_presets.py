"""Focused preset save/readiness regressions."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitorize.desktop.backend import MonitorizeBackend


class PresetTest(unittest.TestCase):
    def test_save_waits_for_primary_and_requested_second_display(self):
        controller = SimpleNamespace(
            streaming=True, primary_ready=False, third_ready=False,
            pending_options={"second": {"enabled": True}},
        )
        backend = SimpleNamespace(streaming=controller, session=SimpleNamespace(busy=True))
        can_save = MonitorizeBackend.canSavePreset.fget
        self.assertFalse(can_save(backend))
        controller.primary_ready = True
        backend.session.busy = False
        self.assertFalse(can_save(backend))
        controller.third_ready = True
        self.assertTrue(can_save(backend))
        controller.pending_options = None
        controller.third_ready = False
        self.assertTrue(can_save(backend))

    def test_backend_rejects_save_during_startup_and_replaces_selected_preset(self):
        original = {"name": "Work", "version": 2, "primary": {}, "second": {"enabled": False}}
        controller = SimpleNamespace(streaming=True, active_configuration=Mock(return_value={
            "version": 2, "primary": {"resolution": "2560x1440"},
            "second": {"enabled": False},
        }))
        backend = SimpleNamespace(
            canSavePreset=False, streaming=controller, _presets=[original],
            presetsChanged=SimpleNamespace(emit=Mock()),
        )
        save = MonitorizeBackend.saveCurrentPreset
        self.assertIn("Wait", save(backend, "Work", 0))
        controller.active_configuration.assert_not_called()
        backend.canSavePreset = True
        with (patch("monitorize.desktop.backend.save_presets") as persist,
              patch("monitorize.desktop.backend.load_presets", side_effect=lambda: backend._presets)):
            self.assertIn("already exists", save(backend, "Work", -1))
            self.assertEqual(save(backend, "Work", 0), "")
            persist.assert_called_once()
        self.assertEqual(len(backend._presets), 1)
        self.assertEqual(backend._presets[0]["primary"]["resolution"], "2560x1440")
        backend.presetsChanged.emit.assert_called_once()
