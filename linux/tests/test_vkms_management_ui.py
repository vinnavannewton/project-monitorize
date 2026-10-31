"""Render and exercise the VKMS card with an inert backend in an isolated process."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROGRAM = r'''
from pathlib import Path
import tempfile, os
from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtProperty, pyqtSlot, QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQml import QQmlExpression, qmlContext
app=QApplication([])
class Backend(QObject):
    changed=pyqtSignal()
    recoveryOperationFinished=pyqtSignal(object)
    def __init__(self):
        super().__init__();self.status={};self.actions=[]
    @pyqtProperty('QVariantMap',notify=changed)
    def vkmsManagementStatus(self):return self.status
    @pyqtProperty(bool,constant=True)
    def systemSetupRunning(self):return False
    @pyqtProperty(bool,constant=True)
    def remoteLoginSetupBusy(self):return False
    @pyqtProperty(bool,constant=True)
    def applyingConfiguration(self):return False
    @pyqtSlot()
    def openMonitorizeVkmsInstallPage(self):self.actions.append('github')
    @pyqtSlot()
    def reinstallMonitorizeVkms(self):self.actions.append('reinstall')
    @pyqtSlot()
    def updateMonitorizeVkms(self):self.actions.append('update')
    @pyqtSlot(str)
    def copySettingsMessage(self,text):pass
def settle():
    loop=QEventLoop();QTimer.singleShot(180,loop.quit);loop.exec()
def click(button):
    expression=QQmlExpression(qmlContext(button),button,'clicked()')
    expression.evaluate();assert not expression.hasError(),expression.error().toString()
backend=Backend()
qml=Path('linux/monitorize/qml').resolve()
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'Card.qml'
    path.write_text('import QtQuick\nimport "'+qml.as_uri()+'"\nRectangle { width: 600; height: 560; color: theme.background; Theme { id: theme; mode: "dark" } VkmsManagementCard { anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 } } }')
    view=QQuickWidget();view.resize(600,560)
    view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    view.rootContext().setContextProperty('backend',backend)
    backend.status=dict(installed=False,state='not_installed',message='Monitorize VKMS not installed',checking=False,busy=False)
    view.setSource(QUrl.fromLocalFile(str(path)))
    assert view.status()==QQuickWidget.Status.Ready,[error.toString() for error in view.errors()]
    view.show();settle()
    card=view.rootObject().findChild(QObject,'vkmsManagementCard')
    assert card
    install=card.findChild(QObject,'vkmsInstallButton')
    assert install.property('visible')
    click(install);assert backend.actions==['github']
    restart=card.findChild(QObject,'vkmsReinstallButton');update=card.findChild(QObject,'vkmsUpdateButton')
    assert restart.property('text') == 'Reinstall'
    assert not restart.property('visible') and not update.property('visible')
    output=os.environ.get('MONITORIZE_VKMS_PREVIEWS')
    def capture(name):
        if output:
            Path(output).mkdir(parents=True,exist_ok=True)
            assert view.grabFramebuffer().save(str(Path(output)/(name+'.png')))
    capture('missing-dark')
    for mode in ('light','dark'):
        theme=view.rootObject().findChild(QObject,'appearanceTheme');theme.setProperty('mode',mode)
        for state,message in [('working','Verified — working'),('needs_attention','Needs attention'),('restart_required','System restart required')]:
            backend.status=dict(installed=True,state=state,message=message,version='1.0',checking=False,busy=False,details='Example diagnostic' if state=='needs_attention' else '')
            backend.changed.emit();settle()
            assert restart.property('visible') and restart.property('enabled')
            assert update.property('visible') and update.property('enabled')
            assert card.property('implicitHeight') < view.height()
            capture(state+'-'+mode)
    backend.status={**backend.status,'state':'working','up_to_date':True}
    backend.changed.emit();settle()
    health=card.findChild(QObject,'vkmsHealthMessage')
    assert health.property('text')=='Up to date'
    backend.status={**backend.status,'up_to_date':False,'message':'Verified — working'}
    backend.changed.emit();settle()
    assert health.property('text')=='Verified — working'
    click(restart);click(update);assert backend.actions==['github','reinstall','update']
    backend.status={**backend.status,'busy':True,'operation_message':'Installing VKMS…'}
    backend.changed.emit();settle()
    assert not restart.property('enabled') and not update.property('enabled')
    view.resize(360,560);settle();capture('narrow-updating')
    assert card.property('width') <= 328
    # Streaming log replacement and bounded-tail rotation must follow the end.
    backend.status={**backend.status,'logs':'\n'.join('line '+str(i) for i in range(100))}
    backend.changed.emit();settle()
    card.setProperty('detailsExpanded',True);settle();settle()
    viewport=card.findChild(QObject,'vkmsLogViewport')
    log=card.findChild(QObject,'vkmsLogText')
    def at_bottom():
        maximum=max(0,viewport.property('contentHeight')-viewport.property('height'))
        assert maximum>0
        assert abs(viewport.property('contentY')-maximum)<2,(viewport.property('contentY'),maximum)
    at_bottom()
    for step in range(3):
        viewport.setProperty('contentY',0)
        backend.status={**backend.status,'logs':'\n'.join('line '+str(i) for i in range(step+1,step+101))}
        backend.changed.emit();settle();settle();at_bottom()
        assert log.property('text').endswith('line '+str(step+100))
    card.setProperty('detailsExpanded',False);settle()
    backend.status={**backend.status,'logs':backend.status['logs']+'\nLATEST'}
    backend.changed.emit();settle()
    card.setProperty('detailsExpanded',True);settle();settle();at_bottom()
    assert log.property('text').endswith('LATEST')
    # Run the actual controller completion and real ten-second timer in QML.
    from concurrent.futures import Future
    from types import SimpleNamespace
    from monitorize.desktop.vkms_management import VkmsManagement
    backend._closing=False
    future=Future()
    backend._background_executor=SimpleNamespace(submit=lambda function:future)
    manager=VkmsManagement(backend)
    def publish():
        backend.status=manager.snapshot();backend.changed.emit()
    manager.changed.connect(publish)
    manager.health=dict(installed=True,state='working',message='Verified — working')
    manager.operation='update'
    manager.prepared=dict(revision='a'*40,up_to_date=False)
    manager.finish(dict(success=True,message='VKMS updated. Checking installation…'))
    future.set_result(dict(installed=True,state='working',message='Verified — working'))
    settle()
    assert health.property('text')=='Up to date'
    assert not card.findChild(QObject,'vkmsOperationMessage').property('visible')
    loop=QEventLoop();QTimer.singleShot(10300,loop.quit);loop.exec();settle()
    assert health.property('text')=='Verified — working'
    assert not manager.notice_timer.isActive()
    assert not card.findChild(QObject,'vkmsOperationMessage').property('visible')
    view.setSource(QUrl());view.close()
'''


class VkmsManagementUiTests(unittest.TestCase):
    def test_card_states_actions_and_layout(self):
        environment = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software', 'PYTHONPATH': 'linux'}
        result = subprocess.run([sys.executable, '-c', PROGRAM], capture_output=True, text=True,
                                env=environment, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('ReferenceError', result.stderr)
        self.assertNotIn('TypeError', result.stderr)
