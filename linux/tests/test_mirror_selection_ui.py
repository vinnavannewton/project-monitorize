"""Mirror discovery keeps the actual QML selection and unapplied draft aligned."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    "mirror_ui_fixture", Path(__file__).with_name("test_host_configuration_ui.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
PREFIX = fixture.PROGRAM.split("            backend.startConfiguredListeners();")[0]

SCENARIOS = r'''
            evaluate('navigate("DisplaySetupPage.qml")');settle()
            page=view.rootObject().findChild(QObject,'displaySetupPage')
            def edit(code):
                expression=QQmlExpression(qmlContext(page),page,code)
                result=expression.evaluate()
                assert not expression.hasError(),expression.error().toString()
                return result[0]
            with patch.object(backend,'requestMirrorOutputs',return_value=123):
                edit('mirrorOutputId = ""; displayType.currentIndex = 1; saveSettings()')
                assert not backend.configurationValid
                # No monitor, or several monitors, must wait for an explicit choice.
                edit('applyMirrorOutputs([])')
                assert edit('mirrorOutputId') == ''
                assert backend.loadDisplaySettings()['mirror_output'] == ''
                edit('applyMirrorOutputs([{id:"DP-1",label:"First"}, {id:"HDMI-1",label:"Second"}])')
                assert edit('mirrorMonitor.currentIndex') == 0
                assert backend.loadDisplaySettings()['mirror_output'] == ''
                # A sole discovered monitor is displayed and saved into the draft.
                edit('applyMirrorOutputs([{id:"DP-1",label:"First",native_width:1920,native_height:1080}])')
                assert edit('mirrorOutputId') == 'DP-1'
                assert edit('mirrorMonitor.currentIndex') == 1
                assert backend.loadDisplaySettings()['mirror_output'] == 'DP-1'
                assert backend.configurationValid, backend.configurationValidationMessage
                assert backend.configurationDirty
                assert settings.load_display_settings()['mirror_output'] == ''
                assert settings.load_display_settings()['display_type'] == 'Extend'
                assert launches == [], 'Discovery started or restarted a session'
                # A disappeared saved target stays unavailable, never silently replaced.
                edit('mirrorOutputId = "missing-output"; saveSettings(); applyMirrorOutputs([{id:"HDMI-1",label:"Different"}])')
                assert edit('mirrorOutputId') == 'missing-output'
                assert edit('mirrorMonitor.currentIndex') == 0
                assert edit('mirrorMonitor.currentText') == 'Unavailable: missing-output'
                assert backend.loadDisplaySettings()['mirror_output'] == 'missing-output'
                edit('applyMirrorOutputs([])')
                assert edit('mirrorMonitor.currentText') == 'Unavailable: missing-output'
                assert backend.loadDisplaySettings()['mirror_output'] == 'missing-output'
                assert settings.load_display_settings()['mirror_output'] == ''
                assert launches == []
            view.setSource(QUrl());backend.close()
'''


class MirrorSelectionUiTest(unittest.TestCase):
    def test_discovered_selection_is_draft_only_and_preserves_missing_target(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                   PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-c", PREFIX + SCENARIOS], env=env,
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
