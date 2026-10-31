"""Lightweight boot tray for Monitorize."""

import os
import sys

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from monitorize.platform.utils import ASSETS_DIR, LINUX_DIR

TRAY_SERVER = 'monitorize-tray-agent'


def dismiss_tray_agent():
    """Transfer tray ownership to the full app without touching its session."""
    socket = QLocalSocket()
    socket.connectToServer(TRAY_SERVER)
    if socket.waitForConnected(250):
        socket.write(b'quit')
        socket.waitForBytesWritten(250)
        return True
    return False


def _claim_tray(app):
    socket = QLocalSocket()
    socket.connectToServer(TRAY_SERVER)
    if socket.waitForConnected(250):
        return None
    QLocalServer.removeServer(TRAY_SERVER)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(TRAY_SERVER):
        return None

    def accept():
        while server.hasPendingConnections():
            peer = server.nextPendingConnection()

            def read(peer=peer):
                if bytes(peer.readAll()) == b'quit':
                    app.quit()

            peer.readyRead.connect(read)
            peer.disconnected.connect(peer.deleteLater)
            read()

    server.newConnection.connect(accept)
    return server


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
        self.menu.addAction("Open").triggered.connect(self.show_app)
        self.menu.addSeparator()
        self.menu.addAction("Quit").triggered.connect(QApplication.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

    def show(self):
        self.tray.show()

    def show_app(self):
        self._launch_and_quit(['--restore-from-tray'])

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
    server = _claim_tray(app)
    if server is None:
        return
    if not QSystemTrayIcon.isSystemTrayAvailable():
        _start_full_app([])
        return
    agent = TrayAgent()
    agent.show()
    sys.exit(app.exec())
