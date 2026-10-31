"""Exercise actual first-run QML completion and partial retry behavior."""
import os
import subprocess
import sys
import unittest

PROGRAM=r'''
from pathlib import Path
from PyQt6.QtCore import QObject,QUrl,QMetaObject,pyqtSignal,pyqtSlot
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQml import QQmlApplicationEngine
app=QApplication([])
class Backend(QObject):
    systemSetupFinished=pyqtSignal('QVariantMap')
    decided=0
    starts=[]
    @pyqtSlot(result='QVariantMap')
    def getSystemSetupStatus(self):return dict(available=True,input_ready=False,input_configured=True,logout_required=True,input_message='Log out and back in to enable input.')
    @pyqtSlot()
    def markSystemSetupDecided(self):self.decided+=1
    @pyqtSlot(bool,bool)
    def startSystemSetup(self,*args):self.starts.append(args)
backend=Backend()
engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('backend',backend)
engine.loadData(b''' + '"""' + '''import QtQuick
import QtQuick.Controls
Item {
 width: 560; height: 650
 Theme { id: theme }
 SystemSetupPage { id: setup; objectName: "setup"; anchors.fill: parent; firstRun: true }
}
''' + '"""' + ''' ,QUrl.fromLocalFile(str(Path('linux/monitorize/qml/SetupTest.qml').resolve())))
assert engine.rootObjects()
root=engine.rootObjects()[0];page=root.findChild(QObject,'setup')
assert not any(child.property('text')=='Enable unattended VKMS displays' for child in root.findChildren(QObject))
completed=[];page.setupCompleted.connect(lambda:completed.append(True))
page.setProperty('applying',True)
backend.systemSetupFinished.emit(dict(success=False,message='Firewall failed',logout_required=True,actions={'input':{'success':True,'message':'Input configured'},'firewall':{'success':False,'message':'Firewall failed'}}))
app.processEvents()
assert not completed and backend.decided==0
assert 'Log out' in page.property('statusMessage')
retry=root.findChild(QObject,'systemSetupAuthorizeButton')
assert retry and retry.property('enabled')
QMetaObject.invokeMethod(retry,'clicked');app.processEvents()
assert backend.starts==[(False,True)],backend.starts
backend.systemSetupFinished.emit(dict(success=True,message='Firewalld allows Monitorize in home.',logout_required=False))
app.processEvents()
assert not completed and backend.decided==0,'Completion closed before acknowledgement'
assert page.property('statusSucceeded')
assert 'Log out' in page.property('statusMessage'),'Partial retry lost pending logout notice'
done=root.findChild(QObject,'systemSetupDoneButton')
assert done and done.property('visible')
QMetaObject.invokeMethod(done,'clicked');app.processEvents()
assert completed==[True] and backend.decided==1
engine.deleteLater();app.processEvents()
'''

class SetupCompletionUiTests(unittest.TestCase):
    def test_result_survives_until_done_and_retry_skips_completed_input(self):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',PROGRAM],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
