import sys
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt6.QtGui import QFont, QFontDatabase, QImage, QPainter
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

lbl1 = QLabel("1. default app font: 🔌 📶 📱")
lbl1.setStyleSheet("font-size: 32px;")

lbl2 = QLabel("2. css with 'Noto Color Emoji': 🔌 📶 📱")
lbl2.setStyleSheet("font-family: 'Inter', 'SF Pro Display', 'Segoe UI', 'Noto Color Emoji', sans-serif; font-size: 32px;")

font = app.font()
families = font.families()
font.setFamilies(["Inter", "SF Pro Display", "Segoe UI"] + families)
app.setFont(font)
lbl3 = QLabel("3. app font modified: 🔌 📶 📱")
lbl3.setStyleSheet("font-size: 32px;")

w = QWidget()
l = QVBoxLayout(w)
l.addWidget(lbl1)
l.addWidget(lbl2)
l.addWidget(lbl3)

w.show()
w.adjustSize()
img = QImage(w.size(), QImage.Format.Format_ARGB32)
w.render(img)
img.save("/tmp/test_emoji2.png")

app.quit()
