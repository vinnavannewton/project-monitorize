"""Lightweight boot tray for Monitorize."""

import os
import sys

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from monitorize.config.settings import load_presets
from monitorize.platform.utils import ASSETS_DIR, LINUX_DIR


def _start_full_app(args=None):
    result = QProcess.startDetached(
        sys.executable,
        ["-m", "monitorize", *(args or [])],
        LINUX_DIR,
    )
    return result[0] if isinstance(result, tuple) else result


class TrayAgent:
    def __init__(self):
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(QIcon(
            os.path.join(ASSETS_DIR, "tray", "icon_tray_white.svg")
        ))
        self.tray.setToolTip("Monitorize")
        self.menu = QMenu()
        self.menu.addAction("Show").triggered.connect(self.show_app)
        self.presets_menu = self.menu.addMenu("Presets")
        self.presets_menu.aboutToShow.connect(self.refresh_presets)
        self.refresh_presets()
        self.menu.addSeparator()
        self.menu.addAction("Quit").triggered.connect(QApplication.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

    def refresh_presets(self):
        self.presets_menu.clear()
        presets = load_presets()
        if not presets:
            action = self.presets_menu.addAction("No saved presets")
            action.setEnabled(False)
            return
        for index, preset in enumerate(presets):
            action = self.presets_menu.addAction(preset["name"])
            action.triggered.connect(
                lambda _checked=False, preset_index=index:
                    self.launch_preset(preset_index)
            )

    def show(self):
        self.tray.show()

    def show_app(self):
        self._launch_and_quit([])

    def launch_preset(self, index):
        self._launch_and_quit([
            "--start-in-tray",
            "--launch-preset",
            str(index),
        ])

    def _launch_and_quit(self, args):
        if _start_full_app(args):
            QApplication.quit()

    def _activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_app()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setDesktopFileName("monitorize")
    QApplication.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        _start_full_app([])
        return
    agent = TrayAgent()
    agent.show()
    sys.exit(app.exec())
