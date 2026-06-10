"""
Monitorize GUI package — PyQt6 control panel.
Re-exports the public API so existing callers still work.
"""

from gui.styles import DARK_QSS
from gui.utils import (
    hr, vspace, _make_tray_icon, detect_desktop_environment, get_local_ip,
    LINUX_DIR,
)
from gui.widgets import NonScrollComboBox, make_scrollable
from gui.constants import PAGE_MAIN, PAGE_WIFI, PAGE_USB1, PAGE_USB2, PAGE_STREAMING
from gui.pages import MainMenuPage, WifiPage, UsbStep1Page, UsbStep2Page, StreamingPage
from gui.main_window import MonitorizeWindow, main
from gui import settings
