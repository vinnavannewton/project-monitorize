import sys
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt6.QtGui import QFont, QFontDatabase, QImage, QPainter
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)
font = app.font()
families = font.families()
# Try prepending Noto Color Emoji to force it for all glyphs it supports
font.setFamilies(["Noto Color Emoji", "Inter", "SF Pro Display", "Segoe UI"] + families)
app.setFont(font)

lbl = QLabel("Does this work: 🔌 📶 📱 A B C D")
lbl.setStyleSheet("font-size: 32px;")

w = QWidget()
l = QVBoxLayout(w)
l.addWidget(lbl)

w.show()
w.adjustSize()
img = QImage(w.size(), QImage.Format.Format_ARGB32)
w.render(img)
img.save("/tmp/test_noto_first.png")

app.quit()
