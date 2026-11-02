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
assert done and not done.property('visible')
QMetaObject.invokeMethod(page,'completeSetup');app.processEvents()
assert completed==[True] and backend.decided==1
engine.deleteLater();app.processEvents()
'''

class SetupCompletionUiTests(unittest.TestCase):
    def test_success_shows_same_summary_regardless_of_activation_state(self):
        for activation in (False, True):
            program = PROGRAM.split("page.setProperty('applying',True)")[0]
            program = program.replace("input_ready=False,input_configured=True,logout_required=True,input_message='Log out and back in to enable input.'",
                                      "input_ready=" + str(not activation) + ",input_configured=True,logout_required=" + str(activation) + ",input_message=" + repr('Log out and back in to enable input.' if activation else 'Touch and input are ready.'))
            program += """
from PyQt6.QtCore import QTimer
summaries=[]
page.configurationSaved.connect(lambda:summaries.append(backend.decided))
page.setProperty('applying',True)
backend.systemSetupFinished.emit(dict(success=True,message='Firewall rules configured',actions={'firewall':dict(success=True,message='Firewall rules configured')}))
QTimer.singleShot(650,app.quit)
app.exec()
assert completed==[True] and backend.decided==1,(completed,backend.decided)
assert summaries==[1],summaries
""".replace('ACTIVATION',str(activation))
            result=subprocess.run([sys.executable,'-c',program],env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_completion_dialog_has_one_ok_action_that_requests_full_quit(self):
        from pathlib import Path
        source = Path('linux/monitorize/qml/main.qml').read_text()
        start = source.index('    Dialog {\n        id: setupActivationDialog')
        end = source.index('    Popup {\n        id: firstRunCancelPopup', start)
        qml = 'import QtQuick\nimport QtQuick.Controls\nimport QtQuick.Window\nWindow { id:root; visible:true; width:760; height:520;\n' + source[start:end] + '\nComponent.onCompleted: setupActivationDialog.open()\n}'
        program = PROGRAM.split('engine=')[0]
        program = 'from PyQt6.QtQuick import QQuickWindow\n' + program
        program = program.replace('backend=Backend()', 'backend=Backend();backend.finishes=0')
        program = program.replace('    decided=0', '    @pyqtSlot()\n    def finishSystemSetup(self):self.finishes+=1\n    decided=0')
        program += "\nengine=QQmlApplicationEngine();engine.rootContext().setContextProperty('backend',backend)\n"
        program += 'engine.loadData(' + repr(qml.encode()) + ",QUrl.fromLocalFile(str(Path('linux/monitorize/qml/CompletionTest.qml').resolve())))\n"
        program += """
assert engine.rootObjects()
app.processEvents()
root=engine.rootObjects()[0]
dialog=root.findChild(QObject,'setupActivationDialog')
assert dialog.property('title')=='Setup complete'
button=root.findChild(QObject,'setupCompleteOkButton')
assert button and button.property('text')=='OK'
assert backend.finishes==0
QMetaObject.invokeMethod(button,'clicked');app.processEvents()
assert backend.finishes==1
engine.deleteLater();app.processEvents()
"""
        result=subprocess.run([sys.executable,'-c',program],env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_network_consent_and_post_authorization_verification(self):
        program = PROGRAM.split("page.setProperty('applying',True)")[0]
        program = 'from PyQt6.QtQuick import QQuickItem\n' + program
        program = program.replace("    decided=0", "    firewallScopeReady=pyqtSignal('QVariantMap')\n    checking=False\n    decided=0")
        program = program.replace("input_message='Log out and back in to enable input.')",
                                  "input_message='Log out and back in to enable input.',firewall_scope_checking=self.checking)")
        program = program.replace("    @pyqtSlot()\n    def markSystemSetupDecided", """    @pyqtSlot()
    def refreshFirewallScope(self):pass
    @pyqtSlot(bool,bool,'QVariantList')
    def startScopedSystemSetup(self,*args):self.starts.append(args)
    @pyqtSlot()
    def markSystemSetupDecided""")
        program += """
app.processEvents()
authorize=root.findChild(QObject,'systemSetupAuthorizeButton')
assert not authorize.property('enabled'), 'Authorization enabled before network discovery'
zones=[dict(name='home',interfaces=['wlan0'],sources=[],default=False),
       dict(name='docker',interfaces=['docker0'],sources=[],default=False)]
backend.firewallScopeReady.emit(dict(available=True,running=True,zones=zones,scope_current=None))
app.processEvents()
assert not authorize.property('enabled'), 'Multiple networks were silently authorized'
def visual(item,name):
    if item.objectName()==name:return item
    for child in item.childItems():
        found=visual(child,name)
        if found is not None:return found
home=visual(page,'firewallZone_home')
assert home is not None
home.setProperty('checked',True);QMetaObject.invokeMethod(home,'toggled');app.processEvents()
assert authorize.property('enabled')
QMetaObject.invokeMethod(authorize,'clicked');app.processEvents()
assert backend.starts==[(True,True,['home'])],backend.starts
backend.checking=True
backend.systemSetupFinished.emit(dict(success=True,message='Configured',actions={
    'input':dict(success=True,message='Input configured'),
    'firewall':dict(success=True,message='Firewall configured')}))
app.processEvents()
assert not page.property('statusSucceeded'), 'Completion preceded firewall readback'
backend.firewallScopeReady.emit(dict(available=True,running=True,zones=zones,scope_current=False,message='Rules changed'))
app.processEvents()
assert not page.property('statusSucceeded')
assert 'Rules changed' in page.property('statusMessage')
# Completed input is retained; retry only the chosen firewall scope.
QMetaObject.invokeMethod(authorize,'clicked');app.processEvents()
assert backend.starts[-1]==(False,True,['home']),backend.starts
backend.systemSetupFinished.emit(dict(success=True,message='Verified',actions={'firewall':dict(success=True,message='Firewall verified')}))
app.processEvents()
backend.checking=False
backend.firewallScopeReady.emit(dict(available=True,running=True,zones=zones,scope_current=True))
app.processEvents()
assert page.property('statusSucceeded'), 'Verified setup did not offer Done'
"""
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_result_survives_until_done_and_retry_skips_completed_input(self):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',PROGRAM],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_unusable_input_cannot_complete_setup(self):
        program = PROGRAM.split("page.setProperty('applying',True)")[0]
        program = program.replace("logout_required=True", "logout_required=False")
        program += """
page.setProperty('applying',True)
backend.systemSetupFinished.emit(dict(success=True,message='Configured',actions={'input':{'success':True,'message':'Input configured'}}))
app.processEvents()
assert not page.property('statusSucceeded'), 'Unusable device reported as complete'
assert not root.findChild(QObject,'systemSetupDoneButton').property('visible')
assert root.findChild(QObject,'systemSetupAuthorizeButton').property('enabled')
"""
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_reopened_page_waits_for_live_helper_and_recovers_its_result(self):
        program = PROGRAM.split("page.setProperty('applying',True)")[0]
        program = program.replace(
            "def getSystemSetupStatus(self):return dict(available=True,input_ready=False,input_configured=True,logout_required=True,input_message='Log out and back in to enable input.')",
            'def getSystemSetupStatus(self):return self.status')
        program = program.replace('backend=Backend()',
            "backend=Backend();backend.status=dict(available=True,busy=True,input_ready=False,logout_required=False)")
        program += """
app.processEvents()
assert page.property('helperBusy')
assert not root.findChild(QObject,'systemSetupAuthorizeButton').property('enabled')
backend.status=dict(available=True,busy=False,interrupted=False,request_succeeded=True,
                    selected_actions=['firewall'],completed_actions={'firewall':'Verified'},
                    input_ready=False,logout_required=False,result_message='Firewall verified')
QMetaObject.invokeMethod(page,'refreshStatus');app.processEvents()
assert not page.property('helperBusy')
assert page.property('statusSucceeded'), 'A completed firewall-only selection was not recovered'
assert not root.findChild(QObject,'systemSetupDoneButton').property('visible')
backend.status['interrupted']=True
QMetaObject.invokeMethod(page,'refreshStatus');app.processEvents()
assert not page.property('statusSucceeded'), 'Interrupted work was shown as complete'
"""
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
