import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    property var allLogs: []

    Connections {
        target: backend
        function onReceiverLogAppended(msg) {
            page.appendLog(msg)
        }
    }

    function appendLog(msg) {
        allLogs.push(msg)
        updateLogDisplay()
    }

    function updateLogDisplay() {
        let text = ""
        for (let i = 0; i < allLogs.length; i++) {
            let logMsg = allLogs[i]
            let msgColor = "#e2e8f0"
            let lowerMsg = logMsg.toLowerCase()
            if (lowerMsg.includes("error") || lowerMsg.includes("failed")) {
                msgColor = "#fca5a5"
            } else if (lowerMsg.includes("connected") || lowerMsg.includes("playing") || lowerMsg.includes("receiving")) {
                msgColor = "#86efac"
            } else if (lowerMsg.includes("connecting") || lowerMsg.includes("waiting")) {
                msgColor = "#fde047"
            }
            text += "<b><font color='#60a5fa'>[RECEIVER]</font></b> &nbsp;<font color='" + msgColor + "'>" + logMsg + "</font><br>"
        }
        logArea.text = text
        logScrollView.contentItem.contentY = Math.max(0, logArea.implicitHeight - logScrollView.height)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14

        // Top Status Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 60
            radius: theme.cardRadius
            color: theme.surface
            border.color: theme.border
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                spacing: 20

                // Pulsing Active Indicator
                Rectangle {
                    width: 12
                    height: 12
                    radius: 6
                    color: theme.accent

                    SequentialAnimation on opacity {
                        running: backend.isReceiving
                        loops: Animation.Infinite
                        NumberAnimation { from: 0.3; to: 1.0; duration: 800 }
                        NumberAnimation { from: 1.0; to: 0.3; duration: 800 }
                    }
                }

                Text {
                    text: "Receiving Stream"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: theme.accent
                }

                Text {
                    text: backend.receiverStatus
                    font.pixelSize: 13
                    color: theme.cardTextSecondary
                    Layout.fillWidth: true
                }
            }
        }

        // Info card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: infoCol.implicitHeight + 20
            radius: 8
            color: theme.surfaceAlt
            border.color: theme.border
            border.width: 1

            ColumnLayout {
                id: infoCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 16
                spacing: 6

                Text {
                    text: "Connected to: " + backend.receiverHostIp
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    color: theme.cardTextPrimary
                }
                Text {
                    text: "The GStreamer player window should appear. Press Esc in that window to close it."
                    font.pixelSize: 12
                    color: theme.cardTextMuted
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
            }
        }

        // Log Box Container
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.logBoxBackground
            border.color: theme.border
            border.width: 1
            radius: 8

            ScrollView {
                id: logScrollView
                anchors.fill: parent
                anchors.margins: 10
                clip: true

                TextEdit {
                    id: logArea
                    textFormat: Text.RichText
                    font.family: "Fira Code, JetBrains Mono, DejaVu Sans Mono, Consolas, monospace"
                    font.pixelSize: 12
                    color: theme.cardTextPrimary
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.WrapAnywhere
                    leftPadding: 8
                    rightPadding: 8
                    topPadding: 8
                    bottomPadding: 8

                    onImplicitHeightChanged: {
                        logScrollView.contentItem.contentY = Math.max(0, implicitHeight - logScrollView.height)
                    }

                }
            }
        }

        // Bottom control
        RowLayout {
            spacing: 12
            Layout.alignment: Qt.AlignLeft
            Layout.bottomMargin: 10

            Button {
                text: "⏹ Disconnect"
                onClicked: {
                    allLogs = []
                    backend.stopReceiving()
                }
                background: Rectangle {
                    implicitWidth: 150
                    implicitHeight: 38
                    color: parent.down ? "#5a1010" : (parent.hovered ? "#c42830" : "#a82028")
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Text {
                    text: parent.text
                    color: "#ffffff"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
