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
    property bool enableStylusFeatures: false
    property bool loadingSettings: true
    property bool showPairingCode: true

    function saveSecondDisplaySettings() {
        if (page.loadingSettings) return
        backend.saveSecondDisplaySettings(
            s2ResCombo.currentText,
            s2FpsCombo.currentText,
            s2BitrateField.text,
            s2EncoderCombo.currentText
        )
    }

    Component.onCompleted: {
        let gen = backend.loadGeneralSettings();
        page.enableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true;
        page.enableStylusFeatures = gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false;
        trayCheck.checked = gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false;

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
        page.loadingSettings = false;
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
                let categoryColor = "#a3e635"
                if (log.type === "STREAMER") categoryColor = "#60a5fa"
                else if (log.type === "INPUT") categoryColor = "#fbbf24"

                let msgColor = "#e2e8f0"
                let lowerMsg = log.message.toLowerCase()
                if (lowerMsg.includes("warning") || lowerMsg.includes("warn")) {
                    msgColor = "#fde047"
                } else if (lowerMsg.includes("error") || lowerMsg.includes("exception") || lowerMsg.includes("failed") || lowerMsg.includes("denied") || lowerMsg.includes("crashed")) {
                    msgColor = "#fca5a5"
                } else if (lowerMsg.includes("success") || lowerMsg.includes("ready") || lowerMsg.includes("listening") || lowerMsg.includes("connected") || lowerMsg.includes("active")) {
                    msgColor = "#86efac"
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
                    color: "#86efac"

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
                    color: "#86efac"
                }

                Text {
                    text: backend.streamingStatus
                    font.pixelSize: 13
                    color: theme.cardTextSecondary
                    Layout.fillWidth: true
                }
            }
        }

        // Active Ports Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 40
            radius: 8
            color: theme.surfaceAlt
            border.color: theme.border
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                
                Text {
                    text: "📺 Second display: Port 7110"
                    color: theme.accent
                    font.pixelSize: 12
                    font.weight: Font.Bold
                }
                
                Item { Layout.fillWidth: true }
                
                Text {
                    text: "IP: " + backend.localIp
                    color: theme.cardTextPrimary
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }
                
                Item { Layout.fillWidth: true }
                
                Text {
                    text: "📺 Third display: Port 7114"
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
                        color: isSelected ? theme.accent : theme.surface
                        border.color: isSelected ? theme.accent : theme.border
                        radius: 6
                    }
                    contentItem: Text {
                        text: parent.text
                        color: theme.cardTextPrimary
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
            color: theme.logBoxBackground
            border.color: theme.border
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
                    color: theme.cardTextPrimary
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
                    color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                    border.color: theme.border
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Text {
                    text: parent.text
                    color: theme.cardTextSecondary
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            // Add / Remove Third Display button (KDE only)
            Button {
                id: displayActionButton
                text: backend.secondStreamActive ? "Remove Third Display" : "Add Third Display"
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
                    border.color: backend.secondStreamActive ? "#c42830" : theme.accent
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Item {
                    implicitWidth: displayActionContent.implicitWidth
                    implicitHeight: 38

                    Row {
                        id: displayActionContent
                        anchors.centerIn: parent
                        spacing: 8

                        Image {
                            width: 16
                            height: 16
                            anchors.verticalCenter: parent.verticalCenter
                            source: "../assets/svg/display-add.svg"
                            sourceSize.width: 16
                            sourceSize.height: 16
                            visible: !backend.secondStreamActive
                        }

                        Text {
                            text: displayActionButton.text
                            color: backend.secondStreamActive ? theme.textPrimary : theme.accent
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
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
                    focus: true
                    antialiasing: true
                }
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                visible: backend.pairingCode !== ""
                implicitWidth: pairingCodeRow.implicitWidth + 24
                implicitHeight: 38
                radius: 8
                color: theme.surface
                border.color: theme.accent

                Row {
                    id: pairingCodeRow
                    anchors.centerIn: parent
                    spacing: 8

                    Text {
                        id: pairingText
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Pairing code: " + (page.showPairingCode ? backend.pairingCode : "••••••")
                        color: theme.accent
                        font.pixelSize: 13
                        font.weight: Font.Bold
                    }

                    Button {
                        anchors.verticalCenter: parent.verticalCenter
                        flat: true
                        onClicked: page.showPairingCode = !page.showPairingCode
                        ToolTip.visible: hovered
                        ToolTip.text: page.showPairingCode ? "Hide pairing code" : "Show pairing code"
                        contentItem: Image {
                            source: page.showPairingCode
                                ? "../assets/svg/eye-open.svg"
                                : "../assets/svg/eye-closed.svg"
                            sourceSize.width: 20
                            sourceSize.height: 20
                            fillMode: Image.PreserveAspectFit
                        }
                    }
                }
            }
        }

        CustomCheckBox {
            id: trayCheck
            text: "Minimize to tray on close"
            Layout.alignment: Qt.AlignLeft
            Layout.bottomMargin: 10
            onCheckedChanged: {
                if (!page.loadingSettings)
                    backend.saveGeneralSettings(checked, page.enableTouch, page.enableStylusFeatures)
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
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
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
                text: "Add Third Display"
                font.pixelSize: 18
                font.weight: Font.ExtraBold
                color: theme.cardTextPrimary
            }

            Text {
                text: "Spawns the third display stream on port 7114.\nA KDE source picker will appear — select 'TabletDisplay2'."
                font.pixelSize: 12
                color: theme.cardTextMuted
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: theme.border }

            // Settings grid
            GridLayout {
                columns: 2
                columnSpacing: 16
                rowSpacing: 10
                Layout.fillWidth: true

                Text { text: "Resolution:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2ResCombo
                    model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)"]
                    currentIndex: 2
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text { text: "FPS:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2FpsCombo
                    model: ["30", "60", "90", "120"]
                    currentIndex: 1
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text { text: "Bitrate (kbps):"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomTextField {
                    id: s2BitrateField
                    text: "8000"
                    maximumLength: 5
                    onTextEdited: page.saveSecondDisplaySettings()
                }

                Text { text: "Encoder:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2EncoderCombo
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                    onActivated: page.saveSecondDisplaySettings()
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
                        border.color: theme.border
                        radius: 8
                    }
                    contentItem: Text {
                        text: parent.text
                        color: theme.cardTextSecondary
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                CustomButton {
                    text: "▶  Start Third Display"
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
                        page.saveSecondDisplaySettings()
                        addDisplayPopup.close()
                    }
                }
            }
        }
    }
}
