import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SectionCard {
    id: card
    objectName: "vkmsManagementCard"
    collapsible: false
    title: "Monitorize VKMS"
    symbol: "display"
    property var status: backend.vkmsManagementStatus
    property bool detailsExpanded: false
    VkmsInstallNotice {
        Layout.fillWidth: true
        visible: card.status.installed === false && !card.status.checking
        description: "Required for remote login and creating virtual displays on Niri and Cinnamon (X11)."
        onInstallRequested: backend.openMonitorizeVkmsInstallPage()
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: card.status.installed !== false || card.status.checking
        spacing: 12
        Text {
            objectName: "vkmsHealthMessage"
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: !card.status.busy && card.status.completion_message ? card.status.completion_message
                : card.status.checking ? "Verifying installation…"
                : card.status.busy ? card.status.operation_message
                : (card.status.message || "Checking…")
            color: card.status.busy || card.status.checking ? theme.textPrimary
                : card.status.state === "working" || card.status.state === "restart_required" ? theme.successText : theme.textPrimary
        }
        Text {
            visible: (card.status.version || "").length > 0
            text: "Installed version: " + (card.status.version || "")
            color: theme.textSecondary
        }
        Text {
            visible: card.status.up_to_date && !card.status.busy && !card.status.completion_message
            objectName: "vkmsCurrentNotice"
            text: "Already up to date"
            font.pixelSize: 12
            color: theme.successText
        }
        Flow {
            spacing: 8
            visible: card.status.installed === true
            Layout.fillWidth: true
            CustomButton {
                objectName: "vkmsReinstallButton"
                width: implicitWidth
                height: implicitHeight
                text: "Reinstall"; primary: false
                enabled: !card.status.checking && !card.status.busy && !backend.systemSetupRunning
                    && !backend.remoteLoginSetupBusy && !backend.applyingConfiguration
                onClicked: {
                    card.detailsExpanded = true
                    backend.reinstallMonitorizeVkms()
                }
            }
            CustomButton {
                objectName: "vkmsUpdateButton"
                width: implicitWidth
                height: implicitHeight
                text: "Update"
                enabled: !card.status.checking && !card.status.busy && !backend.systemSetupRunning
                    && !backend.remoteLoginSetupBusy && !backend.applyingConfiguration
                onClicked: {
                    card.detailsExpanded = true
                    backend.updateMonitorizeVkms()
                }
            }
            CustomButton {
                objectName: "vkmsDetailsButton"
                width: implicitWidth
                height: implicitHeight
                text: card.detailsExpanded ? "Hide details" : "Show details"
                primary: false
                onClicked: card.detailsExpanded = !card.detailsExpanded
            }
        }
        SelectableMessage {
            objectName: "vkmsOperationMessage"
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: card.status.busy ? "" : (card.status.operation_message || "")
            visible: text.length > 0
            color: theme.textSecondary
        }
        ScrollView {
            id: detailsScroll
            Layout.fillWidth: true
            Layout.preferredHeight: 180
            visible: card.detailsExpanded
            clip: true
            contentWidth: availableWidth
            onVisibleChanged: if (visible) Qt.callLater(logFlick.scrollToLatest)
            Flickable {
                id: logFlick
                objectName: "vkmsLogViewport"
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: width
                contentHeight: logText.height
                function scrollToLatest() {
                    if (!detailsScroll.visible) return
                    logText.cursorPosition = logText.length
                    contentY = Math.max(originY, originY + contentHeight - height)
                }
                onContentHeightChanged: Qt.callLater(scrollToLatest)
                onHeightChanged: Qt.callLater(scrollToLatest)
                TextArea.flickable: TextArea {
                    id: logText
                    objectName: "vkmsLogText"
                    text: (card.status.details || "") + ((card.status.logs || "").length > 0 ? "\n" + card.status.logs : "")
                    onTextChanged: Qt.callLater(logFlick.scrollToLatest)
                    placeholderText: "No VKMS log output yet."
                    placeholderTextColor: theme.textSecondary
                    font.family: "monospace"
                    readOnly: true; selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    color: theme.textSecondary
                    background: Rectangle { color: theme.logBoxBackground; radius: theme.controlRadius }
                }
            }
        }
    }
}
