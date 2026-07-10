import QtQuick
import QtQuick.Controls
import QtQuick.Layouts


Item {
    id: page

    property bool enableTouch: true
    property bool enableStylusFeatures: false
    property bool loadingSettings: true
    property bool showPairingCode: true
    property int duplicatePresetIndex: -1
    property bool syncingSecondBitrate: false
    readonly property int actionButtonWidth: 160
    readonly property int actionButtonHeight: 38
    readonly property int streamInfoColumns: 3
    readonly property int streamInfoCardHeight: 28
    readonly property int streamInfoSpacing: 10
    readonly property var streamInfoBaseItems: ["Second Display  Port 7110", "Host  " + backend.localIp]
    readonly property var streamInfoItems: backend.secondStreamActive
        ? page.streamInfoBaseItems.concat(["Third Display  Port 7114"])
        : page.streamInfoBaseItems
    readonly property int streamInfoVisibleColumns: Math.max(
        1, Math.min(page.streamInfoColumns, page.streamInfoItems.length)
    )
    readonly property int streamInfoRows: Math.max(
        1, Math.ceil(page.streamInfoItems.length / page.streamInfoColumns)
    )

    function clampMbps(value) {
        let number = Number(value)
        if (!isFinite(number)) number = 8
        return Math.max(0.25, Math.min(100, number))
    }

    function formatMbps(value) {
        let rounded = Math.round(page.clampMbps(value) * 100) / 100
        if (rounded % 1 === 0) return rounded.toFixed(0)
        return (rounded * 10) % 1 === 0 ? rounded.toFixed(1) : rounded.toFixed(2)
    }

    function secondBitrateKbpsText() {
        return String(Math.round(page.clampMbps(parseFloat(s2BitrateField.text)) * 1000))
    }

    function setSecondBitrateMbps(value, save) {
        page.syncingSecondBitrate = true
        let mbps = page.clampMbps(value)
        s2BitrateSlider.value = Math.min(50, mbps)
        s2BitrateField.text = page.formatMbps(mbps)
        page.syncingSecondBitrate = false
        if (save) page.saveSecondDisplaySettings()
    }

    function saveSecondDisplaySettings() {
        if (page.loadingSettings) return
        backend.saveSecondDisplaySettings(
            s2ResCombo.currentText,
            s2FpsCombo.currentText,
            page.secondBitrateKbpsText(),
            s2EncoderCombo.currentText,
            s2EncoderProfileCombo.currentText
        )
    }

    Component.onCompleted: {
        let gen = backend.loadGeneralSettings();
        page.enableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true;
        page.enableStylusFeatures = gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false;

        let s2 = backend.loadSecondDisplaySettings();
        if (s2) {
            let resIdx = s2ResCombo.find(s2["resolution"] || "1920x1080 (16:9)");
            s2ResCombo.currentIndex = resIdx !== -1 ? resIdx : 2;

            let fpsIdx = s2FpsCombo.find(s2["fps"] || "60");
            s2FpsCombo.currentIndex = fpsIdx !== -1 ? fpsIdx : 1;

            page.setSecondBitrateMbps(Number(s2["bitrate"] || "8000") / 1000, false);

            let encIdx = s2EncoderCombo.find(s2["encoder"] || "Software (CPU / x264enc)");
            s2EncoderCombo.currentIndex = encIdx !== -1 ? encIdx : 2;

            let profileIdx = s2EncoderProfileCombo.find(s2["encoder_profile"] || "Low Latency");
            s2EncoderProfileCombo.currentIndex = profileIdx !== -1 ? profileIdx : 0;
        }
        page.loadingSettings = false;
    }

    Connections {
        target: backend
        function onLogAppended(type, msg) {
            page.appendLog(type, msg)
        }
    }

    function appendLog(type, msg) {
        let prefix = "[" + type + "] "
        let lines = String(msg).split(/\r?\n/)
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].length > 0) logArea.text += prefix + lines[i] + "\n"
        }
        logScrollView.contentItem.contentY = Math.max(0, logArea.implicitHeight - logScrollView.height)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14

        // Top status and stream details card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 72 + page.streamInfoRows * page.streamInfoCardHeight
                + Math.max(0, page.streamInfoRows - 1) * page.streamInfoSpacing
            radius: theme.cardRadius
            color: theme.surface
            border.color: theme.border
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 12

                Text {
                    text: backend.countdown > 0 ? ("Streaming starting in " + backend.countdown + "...") : "Streaming Active"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: "#86efac"
                    Layout.fillWidth: true
                }

                Flow {
                    id: streamInfoGrid
                    Layout.fillWidth: true
                    Layout.preferredHeight: page.streamInfoRows * page.streamInfoCardHeight
                        + Math.max(0, page.streamInfoRows - 1) * page.streamInfoSpacing
                    spacing: page.streamInfoSpacing

                    Repeater {
                        model: page.streamInfoItems

                        Rectangle {
                            width: Math.max(0, (
                                Math.max(0, streamInfoGrid.width)
                                - page.streamInfoSpacing * (page.streamInfoVisibleColumns - 1)
                            ) / page.streamInfoVisibleColumns)
                            height: page.streamInfoCardHeight
                            radius: 8
                            color: theme.surfaceAlt
                            border.color: theme.border
                            border.width: 1

                            Text {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                text: modelData
                                color: theme.cardTextSecondary
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                fontSizeMode: Text.HorizontalFit
                                minimumPixelSize: 9
                                horizontalAlignment: Text.AlignHCenter
                            }
                        }
                    }
                }
            }
        }

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
                    textFormat: TextEdit.PlainText
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

            Button {
                text: "⏹ Stop Streaming"
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                scale: hovered ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                onClicked: {
                    logArea.text = ""
                    backend.stopStreaming()
                }
                background: Rectangle {
                    implicitWidth: page.actionButtonWidth
                    implicitHeight: page.actionButtonHeight
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

            Button {
                text: "Save Preset"
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                onClicked: {
                    presetNameField.text = ""
                    presetSaveError.text = ""
                    replacePresetCombo.currentIndex = 0
                    savePresetPopup.open()
                    presetNameField.forceActiveFocus()
                }
                background: Rectangle {
                    implicitWidth: page.actionButtonWidth
                    implicitHeight: page.actionButtonHeight
                    color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                    border.color: parent.hovered ? theme.borderHover : theme.border
                    radius: 8
                }
                contentItem: Text {
                    text: parent.text
                    color: parent.hovered ? theme.textPrimary : theme.cardTextPrimary
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            // Add / Remove Third Display button
            Button {
                id: displayActionButton
                text: backend.secondStreamActive ? "Remove Third Display" : "Add Another Display"
                visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                onClicked: {
                    if (backend.secondStreamActive) {
                        backend.stopSecondStream()
                    } else {
                        addDisplayPopup.open()
                    }
                }
                background: Rectangle {
                    implicitWidth: page.actionButtonWidth
                    implicitHeight: page.actionButtonHeight
                    color: backend.secondStreamActive
                        ? (parent.down ? "#5a1010" : (parent.hovered ? "#c42830" : "#a82028"))
                        : (parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface))
                    border.color: backend.secondStreamActive
                        ? "#c42830"
                        : (parent.hovered ? theme.borderHover : theme.border)
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Item {
                    implicitWidth: displayActionContent.implicitWidth
                    implicitHeight: page.actionButtonHeight

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
                            color: backend.secondStreamActive
                                ? theme.textPrimary
                                : (displayActionButton.hovered ? theme.textPrimary : theme.cardTextPrimary)
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
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
    }

    Popup {
        id: savePresetPopup
        modal: true
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        width: 410
        height: savePresetContent.implicitHeight + 48
        padding: 0
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#80000000" }

        ColumnLayout {
            id: savePresetContent
            anchors.fill: parent
            anchors.margins: 24
            spacing: 12

            Text {
                text: "Save as Preset"
                color: theme.cardTextPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            Text {
                Layout.fillWidth: true
                text: "Saves this stream, input options, and the active additional display."
                color: theme.cardTextMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            CustomTextField {
                id: presetNameField
                Layout.fillWidth: true
                placeholderText: "Preset name"
                maximumLength: 32
                onAccepted: savePresetButton.clicked()
            }
            Text {
                text: "Replace:"
                visible: backend.presets.length >= 4
                color: theme.cardTextSecondary
                font.pixelSize: 12
            }
            CustomComboBox {
                id: replacePresetCombo
                Layout.fillWidth: true
                visible: backend.presets.length >= 4
                model: backend.presets.map(function(item) { return item["name"] })
            }
            Text {
                id: presetSaveError
                Layout.fillWidth: true
                color: "#fca5a5"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: "Cancel"
                    onClicked: savePresetPopup.close()
                    background: Rectangle {
                        implicitWidth: 90
                        implicitHeight: 36
                        color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                        border.color: parent.hovered ? theme.borderHover : theme.border
                        border.width: 1
                        radius: theme.controlRadius
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }
                    }
                    contentItem: Text {
                        text: parent.text
                        color: parent.hovered ? theme.textPrimary : theme.cardTextPrimary
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                CustomButton {
                    id: savePresetButton
                    text: backend.presets.length >= 4 ? "Replace" : "Save"
                    onClicked: {
                        let replaceIndex = backend.presets.length >= 4
                            ? replacePresetCombo.currentIndex : -1
                        let result = backend.saveCurrentPreset(
                            presetNameField.text, replaceIndex
                        )
                        if (result.indexOf("duplicate:") === 0) {
                            page.duplicatePresetIndex = parseInt(result.split(":")[1])
                            duplicateConfirm.open()
                        } else if (result === "full") {
                            presetSaveError.text = "Choose a preset to replace."
                        } else {
                            presetSaveError.text = result
                            if (result.length === 0) savePresetPopup.close()
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: duplicateConfirm
        modal: true
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        width: 380
        height: 165
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            radius: theme.cardRadius
        }
        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            Text {
                Layout.fillWidth: true
                text: "A preset with this name already exists. Replace it?"
                color: theme.cardTextPrimary
                font.pixelSize: 14
                font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button { text: "Cancel"; onClicked: duplicateConfirm.close() }
                CustomButton {
                    text: "Replace"
                    onClicked: {
                        let result = backend.saveCurrentPreset(
                            presetNameField.text, page.duplicatePresetIndex
                        )
                        presetSaveError.text = result
                        duplicateConfirm.close()
                        if (result.length === 0) savePresetPopup.close()
                    }
                }
            }
        }
    }

    // Add Display Popup
    Popup {
        id: addDisplayPopup
        modal: true
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        width: Math.min(page.width - 40, 560)
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
                text: "Add Another Display"
                font.pixelSize: 18
                font.weight: Font.ExtraBold
                color: theme.cardTextPrimary
            }

            Text {
                text: backend.detectedDe === "kde"
                    ? "Creates Monitorize Display 2 in KDE.\nArrange it in System Settings → Display Configuration."
                    : backend.detectedDe === "gnome"
                    ? "Creates a second native GNOME virtual display.\nArrange it in Settings → Displays; GNOME may show matching monitor labels."
                    : "Your desktop will open a screen-share picker.\nChoose the display to stream on the third-display port."
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

                Text { text: "Bitrate (Mbps):"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                RowLayout {
                    spacing: 10

                    CustomSlider {
                        id: s2BitrateSlider
                        from: 0.25
                        to: 50
                        stepSize: 0.25
                        value: 8
                        snapMode: Slider.SnapAlways
                        Layout.preferredWidth: 180
                        onMoved: page.setSecondBitrateMbps(value, true)
                    }

                    CustomTextField {
                        id: s2BitrateField
                        text: "8"
                        maximumLength: 5
                        validator: DoubleValidator {
                            bottom: 0.25
                            top: 100
                            decimals: 2
                            notation: DoubleValidator.StandardNotation
                        }
                        onTextEdited: {
                            if (page.syncingSecondBitrate) return
                            let mbps = parseFloat(text)
                            if (!isNaN(mbps)) {
                                s2BitrateSlider.value = Math.min(50, page.clampMbps(mbps))
                                page.saveSecondDisplaySettings()
                            }
                        }
                        onEditingFinished: page.setSecondBitrateMbps(parseFloat(text), true)
                    }

                    Text {
                        text: "Mbps"
                        color: theme.cardTextMuted
                        font.pixelSize: 12
                    }
                }

                Text { text: "Encoder:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                ChoiceChips {
                    id: s2EncoderCombo
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text { text: "Encoder Profile:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                ChoiceChips {
                    id: s2EncoderProfileCombo
                    currentIndex: 0
                    model: ["Low Latency", "Balanced", "Quality"]
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
                        color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                        border.color: parent.hovered ? theme.borderHover : theme.border
                        border.width: 1
                        radius: theme.controlRadius
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }
                    }
                    contentItem: Text {
                        text: parent.text
                        color: parent.hovered ? theme.textPrimary : theme.cardTextPrimary
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                CustomButton {
                    text: backend.detectedDe === "kde" || backend.detectedDe === "gnome"
                        ? "▶  Create Virtual Display"
                        : "▶  Start Third Display"
                    implicitWidth: 170
                    implicitHeight: 36
                    onClicked: {
                        let cleanRes = s2ResCombo.currentText.split(" ")[0]
                        backend.startSecondStream(
                            cleanRes,
                            s2FpsCombo.currentText,
                            page.secondBitrateKbpsText(),
                            s2EncoderCombo.currentText,
                            s2EncoderProfileCombo.currentText
                        )
                        page.saveSecondDisplaySettings()
                        addDisplayPopup.close()
                    }
                }
            }
        }
    }
}
