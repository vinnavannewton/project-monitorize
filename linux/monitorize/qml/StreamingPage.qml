import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property int pairInstance: 1
    property bool logsExpanded: false
    property bool followLatestLogs: true
    property bool updatingLogScroll: false
    function logAtBottom() {
        let flick = logScroll.contentItem
        return flick.contentY >= Math.max(0, flick.contentHeight - flick.height) - 16
    }
    function scrollLogsToEnd() {
        let flick = logScroll.contentItem
        page.updatingLogScroll = true
        flick.contentY = Math.max(0, flick.contentHeight - flick.height)
        page.updatingLogScroll = false
    }
    function refreshDiagnostics() {
        let snapshot = backend.sessionLog()
        if (logArea.text === snapshot) return
        let previousY = logScroll.contentItem.contentY
        page.updatingLogScroll = true
        logArea.text = snapshot
        Qt.callLater(function() {
            let flick = logScroll.contentItem
            if (page.followLatestLogs)
                page.scrollLogsToEnd()
            else
                flick.contentY = Math.min(previousY, Math.max(0, flick.contentHeight - flick.height))
            page.updatingLogScroll = false
        })
    }
    function openPair(instance) {
        pairInstance = instance
        pinField.text = ""
        pinMessage.text = ""
        pinPopup.open()
        pinField.forceActiveFocus()
    }
    Connections {
        target: backend
        function onStreamingStartFailed() { page.logsExpanded = true }
        function onStreamingCodecMismatch(message) { page.logsExpanded = true }
    }
    onLogsExpandedChanged: {
        if (logsExpanded) {
            page.followLatestLogs = true
            Qt.callLater(function() {
                page.refreshDiagnostics()
                page.scrollLogsToEnd()
            })
        }
    }
    Timer {
        interval: 1000; repeat: true; running: page.logsExpanded
        onTriggered: page.refreshDiagnostics()
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
                    color: backend.sessionBusy ? "#efbd5a" : (backend.sessionRunning ? "#34d681" : theme.textMuted)
                }
                Text {
                    text: backend.sessionBusy ? "Preparing session" : (backend.sessionRunning ? "Session active" : "Session")
                    color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold
                    Layout.fillWidth: true
                }
            }
            Text {
                text: "Create, manage, and stream your virtual screens."
                color: theme.textMuted; font.pixelSize: 13
                Layout.fillWidth: true
            }
            RowLayout {
                visible: backend.sessionMode === "Extend"
                Layout.fillWidth: true
                Layout.topMargin: 18
                spacing: 16
                Rectangle {
                    Layout.preferredWidth: 48; Layout.preferredHeight: 48
                    radius: 10; color: theme.surfaceAlt
                    LineIcon {
                        symbol: "display"
                        anchors.centerIn: parent
                        width: 26; height: 26
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: "Virtual Displays"
                        color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.DemiBold
                    }
                    Text {
                        text: "Manage the displays in this session."
                        color: theme.textSecondary; font.pixelSize: 13
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                    }
                    TextEdit {
                        text: backend.streamingStatus
                        visible: text.length > 0
                        color: theme.textSecondary; font.pixelSize: 13
                        Layout.fillWidth: true; wrapMode: TextEdit.Wrap
                        readOnly: true; selectByMouse: true
                        activeFocusOnPress: true
                    }
                }
            }
            Rectangle {
                visible: backend.sessionMode === "Mirror"
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
                            text: "Mirror your screen"
                            font.pixelSize: 18; font.weight: Font.DemiBold; color: theme.textPrimary
                        }
                        TextEdit {
                            text: backend.streamingStatus || "Start to share your existing screen with Moonlight."
                            Layout.fillWidth: true; wrapMode: TextEdit.Wrap
                            color: theme.textSecondary; font.pixelSize: 13
                            readOnly: true; selectByMouse: true
                            activeFocusOnPress: true
                        }
                        TextEdit {
                            visible: backend.sessionMode === "Mirror" && backend.sessionRunning
                            text: backend.localIp + ":47989"; color: theme.textSecondary; font.pixelSize: 13
                            Layout.fillWidth: true; wrapMode: TextEdit.WrapAnywhere
                            readOnly: true; selectByMouse: true
                            activeFocusOnPress: true
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
                    implicitHeight: Math.max(88, statusColumn.implicitHeight + 36)
                    radius: 14; color: theme.surface; border.color: theme.border
                    Item {
                        anchors.fill: parent; anchors.margins: 18
                        LineIcon {
                            id: displayIcon
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            symbol: "display"
                            width: 30; height: 30
                        }
                        Column {
                            id: statusColumn
                            anchors.left: displayIcon.right
                            anchors.leftMargin: 18
                            anchors.right: displayActions.left
                            anchors.rightMargin: 16
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 5
                            Text {
                                text: displayCard.modelData.title
                                color: theme.textPrimary; font.pixelSize: 16; font.weight: Font.DemiBold
                            }
                            RowLayout {
                                width: parent.width
                                spacing: 7
                                Rectangle {
                                    Layout.preferredWidth: 8; Layout.preferredHeight: 8
                                    radius: 4
                                    color: displayCard.modelData.live ? "#34d681" : theme.textMuted
                                }
                                TextEdit {
                                    text: backend.sessionRunning ? displayCard.modelData.address : displayCard.modelData.state
                                    color: theme.textSecondary; font.pixelSize: 12
                                    Layout.fillWidth: true; wrapMode: TextEdit.WrapAnywhere
                                    readOnly: true; selectByMouse: true
                                    activeFocusOnPress: true
                                }
                            }
                        }
                        AbstractButton {
                            id: displayActions
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: 36; implicitHeight: 36
                            enabled: !backend.sessionBusy
                            Accessible.name: "Virtual display " + displayCard.modelData.number + " actions"
                            background: Rectangle {
                                radius: theme.controlRadius
                                color: displayActions.hovered ? theme.surfaceAlt : "transparent"
                                border.color: theme.border
                            }
                            contentItem: Text {
                                text: "⋮"; color: theme.textSecondary; font.pixelSize: 23
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            onClicked: displayMenu.open()
                            Menu {
                                id: displayMenu
                                width: 220
                                background: Rectangle { color: theme.surface; border.color: theme.border; radius: 8 }
                                CardMenuItem { text: "Sunshine settings"; enabled: backend.sunshineAvailable && !backend.sunshineSettingsOpening; onTriggered: backend.openSunshineWebUi(displayCard.modelData.number) }
                                CardMenuItem {
                                    visible: displayCard.modelData.number === 2
                                    text: "Remove"
                                    onTriggered: backend.removeSessionDisplay(1)
                                }
                            }
                        }
                    }
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 10
                CustomButton { text: "Pair Moonlight PIN"; iconSymbol: "link"; implicitHeight: 46; primary: false; visible: backend.streamingBackend !== "none"; enabled: backend.sessionRunning && !backend.sessionBusy; onClicked: page.openPair(1) }
                CustomButton {
                    text: "Save Preset"; iconSymbol: "bookmark"; primary: false; enabled: backend.canSavePreset
                    implicitWidth: 150; implicitHeight: 46
                    onClicked: {
                        presetTarget.currentIndex = 0
                        presetName.text = ""
                        presetMessage.text = ""
                        presetPopup.open()
                    }
                }
                CustomButton {
                    text: backend.isStreaming || backend.sessionBusy ? "Stop" : "Start"
                    implicitWidth: 150; implicitHeight: 46
                    iconSymbol: text === "Stop" ? "stop" : "play"
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
            TextEdit {
                text: backend.sunshineSettingsMessage
                visible: text.length > 0
                Layout.fillWidth: true
                wrapMode: TextEdit.Wrap
                color: theme.textSecondary
                readOnly: true; selectByMouse: true
                activeFocusOnPress: true
            }
            SectionCard {
                title: "Diagnostics & logs"; symbol: "logs"; expanded: page.logsExpanded
                onExpandedChanged: page.logsExpanded = expanded
                Layout.fillWidth: true
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240
                    ScrollView {
                        id: logScroll
                        anchors.fill: parent
                        Connections {
                            target: logScroll.contentItem
                            function onContentYChanged() {
                                if (!page.updatingLogScroll)
                                    page.followLatestLogs = page.logAtBottom()
                            }
                        }
                        TextArea {
                            id: logArea
                            text: ""
                            readOnly: true; wrapMode: TextEdit.Wrap
                            color: theme.textSecondary; font.family: "monospace"; font.pixelSize: 11
                            background: Rectangle { color: theme.logBoxBackground; radius: 8 }
                        }
                    }
                    Button {
                        width: 36; height: 36
                        anchors.right: parent.right; anchors.bottom: parent.bottom
                        anchors.margins: 12
                        visible: page.logsExpanded && !page.followLatestLogs
                            && logScroll.contentItem.contentHeight > logScroll.contentItem.height
                        text: "↓"
                        Accessible.name: "Jump to latest logs"
                        onClicked: {
                            page.followLatestLogs = true
                            page.scrollLogsToEnd()
                        }
                        background: Rectangle {
                            radius: 18
                            color: parent.hovered ? theme.buttonBackgroundHover : theme.buttonBackground
                            border.color: theme.border
                        }
                        contentItem: Text {
                            text: parent.text
                            color: theme.buttonText
                            font.pixelSize: 20
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
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
            CustomComboBox {
                id: presetTarget
                Layout.fillWidth: true
                model: ["New preset"].concat(backend.presets.map(function(preset) {
                    return "Replace " + preset.name
                }))
                onActivated: function(index) {
                    presetName.text = index > 0 ? backend.presets[index - 1].name : ""
                    presetMessage.text = ""
                }
            }
            CustomTextField { id: presetName; Layout.fillWidth: true; placeholderText: "Preset name"; maximumLength: 32 }
            Text { id: presetMessage; color: "#fca5a5"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: presetPopup.close() }
                CustomButton {
                    text: "Save"
                    primary: true
                    onClicked: {
                        let result = backend.saveCurrentPreset(presetName.text, presetTarget.currentIndex - 1)
                        if (result === "") presetPopup.close()
                        else presetMessage.text = result === "full" ? "Choose a preset to replace or delete one first." : result
                    }
                }
            }
        }
    }

}
