#!/usr/bin/env python3
"""Load the real QML/backend offscreen without starting streams or setup."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PyQt6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler
from PyQt6.QtQml import QQmlComponent, QQmlExpression
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtWidgets import QApplication

from monitorize.desktop.backend import MonitorizeBackend


def settle():
    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)
    loop.exec()


def main():
    errors = []

    def message_handler(kind, context, message):
        print(message, file=sys.stderr)
        if message.startswith(("file:", "qrc:")) or "ReferenceError:" in message or "TypeError:" in message:
            errors.append(message)

    qml = Path(__file__).resolve().parents[1] / "linux/monitorize/qml"
    with tempfile.TemporaryDirectory(prefix="monitorize-qml-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory
        app = QApplication([])
        previous_handler = qInstallMessageHandler(message_handler)
        with (
            patch("monitorize.desktop.backend.get_local_ip", return_value="192.0.2.1"),
            patch("monitorize.desktop.backend.find_sunshine_command", return_value=None),
            patch("monitorize.desktop.backend.get_system_setup_status", return_value={"available": False}),
        ):
            backend = MonitorizeBackend("sway")
            view = QQuickWidget()
            try:
                view.resize(860, 820)
                view.rootContext().setContextProperty("backend", backend)
                for path in sorted(qml.glob("*.qml")):
                    component = QQmlComponent(view.engine(), QUrl.fromLocalFile(str(path)))
                    if component.isError():
                        errors.extend(error.toString() for error in component.errors())
                view.setSource(QUrl.fromLocalFile(str(qml / "main.qml")))
                view.show()
                settle()
                if view.status() != QQuickWidget.Status.Ready or view.rootObject() is None:
                    errors.extend(error.toString() for error in view.errors())
                    errors.append("main.qml did not produce a ready root object")
                else:
                    for page in ("StreamingPage.qml", "PresetsPage.qml", "SettingsPage.qml", "DisplaySetupPage.qml"):
                        expression = QQmlExpression(view.rootContext(), view.rootObject(), f'navigate("{page}")')
                        expression.evaluate()
                        if expression.hasError():
                            errors.append(expression.error().toString())
                        settle()
            finally:
                # Destroy QML objects while their backend/context still exists.
                view.setSource(QUrl())
                view.close()
                backend.close()
                app.processEvents()
                qInstallMessageHandler(previous_handler)
        if errors:
            raise SystemExit("QML smoke check failed:\n" + "\n".join(errors))
        print("All QML components loaded; four application pages opened successfully.")


if __name__ == "__main__":
    main()
