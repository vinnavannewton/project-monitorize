import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon, QPixmap

app = QApplication(sys.argv)
win = QMainWindow()
tray = QSystemTrayIcon(win)
pix = QPixmap(32, 32)
pix.fill()
tray.setIcon(QIcon(pix))
tray.show()

def do_quit():
    print("Quitting...")
    app.quit()


QTimer.singleShot(1000, do_quit)

win.show()
print("Exec start")
sys.exit(app.exec())
