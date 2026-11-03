"""Exercise keyboard pairing while an asynchronous request is pending."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class PairingUiTest(unittest.TestCase):
    def test_enter_during_verification_keeps_original_request_and_result(self):
        source = Path('linux/monitorize/qml/StreamingPage.qml').read_text()
        handlers = source[source.index('    function openPair(instance)'):source.index('    Connections {\n        target: backend\n        function onStreamingStartFailed')]
        popup_start = source.index('    Popup {\n        id: pinPopup')
        popup = source[popup_start:source.index('    Popup {\n        id: presetPopup', popup_start)]
        qml = ('import QtQuick\nimport QtQuick.Controls\nimport QtQuick.Layouts\nimport QtQuick.Window\n'
               'Window { id:page; visible:true; width:760; height:520;\n'
               'property int pairInstance:1; property int pairRequest:0; Theme { id:theme }\n'
               + handlers + popup + '\n}')
        program = r'''
from PyQt6.QtCore import QObject, QUrl, QMetaObject, Q_ARG, pyqtSignal, pyqtSlot, pyqtProperty
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtWidgets import QApplication
from pathlib import Path
app=QApplication([])
class Backend(QObject):
    pairingRunningChanged=pyqtSignal()
    pairMoonlightFinished=pyqtSignal(int,bool,str)
    calls=0
    running=False
    @pyqtProperty(bool,notify=pairingRunningChanged)
    def pairingRunning(self):return self.running
    @pyqtSlot(str,int,result=int)
    def startPairMoonlightPin(self,pin,instance):
        self.calls+=1
        if self.running:return 0
        self.running=True
        self.pairingRunningChanged.emit()
        return 17
backend=Backend()
engine=QQmlApplicationEngine()
engine.rootContext().setContextProperty('backend',backend)
'''
        program += 'engine.loadData(' + repr(qml.encode()) + ",QUrl.fromLocalFile(str(Path('linux/monitorize/qml/PairingRegression.qml').resolve())))\n"
        program += r'''
assert engine.rootObjects()
root=engine.rootObjects()[0]
QMetaObject.invokeMethod(root,'openPair',Q_ARG('QVariant',1))
app.processEvents()
pin=root.findChild(QObject,'pairMoonlightPinField')
button=root.findChild(QObject,'pairMoonlightSubmitButton')
pin.setProperty('text','1234')
QMetaObject.invokeMethod(pin,'accepted')
app.processEvents()
assert backend.calls==1
assert root.property('pairRequest')==17
assert not button.property('enabled')
QMetaObject.invokeMethod(pin,'accepted')
app.processEvents()
assert backend.calls==1,'Enter submitted the disabled Pair button'
assert root.property('pairRequest')==17,'Pending request identity was overwritten'
backend.running=False
backend.pairingRunningChanged.emit()
backend.pairMoonlightFinished.emit(17,False,'The pairing response was received.')
app.processEvents()
assert root.property('pairRequest')==0,'Original response was discarded'
assert any(item.property('text')=='The pairing response was received.' for item in root.findChildren(QObject))
root.close()
engine.deleteLater()
app.processEvents()
'''
        result = subprocess.run([sys.executable, '-c', program],
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
