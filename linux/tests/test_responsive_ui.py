"""Render production QML through the native resize mode at tiled window sizes."""
import os
import subprocess
import sys
import unittest
from linux.tests.test_host_configuration_ui import PROGRAM

CHECKS=r"""
            backend._system_setup_decided=True
            evaluate('firstRunSetupPopup.close()');settle()
            from PyQt6.QtCore import QPointF
            from PyQt6.QtQuick import QQuickItem
            expected={'DisplaySetupPage.qml':'displaySetupPage','StreamingPage.qml':'streamingPage','SettingsPage.qml':'startupSettingsPage'}
            def walk(item):
                yield item
                for child in item.childItems():yield from walk(child)
            def assert_horizontal(page):
                for item in walk(page):
                    if not item.isVisible() or not item.property('text') or item.width()<=0:continue
                    pos=item.mapToItem(page,QPointF(0,0))
                    assert pos.x()>=-1 and pos.x()+item.width()<=page.width()+1,(size,page.objectName(),item.property('text'),pos.x(),item.width(),page.width())
            def scroll_bottom(page):
                for item in walk(page):
                    if item.property('contentY') is not None and item.property('contentHeight') is not None:
                        item.setProperty('contentY',max(0,item.property('contentHeight')-item.height()))
                settle()
            for mode in ['light','dark']:
                backend.setThemeMode(mode)
                for size in [(860,820),(640,360),(480,320),(360,280),(860,820)]:
                    view.resize(*size);view.show();settle()
                    for destination in expected:
                        evaluate('navigate("'+destination+'")');settle();settle();settle()
                        root=view.rootObject();page=root.findChild(QObject,'mainStack').property('currentItem')
                        assert page.objectName()==expected[destination],(destination,page.objectName(),warnings)
                        if destination=='SettingsPage.qml':page.setProperty('bootOptionsExpanded',True);settle()
                        assert_horizontal(page);scroll_bottom(page);assert_horizontal(page)
                    evaluate('configureUnsavedDialog.open()');settle()
                    confirmation=root.findChild(QObject,'configureUnsavedDialog')
                    assert confirmation.property('height')<=size[1]-16,(size,'unsaved height',confirmation.property('height'))
                    evaluate('configureUnsavedDialog.close()')
                    evaluate('setupActivationDialog.open()');settle()
                    dialog=root.findChild(QObject,'setupActivationDialog')
                    assert dialog.property('visible')
                    assert dialog.property('height')<=size[1]-16,(size,'completion height',dialog.property('height'))
                    evaluate('setupActivationDialog.close()')
                    evaluate('firstRunSetupPopup.open()');settle();settle()
                    popup=evaluate('firstRunSetupPopup')[0]
                    assert popup.property('height')<=size[1]-16
                    evaluate('firstRunSetupPopup.close()');settle()
            failures=[m for m in warnings if any(s in m for s in ['ReferenceError:', 'TypeError:', 'Binding loop detected', 'Cannot assign','unavailable'])]
            assert not failures,failures
            assert not backend.configurationDirty,'Resizing or page navigation changed the draft'
            view.setSource(QUrl());backend.close()
"""

class ResponsiveUiTests(unittest.TestCase):
    def test_tiling_sizes_preserve_content_and_state(self):
        prefix=PROGRAM.split('            backend.startConfiguredListeners()')[0]
        prefix=prefix.replace('view=QQuickWidget();view.rootContext()', 'view=QQuickWidget();view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView);view.rootContext()')
        prefix=prefix.replace('QQmlExpression(view.rootContext(),view.rootObject(),code)','QQmlExpression(qmlContext(view.rootObject()),view.rootObject(),code)')
        prefix=prefix.replace('app=QApplication([])', 'app=QApplication([])\nfrom PyQt6.QtCore import qInstallMessageHandler\nwarnings=[]\nqInstallMessageHandler(lambda kind, context, message:warnings.append(message))')
        prefix=prefix.replace('use_client_display_settings=True)', 'use_client_display_settings=False,streaming_customized=True)')
        for style,scale in [('Fusion','1'),('Basic','1.5')]:
            with self.subTest(style=style,scale=scale):
                result=subprocess.run([sys.executable,'-c',prefix+CHECKS],capture_output=True,text=True,timeout=60,
                    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_QUICK_BACKEND':'software','QT_QUICK_CONTROLS_STYLE':style,'QT_SCALE_FACTOR':scale,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
