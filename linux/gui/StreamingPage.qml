import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    // Tab selection for logs filter
    property string logFilter: "ALL"

    // Local arrays to keep logs separated by category
    property var allLogs: []

    property bool enableTouch: true

    Component.onCompleted: {
        let gen = backend.loadGeneralSettings();
        trayCheck.checked = gen["minimize_to_tray"] || false;
        page.enableTouch = gen["enable_touch"] || false;

        let s2 = backend.loadSecondDisplaySettings();
        if (s2) {
            let resIdx = s2ResCombo.find(s2["resolution"] || "1920x1080 (16:9)");
            s2ResCombo.currentIndex = resIdx !== -1 ? resIdx : 2;

            let fpsIdx = s2FpsCombo.find(s2["fps"] || "60");
            s2FpsCombo.currentIndex = fpsIdx !== -1 ? fpsIdx : 1;

            s2BitrateField.text = s2["bitrate"] || "8000";

            let encIdx = s2EncoderCombo.find(s2["encoder"] || "Software (CPU / x264enc)");
            s2EncoderCombo.currentIndex = encIdx !== -1 ? encIdx : 2;
        }
    }

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

        // Active Ports Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 40
            radius: 8
            color: "#161726"
            border.color: "#2a2d55"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                
                Text {
                    text: "📺 Display 1: Port 7110"
                    color: "#a78bfa"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                }
                
                Item { Layout.fillWidth: true }
                
                Text {
                    text: "IP: " + backend.localIp
                    color: "#e0e2ff"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }
                
                Item { Layout.fillWidth: true }
                
                Text {
                    text: "📺 Display 2: Port 7114"
                    color: backend.secondStreamActive ? "#f472b6" : "transparent"
                    font.pixelSize: 12
                    font.weight: Font.Bold
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

            // Add / Remove Second Display button (KDE only)
            Button {
                text: backend.secondStreamActive ? "✕ Remove Display 2" : "➕ Add Display"
                visible: backend.detectedDe === "kde"
                onClicked: {
                    if (backend.secondStreamActive) {
                        backend.stopSecondStream()
                    } else {
                        addDisplayPopup.open()
                    }
                }
                background: Rectangle {
                    implicitWidth: 160
                    implicitHeight: 38
                    color: backend.secondStreamActive
                        ? (parent.down ? "#5a1010" : (parent.hovered ? "#c42830" : "#a82028"))
                        : (parent.down ? "#16182a" : (parent.hovered ? "#222540" : "#1a1c30"))
                    border.color: backend.secondStreamActive ? "#c42830" : "#7c3aed"
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Text {
                    text: parent.text
                    color: backend.secondStreamActive ? "#ffffff" : "#a78bfa"
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

        CustomCheckBox {
            id: trayCheck
            text: "Minimize to tray on close"
            Layout.alignment: Qt.AlignLeft
            Layout.bottomMargin: 10
            onCheckedChanged: {
                backend.saveGeneralSettings(checked, page.enableTouch)
            }
        }
    }

    // ──── Add Display Popup (KDE only) ────
    Popup {
        id: addDisplayPopup
        modal: true
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        width: 460
        height: popupContent.implicitHeight + 60
        padding: 0

        background: Rectangle {
            color: "#12142a"
            border.color: "#2a2d55"
            border.width: 1
            radius: 14
        }

        // Dim overlay
        Overlay.modal: Rectangle {
            color: "#80000000"
        }

        ColumnLayout {
            id: popupContent
            anchors.fill: parent
            anchors.margins: 24
            spacing: 14

            Text {
                text: "Add Second Display"
                font.pixelSize: 18
                font.weight: Font.ExtraBold
                color: "#e0e2ff"
            }

            Text {
                text: "Spawns a second virtual monitor streamed on port 7114.\nA KDE source picker will appear — select 'TabletDisplay2'."
                font.pixelSize: 12
                color: "#6a6c96"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: "#1a1c30" }

            // Settings grid
            GridLayout {
                columns: 2
                columnSpacing: 16
                rowSpacing: 10
                Layout.fillWidth: true

                Text { text: "Resolution:"; color: "#b0b2d0"; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2ResCombo
                    model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)"]
                    currentIndex: 2
                }

                Text { text: "FPS:"; color: "#b0b2d0"; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2FpsCombo
                    model: ["30", "60", "90", "120"]
                    currentIndex: 1
                }

                Text { text: "Bitrate (kbps):"; color: "#b0b2d0"; font.pixelSize: 13 }
                CustomTextField {
                    id: s2BitrateField
                    text: "8000"
                    maximumLength: 5
                }

                Text { text: "Encoder:"; color: "#b0b2d0"; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2EncoderCombo
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                }
            }

            Item { Layout.preferredHeight: 6 }

            // Action buttons
            RowLayout {
                spacing: 12
                Layout.alignment: Qt.AlignRight

                Button {
                    text: "Cancel"
                    onClicked: addDisplayPopup.close()
                    background: Rectangle {
                        implicitWidth: 90
                        implicitHeight: 36
                        color: "transparent"
                        border.color: "#2a2d55"
                        radius: 8
                    }
                    contentItem: Text {
                        text: parent.text
                        color: "#6a6c90"
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                CustomButton {
                    text: "▶  Start Display 2"
                    implicitWidth: 170
                    implicitHeight: 36
                    onClicked: {
                        let cleanRes = s2ResCombo.currentText.split(" ")[0]
                        backend.startSecondStream(
                            cleanRes,
                            s2FpsCombo.currentText,
                            s2BitrateField.text,
                            s2EncoderCombo.currentText
                        )
                        backend.saveSecondDisplaySettings(
                            s2ResCombo.currentText,
                            s2FpsCombo.currentText,
                            s2BitrateField.text,
                            s2EncoderCombo.currentText
                        )
                        addDisplayPopup.close()
                    }
                }
            }
        }
    }
}
