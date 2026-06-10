"""
Monitorize GUI — Active streaming page.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor


class StreamingPage(QWidget):
    """
    Active streaming view.
    Shows a live log box fed from both QProcess stdout streams.
    """

    def __init__(self, on_stop, on_configure, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 36)
        root.setSpacing(0)

        self._status_lbl = QLabel("Starting virtual monitor\u2026")
        self._status_lbl.setObjectName("streaming")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_lbl)
        root.addSpacing(6)

        hint = QLabel(
            "Live output from both processes is shown below."
        )
        hint.setObjectName("statusLbl")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addSpacing(16)

        self.log = QPlainTextEdit()
        self.log.setObjectName("logBox")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        root.addWidget(self.log, stretch=1)
        root.addSpacing(20)

        self._stop_btn = QPushButton("\u23f9  Stop Streaming")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(on_stop)

        self._configure_btn = QPushButton("\u2699  Configure Display")
        self._configure_btn.clicked.connect(on_configure)
        self._configure_btn.setVisible(False)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._stop_btn)
        row.addWidget(self._configure_btn)
        row.addStretch()
        root.addLayout(row)
        root.addSpacing(16)

    def set_status(self, text: str):
        self._status_lbl.setText(text)

    def set_stop_enabled(self, enabled: bool):
        self._stop_btn.setEnabled(enabled)

    def set_configure_visible(self, visible: bool):
        self._configure_btn.setVisible(visible)

    def append_log(self, prefix: str, text: str):
        """Append a labelled line to the log and auto-scroll."""
        for line in text.splitlines():
            if line.strip():
                self.log.appendPlainText(f"[{prefix}] {line}")
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def clear_log(self):
        self.log.clear()
