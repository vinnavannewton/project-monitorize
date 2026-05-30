import sys
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt6.QtGui import QFont, QFontDatabase, QImage, QPainter
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

lbl1 = QLabel("normal: 🔌 📶 📱")
lbl1.setStyleSheet("font-size: 32px;")

lbl2 = QLabel("bold: 🔌 📶 📱")
lbl2.setStyleSheet("font-size: 32px; font-weight: 700;")

w = QWidget()
l = QVBoxLayout(w)
l.addWidget(lbl1)
l.addWidget(lbl2)

w.show()
w.adjustSize()
img = QImage(w.size(), QImage.Format.Format_ARGB32)
w.render(img)
img.save("/tmp/test_emoji_bold.png")

app.quit()
