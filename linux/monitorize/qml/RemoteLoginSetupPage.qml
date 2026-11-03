import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "remoteLoginSetupPage"
    property var setupStatus: ({available: false, enabled: false, supported: false})
    property string resultMessage: ""
    function refresh() { setupStatus = backend.getRemoteLoginSetupStatus() }
    Component.onCompleted: refresh()
    Connections {
        target: backend
        function onRemoteLoginSetupFinished(result) {
            page.resultMessage = result.message || "Remote-login setup did not complete."
            consent.checked = false
            page.refresh()
        }
    }
    ScrollView {
        anchors.fill: parent; clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 16
            Text { text: "Remote login at boot"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold }
            SectionCard {
                title: "SETUP"; symbol: "session"; Layout.fillWidth: true
                Text {
                    text: "Connect to the login screen. After signing in, reconnect to the same Moonlight PC."
                    color: theme.textPrimary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Text {
                    text: (!page.setupStatus.live_available && page.setupStatus.live_message) || page.setupStatus.message || "Checking installed service assets…"
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Text {
                    text: "Plasma Login Manager and experimental SDDM/GDM Wayland adapters use a reserved 1920×1080 login display. Desktop settings are applied after login; compositor-specific limitations appear in diagnostics."
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Text {
                    text: "Setup keeps pairing in protected system storage and enables remote login now and after future boots, without restarting your PC. Wi-Fi configuration is a separate step. Disabling releases ownership for ordinary desktop streaming."
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                CustomCheckBox {
                    id: consent
                    objectName: "remoteLoginConsent"
                    text: "Enable access to this account's login screen for my paired clients."
                    Layout.fillWidth: true
                    enabled: !backend.remoteLoginSetupBusy && page.setupStatus.available && page.setupStatus.supported && page.setupStatus.live_available
                }
                GridLayout {
                    columns: page.width < 400 ? 1 : 2
                    CustomButton {
                        objectName: "remoteLoginEnable"
                        text: backend.remoteLoginSetupBusy ? "Working…" : page.setupStatus.enabled ? "Re-run setup" : "Enable"
                        enabled: !backend.remoteLoginSetupBusy && consent.checked && page.setupStatus.available && page.setupStatus.supported && page.setupStatus.live_available
                        onClicked: backend.configureRemoteLogin(true)
                    }
                    CustomButton {
                        text: "Disable"; danger: true
                        visible: page.setupStatus.enabled
                        enabled: !backend.remoteLoginSetupBusy
                        onClicked: backend.configureRemoteLogin(false)
                    }
                }
            }
            Text {
                text: page.resultMessage; visible: text.length > 0
                color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            GridLayout {
                columns: page.width < 500 ? 1 : 3
                CustomButton { text: "Back"; primary: false; onClicked: page.StackView.view.pop() }
                CustomButton { text: "Refresh"; primary: false; enabled: !backend.remoteLoginSetupBusy; onClicked: page.refresh() }
                CustomButton { text: "Wi-Fi before login"; primary: false; onClicked: page.StackView.view.push("BootWifiSetupPage.qml") }
            }
        }
    }
}
