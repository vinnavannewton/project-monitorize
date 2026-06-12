import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    // Tab selection for logs filter
    property string logFilter: "ALL"

    // Local arrays to keep logs separated by category
    property var allLogs: []

    // Listen directly for log signals from the Python backend
    Connections {
        target: backend
        function onLogAppended(type, msg) {
            page.appendLog(type, msg)
        }
    }

    function appendLog(type, msg) {
        allLogs.push({ type: type, message: msg })
        updateLogDisplay()
    }

    function updateLogDisplay() {
        let text = ""
        for (let i = 0; i < allLogs.length; i++) {
            let log = allLogs[i]
            if (logFilter === "ALL" || log.type === logFilter) {
                let categoryColor = "#7cc87c"
                if (log.type === "STREAMER") categoryColor = "#5c9eff"
                else if (log.type === "INPUT") categoryColor = "#e8a840"

                let msgColor = "#b8bad8"
                let lowerMsg = log.message.toLowerCase()
                if (lowerMsg.includes("warning") || lowerMsg.includes("warn")) {
                    msgColor = "#e8a840"
                } else if (lowerMsg.includes("error") || lowerMsg.includes("exception") || lowerMsg.includes("failed") || lowerMsg.includes("denied") || lowerMsg.includes("crashed")) {
                    msgColor = "#ff6b6b"
                } else if (lowerMsg.includes("success") || lowerMsg.includes("ready") || lowerMsg.includes("listening") || lowerMsg.includes("connected") || lowerMsg.includes("active")) {
                    msgColor = "#4cd68d"
                }

                let tag = "[" + log.type + "]"
                text += "<b><font color='" + categoryColor + "'>" + tag + "</font></b> &nbsp;<font color='" + msgColor + "'>" + log.message + "</font><br>"
            }
        }
        logArea.text = text
        // Scroll to bottom
        logScrollView.contentItem.contentY = Math.max(0, logArea.implicitHeight - logScrollView.height)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14

        // Top Status Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 60
            radius: 12
            color: "#12142a"
            border.color: "#2a2d55"
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
                    color: "#4cd68d"

                    OpacityAnimator {
                        target: parent.children[0]
                        from: 0.3
                        to: 1.0
                        duration: 800
                        running: backend.isStreaming
                        loops: Animation.Infinite
                    }
                }

                Text {
                    text: backend.countdown > 0 ? ("Streaming starting in " + backend.countdown + "...") : "Streaming Active"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: "#4cd68d"
                }

                Text {
                    text: backend.streamingStatus
                    font.pixelSize: 13
                    color: "#8a8cc0"
                    Layout.fillWidth: true
                }
            }
        }

        // Log Tab Filters
        RowLayout {
            spacing: 10
            Layout.fillWidth: true

            Repeater {
                model: ["ALL", "STREAMER", "INPUT"]
                Button {
                    text: modelData
                    property bool isSelected: page.logFilter === modelData
                    onClicked: {
                        page.logFilter = modelData
                        page.updateLogDisplay()
                    }
                    background: Rectangle {
                        implicitWidth: 100
                        implicitHeight: 32
                        color: isSelected ? "#3538b0" : "#12142a"
                        border.color: isSelected ? "#4c4fd0" : "#2a2d55"
                        radius: 6
                    }
                    contentItem: Text {
                        text: parent.text
                        color: isSelected ? "#ffffff" : "#6a6c96"
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }

        // Log Box Container
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#080910"
            border.color: "#1a1c30"
            border.width: 1
            radius: 8

            ScrollView {
                id: logScrollView
                anchors.fill: parent
                anchors.margins: 10
                clip: true

                TextArea {
                    id: logArea
                    textFormat: Text.RichText
                    font.family: "Fira Code, JetBrains Mono, DejaVu Sans Mono, Consolas, monospace"
                    font.pixelSize: 12
                    color: "#b8bad8"
                    readOnly: true
                    selectByMouse: true
                    wrapMode: Text.WrapAnywhere
                    background: null
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

        // Bottom control buttons
        RowLayout {
            spacing: 12
            Layout.alignment: Qt.AlignLeft
            Layout.bottomMargin: 10

            Button {
                text: "⚙ Display Config"
                visible: backend.detectedDe === "hyprland"
                onClicked: {
                    backend.configureDisplay()
                }
                background: Rectangle {
                    implicitWidth: 140
                    implicitHeight: 38
                    color: parent.down ? "#16182a" : (parent.hovered ? "#222540" : "#1a1c30")
                    border.color: "#2a2d55"
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Text {
                    text: parent.text
                    color: "#b8bad8"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Button {
                text: "⏹ Stop Streaming"
                onClicked: {
                    allLogs = []
                    backend.stopStreaming()
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
