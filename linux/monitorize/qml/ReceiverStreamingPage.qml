import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    property var allLogs: []

    focus: true
    Keys.onEscapePressed: backend.stopReceiving()

    Component.onCompleted: {
        forceActiveFocus()
        videoHost.createVideoItem()
    }

    Component.onDestruction: backend.setReceiverVideoItem(null)

    Connections {
        target: backend
        function onReceiverLogAppended(msg) {
            page.appendLog(msg)
        }
    }

    function appendLog(msg) {
        allLogs.push(msg)
        if (allLogs.length > 8) {
            allLogs.shift()
        }
        logArea.text = allLogs.join("\n")
    }

    Rectangle {
        anchors.fill: parent
        color: "#000000"
    }

    HoverHandler {
        id: overlayHover
    }

    Item {
        id: videoHost
        anchors.fill: parent
        property var videoItem: null

        function createVideoItem() {
            if (videoItem !== null) {
                backend.setReceiverVideoItem(videoItem)
                return
            }
            try {
                videoItem = Qt.createQmlObject(
                    "import QtQuick\n" +
                    "import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0\n" +
                    "GstGLQt6VideoItem {" +
                    "    objectName: \"receiverVideoItem\";" +
                    "    anchors.fill: parent;" +
                    "    forceAspectRatio: false;" +
                    "    acceptEvents: false" +
                    "}",
                    videoHost,
                    "receiverVideoItem"
                )
                backend.setReceiverVideoItem(videoItem)
            } catch (err) {
                backend.setReceiverVideoItem(null)
                page.appendLog("Embedded video unavailable; using fallback player window.")
                page.appendLog(String(err))
            }
        }
    }

    Rectangle {
        id: topBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 56
        color: "#aa000000"
        visible: overlayHover.hovered || backend.receiverStatus.indexOf("Receiving from") !== 0

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 18
            anchors.rightMargin: 18
            spacing: 14

            Rectangle {
                width: 10
                height: 10
                radius: 5
                color: backend.receiverStatus.indexOf("Receiving from") === 0 ? "#22c55e" : "#facc15"
            }

            Text {
                text: backend.receiverStatus
                color: "#f8fafc"
                font.pixelSize: 13
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Button {
                text: "Disconnect"
                onClicked: backend.stopReceiving()
                background: Rectangle {
                    implicitWidth: 116
                    implicitHeight: 34
                    color: parent.down ? "#7f1d1d" : (parent.hovered ? "#b91c1c" : "#991b1b")
                    radius: 6
                }
                contentItem: Text {
                    text: parent.text
                    color: "#ffffff"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: logArea.text.length > 0 && overlayHover.hovered ? 96 : 0
        color: "#aa000000"
        clip: true

        Text {
            id: logArea
            anchors.fill: parent
            anchors.margins: 12
            color: "#cbd5e1"
            font.family: "Fira Code, JetBrains Mono, DejaVu Sans Mono, Consolas, monospace"
            font.pixelSize: 11
            elide: Text.ElideRight
            wrapMode: Text.WrapAnywhere
        }
    }
}
