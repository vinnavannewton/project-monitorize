import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property int pairInstance: 1
    property int pairRequest: 0
    property bool logsExpanded: false
    property bool followLatestLogs: true
    property bool updatingLogScroll: false
    property string logOpenError: ""
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
        function onPairMoonlightFinished(request, success, message) {
            if (request !== page.pairRequest || !pinPopup.visible) return
            page.pairRequest = 0
            pinMessage.text = message
            pinMessage.color = success ? theme.successText : theme.errorText
            if (success) pinSuccessCloseTimer.restart()
        }
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
        interval: 1000; repeat: true
        running: page.logsExpanded && backend.uiVisible
            && page.StackView.view && page.StackView.view.currentItem === page
        onRunningChanged: {
            if (running) Qt.callLater(function() { if (running) page.refreshDiagnostics() })
        }
        onTriggered: page.refreshDiagnostics()
    }
    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 16
            SectionCard {
                objectName: "remoteLoginCard"
                title: "REMOTE LOGIN"; symbol: "session"
                visible: backend.remoteLoginEnabled
                Layout.fillWidth: true
                Text {
                    text: backend.remoteLoginMessage
                    visible: !remoteError.visible
                    color: theme.textPrimary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                TextEdit {
                    text: backend.localIp + ":47989"
                    readOnly: true; selectByMouse: true
                    color: theme.textSecondary; Layout.fillWidth: true
                }
                DisplayError { id: remoteError; instance: 1; Layout.fillWidth: true }
            }
            Rectangle {
                visible: !backend.remoteLoginEnabled && backend.sessionMode === "Mirror"
                Layout.fillWidth: true
                implicitHeight: summary.implicitHeight + 40
                radius: theme.cardRadius; color: theme.surface; border.color: theme.border
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
                            visible: !mirrorError.visible
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
                        DisplayError { id: mirrorError; instance: 1; Layout.fillWidth: true }
                    }
                }
            }
            Repeater {
                model: backend.sessionMode === "Mirror" ? [] : backend.sessionDisplays
                delegate: Rectangle {
                    id: displayCard
                    objectName: "sessionDisplayCard" + modelData.number
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: Math.max(88, statusColumn.implicitHeight + 36)
                    radius: theme.cardRadius; color: theme.surface; border.color: theme.border
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
                            anchors.right: parent.right
                            anchors.rightMargin: 0
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
                                    text: displayCard.modelData.state + (backend.sessionRunning ? "\n" + displayCard.modelData.address : "")
                                    color: theme.textSecondary; font.pixelSize: 12
                                    Layout.fillWidth: true; wrapMode: TextEdit.WrapAnywhere
                                    readOnly: true; selectByMouse: true
                                    activeFocusOnPress: true
                                }
                            }
                            DisplayError { instance: displayCard.modelData.number; width: parent.width }
                        }
                    }
                }
            }
            RowLayout {
                objectName: "sessionActionButtons"
                Layout.fillWidth: true
                spacing: 10
                CustomButton { objectName: "pairMoonlightButton"; Layout.fillWidth: true; text: "Pair Moonlight PIN"; iconSymbol: "link"; implicitHeight: 46; primary: false; visible: backend.appliedStreamingBackend !== "none"; enabled: backend.sessionRunning && !backend.sessionBusy; onClicked: page.openPair(1) }
                CustomButton {
                    Layout.fillWidth: true
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
                    Layout.fillWidth: true
                    text: backend.appliedStreamingBackend === "none" ? (backend.isStreaming ? "Remove" : "Create")
                        : backend.applyingConfiguration ? "Restarting…" : "Restart"
                    implicitWidth: 150; implicitHeight: 46
                    iconSymbol: backend.appliedStreamingBackend === "none" ? (backend.isStreaming ? "stop" : "play") : "reset"
                    enabled: !backend.applyingConfiguration && !backend.sunshineChoicesSaving
                    onClicked: {
                        if (backend.appliedStreamingBackend === "none") {
                            if (backend.isStreaming) backend.stopSession()
                            else backend.startSession()
                        } else backend.restartSession()
                    }
                }
                CustomButton {
                    Layout.fillWidth: true
                    objectName: "stopStreamButton"
                    text: "Stop"; primary: false; iconSymbol: "stop"
                    implicitWidth: 150; implicitHeight: 46
                    iconTint: theme.dark ? "#ff777f" : "#c03943"
                    visible: backend.appliedStreamingBackend !== "none" && (backend.isStreaming || backend.sessionBusy || backend.applyingConfiguration)
                    onClicked: backend.stopSession()
                }
                CustomButton {
                    Layout.fillWidth: true
                    text: "Start added display"
                    visible: false
                    enabled: !backend.sessionBusy
                    onClicked: backend.startSession()
                }
                CustomButton { Layout.fillWidth: true; text: "Display Settings"; primary: false; visible: backend.canConfigureDisplay; onClicked: backend.configureDisplay() }
            }
            Text {
                objectName: "savedConfigurationRestartNotice"
                text: "Changes saved. Restart streaming to apply."
                visible: backend.configurationRestartRequired
                color: theme.textSecondary
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
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
                            textFormat: TextEdit.PlainText
                            readOnly: true; wrapMode: TextEdit.Wrap
                            color: theme.textSecondary; font.family: "monospace"; font.pixelSize: 11
                            background: Rectangle { color: theme.logBoxBackground; radius: theme.controlRadius }
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
                CustomButton {
                    text: "Open full log"
                    primary: false
                    onClicked: fullLogMenu.open()
                    Menu {
                        id: fullLogMenu
                        y: parent.height
                        width: 180
                        background: Rectangle {
                            color: theme.surface
                            border.color: theme.border
                            radius: theme.controlRadius
                        }
                        CardMenuItem { text: "Monitorize"; onTriggered: page.logOpenError = backend.openDiagnosticLog(0) ? "" : "Monitorize log is unavailable." }
                        CardMenuItem { text: "Sunshine 1"; onTriggered: page.logOpenError = backend.openDiagnosticLog(1) ? "" : "Sunshine 1 log is unavailable." }
                        CardMenuItem { text: "Sunshine 2"; onTriggered: page.logOpenError = backend.openDiagnosticLog(2) ? "" : "Sunshine 2 log is unavailable." }
                    }
                }
                Text {
                    visible: page.logOpenError.length > 0
                    text: page.logOpenError
                    color: theme.errorText
                    Layout.fillWidth: true
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
        onClosed: {
            pinSuccessCloseTimer.stop()
            page.pairRequest = 0
        }
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
                    text: backend.pairingRunning ? "Verifying…" : "Pair"
                    primary: true
                    enabled: !backend.pairingRunning && pinField.text.length === 4
                    onClicked: page.pairRequest = backend.startPairMoonlightPin(pinField.text, page.pairInstance)
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
            Text { id: presetMessage; color: theme.errorText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
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
