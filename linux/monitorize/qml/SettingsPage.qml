import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool settingsLoading: true
    property bool settingsMinimizeToTray: false
    property bool settingsAutostartEnabled: false
    property string settingsError: ""
    Component.onCompleted: loadAppSettings()
    function loadAppSettings() {
        settingsLoading = true
        let gen = backend.loadGeneralSettings()
        settingsMinimizeToTray = gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false
        settingsAutostartEnabled = backend.isAutostartEnabled()
        minimizeTrayCheck.checked = settingsMinimizeToTray
        autostartCheck.checked = settingsAutostartEnabled
        settingsError = ""
        settingsLoading = false
    }

    function saveAppSettings() {
        if (settingsLoading) return
        settingsMinimizeToTray = minimizeTrayCheck.checked
        backend.saveGeneralSettings(settingsMinimizeToTray)
    }

    function saveAutostartSettings() {
        if (settingsLoading) return
        settingsAutostartEnabled = autostartCheck.checked
        settingsError = backend.setAutostartEnabled(settingsAutostartEnabled)
        if (settingsError.length > 0) {
            settingsLoading = true
            settingsAutostartEnabled = backend.isAutostartEnabled()
            autostartCheck.checked = settingsAutostartEnabled
            settingsLoading = false
        }
    }


    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 18
            Text { text: "Settings"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold }
            SectionCard {
                title: "GENERAL"; symbol: "settings"; Layout.fillWidth: true
                CustomCheckBox {
                    id: minimizeTrayCheck; text: "Minimize to tray on close"
                    Layout.fillWidth: true; onCheckedChanged: page.saveAppSettings()
                }
                CustomCheckBox {
                    id: autostartCheck; text: "Start Monitorize after login"
                    Layout.fillWidth: true; onCheckedChanged: page.saveAutostartSettings()
                }
                Text {
                    text: page.settingsError; visible: text.length > 0
                    color: "#fca5a5"; Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
            }
            SectionCard {
                title: "MISCELLANEOUS"; symbol: "extras"; Layout.fillWidth: true
                CustomButton {
                    visible: backend.canConfigureDisplay
                    text: "Remove stagnant virtual displays"; primary: false
                    Layout.fillWidth: true
                    enabled: !backend.isStreaming
                    onClicked: root.removeStagnantVirtualDisplays()
                }
                CustomButton {
                    visible: backend.sunshineAvailable; enabled: !backend.isStreaming
                    text: "Clear restore tokens"; primary: false; Layout.fillWidth: true
                    onClicked: root.clearRestoreTokens()
                }
                CustomButton {
                    visible: backend.systemSetupAvailable
                    text: "Run system setup again"; primary: false; Layout.fillWidth: true
                    onClicked: page.StackView.view.push("SystemSetupPage.qml")
                }
            }
        }
    }
}
