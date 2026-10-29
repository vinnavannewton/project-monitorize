import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "bootWifiSetupPage"
    property var profiles: backend.bootWifiProfiles
    property var selectedProfile: ({})
    property string selectedUuid: ""
    property string selectedVersion: ""
    property string resultMessage: ""
    property bool resultSucceeded: false
    property bool pageReady: false

    function selectProfile(index, keepMessage) {
        selectedProfile = index >= 0 && index < profiles.length ? profiles[index] : ({})
        selectedUuid = selectedProfile.uuid || ""
        selectedVersion = selectedProfile.version || ""
        passwordField.clear()
        if (!keepMessage) resultMessage = ""
    }
    function reconcileProfiles() {
        if (!pageReady) return
        let index = profiles.findIndex(function(p) { return p.uuid === page.selectedUuid })
        if (index < 0 && profiles.length > 0) index = 0
        profilePicker.currentIndex = index
        if (index < 0 || profiles[index].uuid !== selectedUuid || profiles[index].version !== selectedVersion) {
            selectProfile(index, true)
        } else {
            selectedProfile = profiles[index]
        }
    }
    onProfilesChanged: reconcileProfiles()
    Component.onCompleted: {
        pageReady = true
        reconcileProfiles()
        backend.setBootWifiPageVisible(visible)
    }
    onVisibleChanged: {
        if (pageReady) backend.setBootWifiPageVisible(visible)
        if (!visible && pageReady) passwordField.clear()
    }
    Component.onDestruction: {
        passwordField.clear()
        backend.setBootWifiPageVisible(false)
    }
    Connections {
        target: backend
        function onBootWifiFinished(result) {
            passwordField.clear()
            page.resultMessage = result.message || "Wi-Fi setup failed."
            page.resultSucceeded = result.success === true
        }
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 16
            Text { text: "Wi-Fi before login"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold }
            Text {
                text: "Configure one saved Wi-Fi network for automatic connection before desktop login. Ethernet users can skip this step."
                color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            SectionCard {
                title: "NETWORK"; symbol: "settings"; Layout.fillWidth: true
                Text {
                    text: backend.bootWifiStatus.message || "Checking NetworkManager…"
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                CustomButton {
                    text: "Recover network service"
                    visible: backend.bootWifiStatus.code === "unavailable"
                    enabled: !backend.bootWifiBusy && !backend.isStreaming
                    onClicked: backend.recoverBootWifiService()
                }
                Text { text: "Select the connected Wi-Fi"; color: theme.textSecondary }
                CustomComboBox {
                    id: profilePicker
                    objectName: "bootWifiProfilePicker"
                    Layout.fillWidth: true
                    enabled: !backend.bootWifiBusy && page.profiles.length > 0
                    accentedIndex: page.profiles.findIndex(function(p) { return p.active === true })
                    model: page.profiles.map(function(p) {
                        return (p.active ? "Connected · " : "") + p.name + " · " + (p.interface ? p.interface + " · " : "") + p.uuid.slice(-8)
                    })
                    onActivated: page.selectProfile(currentIndex)
                }
                Text {
                    text: page.selectedProfile.reason || (page.selectedProfile.setup_saved === true
                        ? "✓ Authorized and configured for automatic connection before login." : (page.selectedProfile.configuration === "configuration_matches"
                        ? "Saved network settings match. Authorize below to confirm setup with Monitorize."
                        : "This profile needs system-wide availability and a system-stored password."))
                    visible: page.selectedUuid.length > 0
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Text {
                    text: "The Wi-Fi password will be stored in NetworkManager’s protected system configuration. Other local users can use this network. Your current connection will not be deliberately restarted."
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                CustomTextField {
                    id: passwordField
                    objectName: "bootWifiPassword"
                    placeholderText: "Wi-Fi password"
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                    enabled: page.selectedProfile.eligible === true && !backend.bootWifiBusy
                    Layout.fillWidth: true
                }
                Text {
                    text: "Make this network available to all users and connect automatically before login."
                    color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                CustomButton {
                    objectName: "bootWifiApply"
                    text: backend.bootWifiBusy ? "Working…" : page.selectedProfile.setup_saved === true ? "Update configuration" : "Authorize and configure"
                    enabled: !backend.bootWifiBusy && passwordField.text.length > 0 && page.selectedProfile.eligible === true
                    onClicked: {
                        page.resultMessage = ""
                        backend.configureBootWifi(page.selectedUuid, page.selectedVersion, passwordField.text)
                        passwordField.clear()
                    }
                }
                Text {
                    text: "Administrator authorization may be requested. Closing this page cannot undo a change already sent to NetworkManager."
                    color: theme.textMuted; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
            }
            Text {
                text: page.resultMessage
                visible: text.length > 0
                color: page.resultSucceeded ? theme.textSecondary : theme.errorText
                wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            RowLayout {
                CustomButton {
                    text: "Back"; primary: false
                    onClicked: page.StackView.view.pop()
                }
                CustomButton {
                    text: "Refresh"; primary: false
                    enabled: !backend.bootWifiBusy
                    onClicked: backend.refreshBootWifi()
                }
            }
        }
    }
}
