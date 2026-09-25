import os
import unittest
from unittest.mock import patch

from monitorize.platform.utils import detect_desktop_environment


class DesktopDetectionTest(unittest.TestCase):
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
