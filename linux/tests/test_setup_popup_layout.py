"""Exercise setup consent and footer geometry in the actual QML scene."""
import os
import subprocess
import sys
import unittest

from linux.tests.test_system_setup_completion_ui import PROGRAM


def popup_program():
    program = PROGRAM.split("page.setProperty('applying',True)")[0]
    program = "from PyQt6.QtQuick import QQuickItem,QQuickWindow\n" + program
    program = program.replace(
        "    decided=0",
        "    firewallScopeReady=pyqtSignal('QVariantMap')\n    decided=0")
    program = program.replace(
        "    @pyqtSlot()\n    def markSystemSetupDecided",
        "    @pyqtSlot()\n    def refreshFirewallScope(self):pass\n"
        "    @pyqtSlot()\n    def markSystemSetupDecided")
    program = program.replace(
        "Item {\n width: 560; height: 650",
        "Window {\n visible: true; width: 512; height: 432")
    return program + r'''
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QPointF
def settle():
    QTest.qWait(80)
    app.processEvents()
def find_visual(item, predicate):
    if predicate(item):return item
    for child in item.childItems():
        found=find_visual(child,predicate)
        if found is not None:return found
def named(name):return find_visual(page,lambda item:item.objectName()==name)
def labeled(text):return find_visual(page,lambda item:item.property('text')==text)
def selected():
    value=page.property('selectedZones')
    return value.toVariant() if hasattr(value,'toVariant') else value
def emit_scope(names):
    zones=[dict(name=name,interfaces=['wireless-interface-with-a-long-name'],
                sources=['192.0.2.0/24'],default=False) for name in names]
    backend.firewallScopeReady.emit(dict(available=True,running=True,zones=zones,
                                        scope_current=None))
    settle()
def toggle_zone(name,checked):
    checkbox=named('firewallZone_'+name)
    assert checkbox is not None,name
    checkbox.setProperty('checked',checked)
    QMetaObject.invokeMethod(checkbox,'toggled')
    settle()
def refresh():
    button=labeled('Refresh networks')
    assert button is not None
    QMetaObject.invokeMethod(button,'clicked')
    settle()
    assert not page.property('scopeLoaded')
settle()
'''


class SetupPopupLayoutTests(unittest.TestCase):
    def run_qml(self, scenario):
        result = subprocess.run(
            [sys.executable, '-c', popup_program() + scenario],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                 'QT_QUICK_BACKEND': 'software', 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_refresh_does_not_authorize_replacement_network(self):
        self.run_qml(r'''
emit_scope(['home','docker'])
toggle_zone('home',True)
assert selected()==['home'],selected()
assert named('systemSetupAuthorizeButton').property('enabled')
refresh()
emit_scope(['docker'])
assert selected()==[],selected()
assert not named('systemSetupAuthorizeButton').property('enabled')
''')

    def test_refresh_preserves_explicit_single_zone_deselection(self):
        self.run_qml(r'''
emit_scope(['home'])
assert selected()==['home'],selected()
toggle_zone('home',False)
assert selected()==[],selected()
refresh()
emit_scope(['home'])
assert selected()==[],selected()
assert not named('systemSetupAuthorizeButton').property('enabled')
''')

    def test_actions_stay_visible_beside_long_scrolling_details(self):
        self.run_qml(r'''
emit_scope(['network_'+str(index) for index in range(8)])
page.setProperty('statusMessage','Authorization diagnostic details. '*40)
settle()
authorize=named('systemSetupAuthorizeButton')
cancel=labeled('Cancel')
assert authorize is not None and cancel is not None
def assert_visible_button(button):
    assert button.property('visible')
    point=button.mapToItem(page,QPointF())
    assert 0<=point.x() and point.x()+button.width()<=page.width()+1,(point,button.width(),page.width())
    assert 0<=point.y() and point.y()+button.height()<=page.height()+1,(point,button.height(),page.height())
    assert button.height()>=38,button.height()
    return point
before=assert_visible_button(authorize)
assert_visible_button(cancel)
flickable=find_visual(page,lambda item:item.inherits('QQuickFlickable'))
assert flickable is not None
assert flickable.property('contentHeight')>flickable.height()
flickable.setProperty('contentY',flickable.property('contentHeight')-flickable.height())
settle()
after=assert_visible_button(authorize)
assert abs(before.y()-after.y())<1,(before,after)
assert_visible_button(cancel)
''')


if __name__ == '__main__':
    unittest.main()
