import sys
from PyQt6.QtCore import QObject, pyqtSignal, pyqtProperty, pyqtSlot, QVariant, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

class Backend(QObject):
    devicesChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._devices = [{'name': 'Init', 'ip': '1.1.1.1'}]

    @pyqtProperty('QVariant', notify=devicesChanged)
    def devices(self):
        
        return self._devices

    @pyqtSlot()
    def add(self):
        self._devices.append({'name': 'Added', 'ip': '2.2.2.2'})
        self.devicesChanged.emit()

app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
backend = Backend()
engine.rootContext().setContextProperty("backend", backend)
engine.loadData(b"""
import QtQuick
import QtQuick.Window

Window {
    visible: true
    width: 200
    height: 200
    Column {
        Repeater {
            id: rep
            model: backend.devices
            Text { text: modelData.name + " " + modelData.ip }
            onCountChanged: print("Repeater count changed to", count)
        }
    }
    Timer {
        interval: 1000
        running: true
        onTriggered: backend.add()
    }
    Connections {
        target: backend
        function onDevicesChanged() {
            rep.model = backend.devices
        }
    }
}
""")

QTimer.singleShot(3000, app.quit)
sys.exit(app.exec())
