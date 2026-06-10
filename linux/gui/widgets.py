
"""
Monitorize GUI — Reusable custom widgets.
"""

from PyQt6.QtWidgets import QComboBox, QScrollArea, QWidget, QFrame
from PyQt6.QtCore import Qt


class NonScrollComboBox(QComboBox):
    """A QComboBox that ignores mouse wheel events to prevent accidental setting changes while scrolling the panel."""
    def wheelEvent(self, event):
        event.ignore()


def make_scrollable(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return scroll
