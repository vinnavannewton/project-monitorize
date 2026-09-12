import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property int pairInstance: 1
    property bool logsExpanded: false
    function openPair(instance) {
        pairInstance = instance
        pinField.text = ""
        pinMessage.text = ""
        pinPopup.open()
        pinField.forceActiveFocus()
    }
    Connections {
        target: backend
        function onLogAppended(type, message) { logArea.text = backend.sessionLog() }
        function onStreamingStartFailed() { page.logsExpanded = true }
        function onStreamingCodecMismatch(message) { page.logsExpanded = true }
    }
    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 16
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    width: 12; height: 12; radius: 6
                    color: backend.sessionRunning ? "#34d681" : (backend.sessionBusy ? "#efbd5a" : theme.textMuted)
                }
                Text {
                    text: backend.sessionBusy ? "Preparing session" : (backend.sessionRunning ? "Session active" : "Session")
                    color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold
                    Layout.fillWidth: true
                }
            }
            Rectangle {
                visible: backend.sessionMode !== "Extend" || backend.sessionHasDisplays
                Layout.fillWidth: true
                implicitHeight: summary.implicitHeight + 40
                radius: 14; color: theme.surface; border.color: theme.border
                RowLayout {
                    id: summary
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                    spacing: 20
                    LineIcon { symbol: "session"; Layout.preferredWidth: 38; Layout.preferredHeight: 38 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            text: backend.sessionMode === "Mirror" ? "Mirror your screen" : (backend.sessionHasDisplays ? "Your virtual displays" : "Extend your workspace")
                            font.pixelSize: 18; font.weight: Font.DemiBold; color: theme.textPrimary
                        }
                        Text {
                            text: backend.streamingStatus || (backend.sessionMode === "Mirror" ? "Start to share your existing screen with Moonlight." : (backend.sessionHasDisplays ? "Start to recreate your displays and stream." : "Add a display, then start your session."))
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            color: theme.textSecondary; font.pixelSize: 13
                        }
                        Text {
                            visible: backend.sessionMode === "Mirror" && backend.sessionRunning
                            text: backend.localIp + ":47989"; color: theme.textSecondary; font.pixelSize: 13
                        }
                    }
                }
            }
            Repeater {
                model: backend.sessionDisplays
                delegate: Rectangle {
                    id: displayCard
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: 102
                    radius: 14; color: theme.surface; border.color: theme.border
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 20; spacing: 18
                        LineIcon { symbol: "display"; Layout.preferredWidth: 34; Layout.preferredHeight: 34 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text { text: displayCard.modelData.title; color: theme.textPrimary; font.pixelSize: 17; font.weight: Font.DemiBold }
                            Text {
                                text: backend.sessionRunning ? displayCard.modelData.address : displayCard.modelData.state
                                color: theme.textSecondary; font.pixelSize: 13
                            }
                        }
                        Rectangle { width: 9; height: 9; radius: 5; color: displayCard.modelData.live ? "#34d681" : theme.textMuted }
                        CustomButton {
                            text: "⋮"; primary: false; implicitWidth: 38
                            enabled: !backend.sessionBusy
                            onClicked: displayMenu.open()
                            Menu {
                                id: displayMenu
                                popupType: Popup.Item
                                width: 220
                                background: Rectangle { color: theme.surface; border.color: theme.border; radius: 8 }
                                CardMenuItem { text: "Sunshine settings"; enabled: backend.sessionRunning && displayCard.modelData.live; onTriggered: backend.openSunshineWebUi(displayCard.modelData.number) }
                                CardMenuItem { text: "Remove"; visible: !displayCard.modelData.mirror; onTriggered: backend.removeSessionDisplay(displayCard.index) }
                            }
                        }
                    }
                }
            }
            AbstractButton {
                visible: backend.sessionMode === "Extend"
                    && backend.sessionDisplays.length < backend.sessionMaxDisplays
                enabled: !backend.sessionBusy
                Layout.fillWidth: true
                implicitHeight: 90
                onClicked: backend.addSessionDisplay()
                background: Rectangle {
                    radius: 14; color: parent.hovered ? theme.surfaceAlt : "transparent"
                    border.color: theme.borderHover
                }
                contentItem: RowLayout {
                    spacing: 20
                    anchors { left: parent.left; right: parent.right; margins: 22 }
                    LineIcon { symbol: "plus"; Layout.preferredWidth: 30; Layout.preferredHeight: 30 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text { text: "Add Display"; color: "#94caff"; font.pixelSize: 17; font.weight: Font.DemiBold }
                        Text { text: "Add a display card. Start creates the display."; color: theme.textMuted; font.pixelSize: 13 }
                    }
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 10
                CustomButton { text: "Pair Moonlight PIN"; visible: backend.streamingBackend !== "none"; enabled: backend.sessionRunning && !backend.sessionBusy; onClicked: page.openPair(1) }
                CustomButton {
                    text: "Save Preset"; primary: false; enabled: backend.isStreaming
                    onClicked: { presetName.text = ""; presetMessage.text = ""; presetPopup.open() }
                }
                CustomButton {
                    text: backend.sessionRunning || backend.sessionBusy || (backend.streamingBackend === "none" && backend.isStreaming) ? "Stop" : "Start"
                    danger: text === "Stop"
                    enabled: text === "Stop" || backend.sessionMode === "Mirror" || backend.sessionHasDisplays
                    onClicked: text === "Stop" ? backend.stopSession() : backend.startSession()
                }
                CustomButton {
                    text: "Start added display"
                    visible: backend.isStreaming && backend.sessionPendingDisplays
                    enabled: !backend.sessionBusy
                    onClicked: backend.startSession()
                }
                CustomButton { text: "Display Settings"; primary: false; visible: backend.canConfigureDisplay; onClicked: backend.configureDisplay() }
            }
            SectionCard {
                title: "Diagnostics & logs"; symbol: "logs"; expanded: page.logsExpanded
                onExpandedChanged: page.logsExpanded = expanded
                Layout.fillWidth: true
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240
                    TextArea {
                        id: logArea
                        text: backend.sessionLog()
                        readOnly: true; wrapMode: TextEdit.Wrap
                        color: theme.textSecondary; font.family: "monospace"; font.pixelSize: 11
                        background: Rectangle { color: theme.logBoxBackground; radius: 8 }
                    }
                }
            }
        }
    }
    Popup {
        id: pinPopup
        modal: true
        anchors.centerIn: parent
        width: 380
        padding: 22
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        onClosed: pinSuccessCloseTimer.stop()
        Timer {
            id: pinSuccessCloseTimer
            interval: 2000
            onTriggered: pinPopup.close()
        }
        ColumnLayout {
            width: parent.width
            spacing: 12
            Text { text: "Pair Moonlight"; color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold }
            Text { text: "Enter the four-digit PIN shown by Moonlight."; color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            CustomTextField {
                id: pinField
                Layout.fillWidth: true
                maximumLength: 4
                validator: RegularExpressionValidator { regularExpression: /[0-9]{0,4}/ }
                onAccepted: pairButton.clicked()
            }
            Text { id: pinMessage; color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: pinPopup.close() }
                CustomButton {
                    id: pairButton
                    text: "Pair"
                    primary: true
                    onClicked: {
                        let result = backend.pairMoonlightPin(pinField.text, page.pairInstance)
                        pinMessage.text = result["message"]
                        pinMessage.color = result["success"] ? "#86efac" : "#fca5a5"
                        if (result["success"]) pinSuccessCloseTimer.restart()
                    }
                }
            }
        }
    }

    Popup {
        id: presetPopup
        modal: true
        anchors.centerIn: parent
        width: 380
        padding: 22
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        ColumnLayout {
            width: parent.width
            spacing: 12
            Text { text: "Save Session Preset"; color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold }
            CustomTextField { id: presetName; Layout.fillWidth: true; placeholderText: "Preset name"; maximumLength: 32 }
            Text { id: presetMessage; color: "#fca5a5"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: presetPopup.close() }
                CustomButton {
                    text: "Save"
                    primary: true
                    onClicked: {
                        let result = backend.saveCurrentPreset(presetName.text, -1)
                        if (result === "") presetPopup.close()
                        else presetMessage.text = result === "full" ? "Delete or replace an existing preset first." : result
                    }
                }
            }
        }
    }

}
