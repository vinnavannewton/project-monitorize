"""Briefly damage a KDE virtual output while its screencast attaches."""

import argparse
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class WakeupWindow(QWidget):
    def __init__(self, screen):
        super().__init__(
            None,
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self._light = False
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.showFullScreen()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(16)

    def _advance(self):
        self._light = not self._light
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(1, 1, 1) if self._light else Qt.black)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--duration-ms", type=int, default=2500)
    args = parser.parse_args()

    app = QApplication([])
    screen = next((item for item in app.screens() if item.name() == args.output), None)
    if screen is None:
        print(json.dumps({"event": "error", "message": f"output {args.output} not found"}), flush=True)
        return 1

    window = WakeupWindow(screen)
    QTimer.singleShot(
        100,
        lambda: print(json.dumps({"event": "ready"}), flush=True),
    )
    QTimer.singleShot(max(250, args.duration_ms), app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
