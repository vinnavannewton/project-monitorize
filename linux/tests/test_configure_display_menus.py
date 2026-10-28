"""Real QML menus remain in Configure and route to each selected Sunshine host."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

from linux.tests.test_host_configuration_ui import PROGRAM

PREFIX = PROGRAM.split('            # Live changes')[0]
SCENARIOS = r'''
            from PyQt6.QtCore import QMetaObject
            view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
            view.resize(1000,850);view.show();settle()
            def find(name, root=None, seen=None):
                root=view.rootObject() if root is None else root
                seen=set() if seen is None else seen
                if root in seen:return None
                seen.add(root)
                if root.objectName()==name:return root
                children=root.children()+(root.childItems() if hasattr(root,'childItems') else [])
                for child in children:
                    found=find(name,child,seen)
                    if found is not None:return found
                return None
            page=view.rootObject().findChild(QObject,'displaySetupPage')
            def edit(code):
                result=QQmlExpression(qmlContext(page),page,code)
                result.evaluate();assert not result.hasError(),result.error().toString()
            action1=find('configureDisplayActions1')
            assert action1 and action1.property('visible')
            assert not find('configureRemoveDisplay1').property('visible')
            with patch('monitorize.desktop.backend.start_sunshine',return_value=(True,'')) as settings:
                item=find('configureSunshineSettings1')
                assert item.property('enabled')
                QMetaObject.invokeMethod(action1,'clicked');settle()
                QMetaObject.invokeMethod(item,'triggered');settle()
                settings.assert_called_once_with(1,settings_only=True)
                backend._cancel_settings_open()
                edit('addDisplay()');settle()
                action2=find('configureDisplayActions2')
                assert action2 and action2.property('visible')
                item=find('configureSunshineSettings2')
                QMetaObject.invokeMethod(action2,'clicked');settle()
                QMetaObject.invokeMethod(item,'triggered');settle()
                assert settings.call_args.args==(2,)
                assert settings.call_args.kwargs=={'settings_only':True}
                backend._cancel_settings_open()
                QMetaObject.invokeMethod(action2,'clicked');settle()
                remove=find('configureRemoveDisplay2')
                assert remove.property('visible')
                QMetaObject.invokeMethod(remove,'triggered');settle()
                assert len(backend.loadVirtualDisplaySettings())==1
            # Mirror uses instance 1 from the main Configure header; no separate card.
            edit('displayType.selectValue("Mirror")');settle()
            assert 'SUNSHINE · MIRROR' not in Path('linux/monitorize/qml/DisplaySetupPage.qml').read_text()
            with patch('monitorize.desktop.backend.start_sunshine',return_value=(True,'')) as settings:
                action=find('configureDisplayActions1')
                assert action and action.property('visible')
                QMetaObject.invokeMethod(action,'clicked');settle()
                QMetaObject.invokeMethod(find('configureSunshineSettings1'),'triggered');settle()
                settings.assert_called_once_with(1,settings_only=True)
                assert not find('configureRemoveDisplay1').property('visible')
                backend._cancel_settings_open()
            edit('displayType.selectValue("Extend")');settle()
            backend.configuration_state.discard()
            evaluate('navigate("StreamingPage.qml")');settle();settle()
            assert view.rootObject().findChild(QObject,'sessionErrorCard') is None
            assert view.rootObject().findChild(QObject,'configureDisplayActions1') is None
            backend._remember_session_error('First display failure',1)
            backend._remember_session_error('Second display failure',2)
            assert backend.copyDisplayError(1)
            assert app.clipboard().text()=='First display failure'
            assert backend.copyDisplayError(2)
            assert app.clipboard().text()=='Second display failure'
            view.setSource(QUrl());backend.close()
print('Configure display menus and per-display error copying passed')
'''


class ConfigureDisplayMenusTest(unittest.TestCase):
    def test_both_cards_route_settings_and_only_the_second_is_removable(self):
        result = subprocess.run([sys.executable, '-c', PREFIX + SCENARIOS],
            cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                     PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
