import os
import unittest
from unittest.mock import patch

from monitorize.platform.utils import detect_desktop_environment


class DesktopDetectionTest(unittest.TestCase):
    def test_cosmic_desktop_identifiers_are_case_insensitive(self):
        for value in ("COSMIC", "cosmic", "Cosmic"):
            for variable in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
                with self.subTest(value=value, variable=variable):
                    with patch.dict(os.environ, {variable: value}, clear=True):
                        self.assertEqual(detect_desktop_environment(), "cosmic")

    def test_cosmic_in_multi_desktop_identifier(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "COSMIC:GNOME"}, clear=True):
            self.assertEqual(detect_desktop_environment(), "cosmic")

    def test_current_desktop_takes_priority_over_stale_session_desktop(self):
        with patch.dict(os.environ, {
            "XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_DESKTOP": "cosmic",
        }, clear=True):
            self.assertEqual(detect_desktop_environment(), "kde")

    def test_linux_mint_cinnamon_wayland(self):
        with patch.dict(os.environ, {
            "XDG_CURRENT_DESKTOP": "X-Cinnamon",
            "DESKTOP_SESSION": "cinnamon-wayland",
        }, clear=True):
            self.assertEqual(detect_desktop_environment(), "cinnamon")

    def test_linux_mint_cinnamon_x11(self):
        with patch.dict(os.environ, {
            "XDG_CURRENT_DESKTOP": "X-Cinnamon",
            "DESKTOP_SESSION": "cinnamon",
        }, clear=True):
            self.assertEqual(detect_desktop_environment(), "cinnamon")


if __name__ == "__main__":
    unittest.main()
