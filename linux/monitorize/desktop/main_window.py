"""Monitorize desktop window and compatibility entrypoint."""

import os
import re
import shutil
import sys

from PyQt6.QtCore import Qt, QProcess, QTimer, QUrl
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from monitorize.config import app_log
from monitorize.desktop.backend import MonitorizeBackend
from monitorize.platform.process_utils import kill_patterns
from monitorize.platform.utils import ASSETS_DIR, LINUX_DIR, QML_DIR, detect_desktop_environment


class ReceiverVideoWindow(QWidget):
    SYNC_DELAYS_MS = (0, 50, 100, 250, 500, 1000)

    def __init__(self, backend, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint,
        )
        self.backend = backend
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: #000000;")
        self.setWindowTitle("Monitorize Receiver")
        self.video_surface = QWidget(self)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setAttribute(
            Qt.WidgetAttribute.WA_DontCreateNativeAncestors, False
        )
        self.video_surface.setStyleSheet("background: #000000;")
        self.disconnect_button = QPushButton("Disconnect", self)
        self.disconnect_button.setFixedSize(116, 34)
        self.disconnect_button.setStyleSheet(
            "QPushButton {"
            "background: #991b1b; color: white; border: 0; border-radius: 6px;"
            "font-size: 12px; font-weight: 700;"
            "}"
            "QPushButton:hover { background: #b91c1c; }"
            "QPushButton:pressed { background: #7f1d1d; }"
        )
        self.disconnect_button.clicked.connect(self.backend.stopReceiving)

    def show_receiver(self):
        self._move_to_parent_screen()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._bind_receiver_video_surface()

    def hide_receiver(self):
        self.backend.setReceiverVideoItem(None)
        self.hide()

    def _move_to_parent_screen(self):
        parent = self.parentWidget()
        parent_handle = parent.windowHandle() if parent is not None else None
        screen = parent_handle.screen() if parent_handle is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

    def sync_video_geometry(self):
        self.video_surface.setGeometry(
            0, 0, max(1, self.width()), max(1, self.height())
        )
        self.backend.receiver.sync_video_geometry()

    def _bind_receiver_video_surface(self, attempt=0):
        if not self.backend.isReceiving or not self.isVisible():
            return
        self.sync_video_geometry()
        if (
            (self.video_surface.width() <= 1 or self.video_surface.height() <= 1)
            and attempt < 10
        ):
            QTimer.singleShot(
                50, lambda: self._bind_receiver_video_surface(attempt + 1)
            )
            return
        self.backend.setReceiverVideoItem(self.video_surface)
        for delay in self.SYNC_DELAYS_MS:
            QTimer.singleShot(delay, self.sync_video_geometry)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.backend.stopReceiving()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        self.sync_video_geometry()
        margin = 18
        self.disconnect_button.move(
            max(margin, self.width() - self.disconnect_button.width() - margin),
            margin,
        )
        self.disconnect_button.raise_()
        super().resizeEvent(event)


class MonitorizeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monitorize")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)
        icon = os.path.join(ASSETS_DIR, "monitorize_desktop_logo.png")
        if os.path.exists(icon):
            self.app_icon = QIcon(icon)
            self.setWindowIcon(self.app_icon)
        else:
            self.app_icon = QIcon()
        kill_patterns(
            "gst-launch-1.0.*port=7110",
            "gst-launch-1.0.*port=7112",
            "gst-launch-1.0.*port=7114",
            "gst-launch-1.0.*port=7115",
            "monitorize\\.streaming\\.Streamer_.*",
            "monitorize\\.security\\.tls_proxy",
        )
        self.de = detect_desktop_environment() or self._ask_desktop_environment()
        self.backend = MonitorizeBackend(self.de, self)
        self.backend.configureDisplayRequested.connect(self._configure_display)
        self._setup_tray()
        self.content_stack = QStackedWidget(self)
        self.quick_widget = QQuickWidget(self)
        self.quick_widget.setResizeMode(
            QQuickWidget.ResizeMode.SizeRootObjectToView
        )
        self.quick_widget.rootContext().setContextProperty("backend", self.backend)
        self.quick_widget.setSource(
            QUrl.fromLocalFile(os.path.join(QML_DIR, "main.qml"))
        )
        for error in self.quick_widget.errors():
            print(error.toString())
        self.content_stack.addWidget(self.quick_widget)
        self.setCentralWidget(self.content_stack)
        self.receiver_video_window = ReceiverVideoWindow(self.backend, self)
        self.backend.isReceivingChanged.connect(self._sync_receiver_fullscreen)

    def _sync_receiver_fullscreen(self, receiving):
        if receiving:
            if not self.backend.receiver.should_use_embedded_window():
                return
            self.receiver_video_window.show_receiver()
            return
        self.receiver_video_window.hide_receiver()

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(
            os.path.join(ASSETS_DIR, "tray", "icon_tray_white.svg")
        ))
        self.tray.setToolTip("Monitorize")
        menu = QMenu()
        menu.addAction("Show").triggered.connect(self._restore_from_tray)
        self.presets_menu = menu.addMenu("Presets")
        self._update_tray_presets()
        self.backend.presetsChanged.connect(self._update_tray_presets)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)

    def _update_tray_presets(self):
        self.presets_menu.clear()
        presets = self.backend.presets
        if not presets:
            action = self.presets_menu.addAction("No saved presets")
            action.setEnabled(False)
            return
        for index, preset in enumerate(presets):
            action = self.presets_menu.addAction(preset["name"])
            action.triggered.connect(
                lambda _checked=False, preset_index=index:
                    self.backend.launchPreset(preset_index)
            )

    def _tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.tray.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        app_log.write("APP", "Application shutting down.")
        self.backend.close()
        app_log.close()
        QApplication.quit()

    def _quit_to_tray_agent(self):
        result = QProcess.startDetached(
            sys.executable,
            ["-m", "monitorize", "--tray-agent"],
            LINUX_DIR,
        )
        started = result[0] if isinstance(result, tuple) else result
        if started:
            self._quit_app()
            return True
        return False

    def _configure_display(self):
        if shutil.which("nwg-displays") is None:
            QMessageBox.warning(
                self, "nwg-displays Not Installed", "nwg-displays is not installed."
            )
            return
        if not QProcess.startDetached("nwg-displays"):
            QMessageBox.warning(self, "Error", "Failed to run nwg-displays.")

    def closeEvent(self, event):
        minimize = self.backend.should_minimize_to_tray()
        if minimize and QSystemTrayIcon.isSystemTrayAvailable():
            idle = not self.backend.isStreaming and not self.backend.isReceiving
            if idle and self._quit_to_tray_agent():
                event.accept()
                return
            event.ignore()
            self.hide()
            self.tray.show()
            return
        self._quit_app()
        event.accept()

    def _ask_desktop_environment(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Desktop Environment")
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "Could not automatically detect your desktop environment.\n"
            "Please select which one you are running:"
        )
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        selected = {"value": "gnome"}

        def add_row(options):
            row = QHBoxLayout()
            for text, value in options:
                button = QPushButton(text)
                button.clicked.connect(
                    lambda _checked=False, choice=value: (
                        selected.update(value=choice), dialog.accept()
                    )
                )
                row.addWidget(button)
            layout.addLayout(row)

        add_row((("KDE Plasma", "kde"), ("GNOME", "gnome")))
        add_row((("Hyprland", "hyprland"),))
        other = QPushButton("Other (WIP)")
        other.clicked.connect(
            lambda: (selected.update(value="other"), dialog.accept())
        )
        layout.addWidget(other)
        dialog.exec()
        if selected["value"] == "other":
            QMessageBox.information(
                self,
                "Work In Progress",
                "Support for other environments is coming soon. The app will close now.",
            )
            sys.exit(0)
        return selected["value"]


def load_theme_color(property_name, default):
    try:
        with open(os.path.join(QML_DIR, "Theme.qml")) as theme:
            match = re.search(
                rf'property\s+color\s+{property_name}\s*:\s*"([^"]+)"',
                theme.read(),
            )
            return match.group(1) if match else default
    except OSError:
        return default


def _set_palette(app):
    palette = QPalette()
    colors = (
        (QPalette.ColorRole.Window, "background", "#1b1e24"),
        (QPalette.ColorRole.WindowText, "textLight", "#eff0f1"),
        (QPalette.ColorRole.Base, "surface", "#232831"),
        (QPalette.ColorRole.AlternateBase, "surfaceAlt", "#2b313b"),
        (QPalette.ColorRole.Button, "surfaceAlt", "#2b313b"),
        (QPalette.ColorRole.ButtonText, "textLight", "#eff0f1"),
        (QPalette.ColorRole.Highlight, "accent", "#3daee9"),
        (QPalette.ColorRole.HighlightedText, "textPrimary", "#eff0f1"),
    )
    for role, name, default in colors:
        palette.setColor(role, QColor(load_theme_color(name, default)))
    app.setPalette(palette)


def _start_in_tray_requested(argv):
    return "--start-in-tray" in argv


def _launch_preset_index(argv):
    try:
        index = argv.index("--launch-preset")
        return int(argv[index + 1])
    except (ValueError, IndexError):
        return None


def _show_initial_window(window, start_in_tray):
    if start_in_tray and QSystemTrayIcon.isSystemTrayAvailable():
        QApplication.setQuitOnLastWindowClosed(False)
        window.tray.show()
        app_log.write("APP", "Started hidden in system tray.")
        return False
    window.show()
    return True


def _handle_instance_command(server, window):
    connection = server.nextPendingConnection()
    command = ""
    if connection.waitForReadyRead(250):
        command = bytes(connection.readAll()).decode("utf-8", "ignore")
    connection.deleteLater()
    if command == "show":
        window._restore_from_tray()
    elif command.startswith("preset:"):
        try:
            window.backend.launchPreset(int(command.split(":", 1)[1]))
        except ValueError:
            pass


def _instance_command(start_in_tray, preset_index):
    if preset_index is not None:
        return f"preset:{preset_index}".encode()
    return b"noop" if start_in_tray else b"show"


def main():
    start_in_tray = _start_in_tray_requested(sys.argv)
    preset_index = _launch_preset_index(sys.argv)
    log_path = app_log.configure()
    app_log.install_exception_hook()
    app_log.write("APP", f"Application starting. Log file: {log_path}")
    app = QApplication(sys.argv)
    app.setApplicationName("Monitorize")
    app.setDesktopFileName("monitorize")
    socket = QLocalSocket()
    socket.connectToServer("monitorize")
    if socket.waitForConnected(250):
        app_log.write("APP", "Existing instance activated.")
        socket.write(_instance_command(start_in_tray, preset_index))
        socket.waitForBytesWritten(250)
        return
    QLocalServer.removeServer("monitorize")
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen("monitorize"):
        app_log.write("APP", "Failed to create single-instance server.")
        return
    _set_palette(app)
    window = MonitorizeWindow()
    server.newConnection.connect(
        lambda: _handle_instance_command(server, window)
    )
    _show_initial_window(window, start_in_tray)
    if preset_index is not None:
        QTimer.singleShot(0, lambda: window.backend.launchPreset(preset_index))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
