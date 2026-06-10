
"""
Monitorize GUI — Dark theme QSS stylesheet.
"""

DARK_QSS = """
/* ── Base ─────────────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #0c0d14;
    color: #d4d6f0;
    font-family: 'Inter', 'SF Pro Display', 'Segoe UI', sans-serif;
    font-size: 14px;
}

/* ── Generic Button ───────────────────────────────────────────────── */
QPushButton {
    background-color: #16182a;
    color: #b8bad8;
    border: 1px solid #252845;
    border-radius: 10px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #1e2040;
    border-color: #4a4faa;
    color: #e8e9ff;
}
QPushButton:pressed { background-color: #121428; }
QPushButton:disabled {
    background-color: #101220;
    color: #3e3f5a;
    border-color: #1a1c30;
}

/* ── Mode Buttons (USB / Wi-Fi) ───────────────────────────────────── */
QPushButton#modeBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #141630, stop:1 #1c1e3a);
    border: 1px solid #2a2d55;
    border-radius: 16px;
    font-size: 18px;
    font-weight: 700;
    color: #c0c2ee;
    min-width: 200px;
    min-height: 110px;
}
QPushButton#modeBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #1c1e42, stop:1 #24264e);
    border-color: #5458b8;
    color: #ffffff;
}

/* ── Primary Action Button ────────────────────────────────────────── */
QPushButton#primaryBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #3538b0, stop:1 #4c4fd0);
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    min-height: 50px;
    min-width: 240px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #4042c8, stop:1 #5c5ee8);
}
QPushButton#primaryBtn:pressed { background-color: #2a2c98; }

/* ── Stop Button ──────────────────────────────────────────────────── */
QPushButton#stopBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #7a1520, stop:1 #a82028);
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    min-height: 50px;
    min-width: 240px;
}
QPushButton#stopBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #941a24, stop:1 #c42830);
}
QPushButton#stopBtn:pressed { background-color: #5a1010; }

/* ── Back Button ──────────────────────────────────────────────────── */
QPushButton#backBtn {
    background-color: transparent;
    border: 1px solid #252845;
    border-radius: 10px;
    font-size: 13px;
    color: #6a6c90;
    padding: 8px 18px;
    min-width: 90px;
}
QPushButton#backBtn:hover { border-color: #4a4faa; color: #b0b2d8; }

/* ── Labels ───────────────────────────────────────────────────────── */
QLabel#titleLabel {
    font-size: 30px;
    font-weight: 800;
    color: #e0e2ff;
    letter-spacing: 2px;
}
QLabel#subLabel    { font-size: 14px; color: #6a6c96; font-weight: 400; }
QLabel#stepLabel   { font-size: 12px; color: #5a5c82; font-weight: 500; letter-spacing: 1px; }
QLabel#instruction { font-size: 15px; color: #b0b2d0; }
QLabel#wip         { font-size: 17px; color: #7878aa; font-style: italic; }
QLabel#streaming   { font-size: 20px; font-weight: 700; color: #4cd68d; }
QLabel#statusLbl   { font-size: 12px; color: #5a5c82; }

/* ── Log Box ──────────────────────────────────────────────────────── */
QPlainTextEdit#logBox {
    background-color: #080910;
    color: #7cc87c;
    border: 1px solid #1a1c30;
    border-radius: 8px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
    padding: 8px;
    selection-background-color: #2a2d55;
}

/* ── Separator ────────────────────────────────────────────────────── */
QFrame#sep {
    background-color: #1a1c30;
    max-height: 1px;
}

/* ── Combo Box ────────────────────────────────────────────────────── */
QComboBox {
    background-color: #12142a;
    color: #c0c2ee;
    border: 1px solid #2a2d55;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    min-width: 140px;
}
QComboBox:hover { border-color: #5458b8; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #12142a;
    color: #c0c2ee;
    selection-background-color: #3538b0;
    selection-color: #ffffff;
    border: 1px solid #2a2d55;
    border-radius: 6px;
    padding: 4px;
}

/* ── Custom Input ─────────────────────────────────────────────────── */
QLineEdit#customInput {
    background-color: #12142a;
    color: #c0c2ee;
    border: 1px solid #2a2d55;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 600;
    min-width: 80px;
    max-width: 90px;
}
QLineEdit#customInput:focus { border-color: #5458b8; }
QLineEdit#customInput[invalid="true"] {
    border-color: #a82028;
    color: #ff6060;
}

QLabel#xSep {
    font-size: 18px;
    font-weight: 700;
    color: #6a6c96;
    padding: 0 4px;
}
QLabel#customHint {
    font-size: 11px;
    color: #4a4c70;
    font-style: italic;
}

/* ── Warning Label ────────────────────────────────────────────────── */
QLabel#warningLabel {
    font-size: 12px;
    font-weight: 600;
    color: #e8a840;
    padding: 8px 14px;
    background-color: rgba(232, 168, 64, 0.06);
    border: 1px solid rgba(232, 168, 64, 0.18);
    border-radius: 8px;
}

/* ── DE Badge ─────────────────────────────────────────────────────── */
#deBadge {
    background-color: rgba(76, 79, 208, 0.08);
    border: 1px solid rgba(76, 79, 208, 0.16);
    border-radius: 14px;
}
#deBadge QLabel {
    font-size: 12px;
    font-weight: 600;
    color: #6a6cbb;
    background: transparent;
    border: none;
}

/* ── Portal Hint ──────────────────────────────────────────────────── */
QLabel#portalHint {
    font-size: 14px;
    font-weight: 500;
    color: #8a8cc0;
}

/* ── Checkboxes ───────────────────────────────────────────────────── */
QCheckBox#trayCheck, QCheckBox#touchCheck {
    font-size: 12px;
    color: #5a5c82;
    spacing: 8px;
}
QCheckBox#trayCheck:hover, QCheckBox#touchCheck:hover { color: #9a9cc0; }
QCheckBox#trayCheck::indicator, QCheckBox#touchCheck::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #2a2d55;
    border-radius: 4px;
    background-color: #12142a;
}
QCheckBox#trayCheck::indicator:checked, QCheckBox#touchCheck::indicator:checked {
    background-color: #4c4fd0;
    border-color: #4c4fd0;
}

/* ── Scrollbar ────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a2d55;
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #3a3d6a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ── Message Boxes / Dialogs ──────────────────────────────────────── */
QDialog {
    background-color: #0c0d14;
}
QMessageBox {
    background-color: #0c0d14;
}
QMessageBox QLabel {
    color: #d4d6f0;
}
"""
