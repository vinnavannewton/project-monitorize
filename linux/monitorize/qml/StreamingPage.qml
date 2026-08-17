import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window


Item {
    id: page

    property bool enableTouch: true
    property bool enableStylusFeatures: false
    property bool secondTouchEnabled: true
    property bool secondStylusEnabled: false
    property bool secondAudioEnabled: false
    property bool loadingSettings: true
    property int duplicatePresetIndex: -1
    property bool syncingSecondBitrate: false
    property bool secondAutoBitrate: true
    readonly property int optionChipWidth: 150
    readonly property int actionButtonWidth: 160
    readonly property int actionButtonHeight: 38
    readonly property int streamInfoColumns: 3
    readonly property int streamInfoCardHeight: 28
    readonly property int streamInfoSpacing: 10
    readonly property bool isSunshineMode: backend.streamingBackend === "Sunshine"
    readonly property var streamInfoBaseItems: isSunshineMode
        ? ["Host  " + backend.localIp, "Display 1  Port 47989"]
        : ["Second Display  Port 7110", "Host  " + backend.localIp]
    readonly property var streamInfoItems: backend.secondStreamActive
        ? (page.isSunshineMode
            ? page.streamInfoBaseItems.concat(["Display 2  " + backend.localIp + ":49089"])
            : page.streamInfoBaseItems.concat(["Third Display  Port 7114"]))
        : page.streamInfoBaseItems
    readonly property int streamInfoVisibleColumns: Math.max(
        1, Math.min(page.streamInfoColumns, page.streamInfoItems.length)
    )
    readonly property int streamInfoRows: Math.max(
        1, Math.ceil(page.streamInfoItems.length / page.streamInfoColumns)
    )
    readonly property var telemetry: backend.streamingTelemetry

    function telemetryNumber(value, decimals) {
        if (value === undefined || value === null || !isFinite(Number(value))) return "—"
        return Number(value).toFixed(decimals)
    }

    function telemetryText(value) {
        return value === undefined || value === null || value === "" ? "—" : String(value)
    }

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

    function secondResolutionValue() {
        return s2ResCombo.currentText === "Custom..."
            ? s2CustomW.text + "x" + s2CustomH.text
            : s2ResCombo.currentText.split(" ")[0]
    }

    function secondFpsValue() {
        return s2FpsCombo.currentText === "Custom..."
            ? s2CustomFps.text : s2FpsCombo.currentText
    }

    function secondRecommendedBitrateKbps() {
        let resolution = page.secondResolutionValue().split("x")
        return backend.recommendedWifiBitrateKbps(
            Number(resolution[0]), Number(resolution[1]), Number(page.secondFpsValue())
        )
    }

    function secondResolutionOrFpsChanged() {
        let resolution = page.secondResolutionValue().split("x")
        if (page.secondAutoBitrate && Number(resolution[0]) > 0 &&
                Number(resolution[1]) > 0 && Number(page.secondFpsValue()) > 0) {
            page.setSecondBitrateMbps(
                page.secondRecommendedBitrateKbps() / 1000, true, true
            )
        } else {
            page.saveSecondDisplaySettings()
        }
    }

    function setSecondBitrateMbps(value, save, autoSelected) {
        if (autoSelected !== undefined) page.secondAutoBitrate = autoSelected
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
            s2ResCombo.currentText === "Custom..." ? s2CustomW.text : "",
            s2ResCombo.currentText === "Custom..." ? s2CustomH.text : "",
            s2FpsCombo.currentText,
            s2FpsCombo.currentText === "Custom..." ? s2CustomFps.text : "",
            page.secondBitrateKbpsText(),
            s2EncoderCombo.currentText,
            s2EncoderProfileCombo.currentText,
            s2FecCombo.currentText,
            page.secondTouchEnabled,
            page.secondStylusEnabled,
            page.secondAudioEnabled,
            s2SunshineEncoderCombo ? s2SunshineEncoderCombo.currentText : "Auto",
            s2SunshineCodecCombo ? s2SunshineCodecCombo.currentText : "Auto",
            s2SunshineTouchStylusToggle ? s2SunshineTouchStylusToggle.checked : true
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
            if (s2["resolution"] === "Custom...") {
                s2CustomW.text = s2["custom_w"] || "1920";
                s2CustomH.text = s2["custom_h"] || "1080";
            }

            let fpsIdx = s2FpsCombo.find(s2["fps"] || "60");
            s2FpsCombo.currentIndex = fpsIdx !== -1 ? fpsIdx : 1;
            if (s2["fps"] === "Custom...") {
                s2CustomFps.text = s2["custom_fps"] || "60";
            }

            let savedMbps = Number(s2["bitrate"] || "8000") / 1000;
            page.setSecondBitrateMbps(
                savedMbps, false,
                Math.abs(savedMbps - page.secondRecommendedBitrateKbps() / 1000) < 0.001
            );

            let encIdx = s2EncoderCombo.find(s2["encoder"] || "Software (CPU / x264enc)");
            s2EncoderCombo.currentIndex = encIdx !== -1 ? encIdx : 2;

            let profileIdx = s2EncoderProfileCombo.find(s2["encoder_profile"] || "Low Latency");
            s2EncoderProfileCombo.currentIndex = profileIdx !== -1 ? profileIdx : 0;
            if (!s2FecCombo.selectValue(s2["fec_mode"] || "Off", true)) {
                s2FecCombo.selectValue("Off")
            }
            page.secondTouchEnabled = s2["enable_touch"] !== undefined ? s2["enable_touch"] : true;
            page.secondStylusEnabled = s2["enable_stylus_features"] !== undefined
                ? s2["enable_stylus_features"] : false;
            page.secondAudioEnabled = s2["enable_audio"] === true;

            if (s2SunshineEncoderCombo) {
                if (!s2SunshineEncoderCombo.selectValue(s2["sunshine_encoder"] || "Auto", true)) {
                    s2SunshineEncoderCombo.selectValue("Auto");
                }
            }
            if (s2SunshineCodecCombo) {
                if (!s2SunshineCodecCombo.selectValue(s2["sunshine_codec"] || "Auto", true)) {
                    s2SunshineCodecCombo.selectValue("Auto");
                }
            }
            if (s2SunshineTouchStylusToggle) {
                s2SunshineTouchStylusToggle.checked = s2["sunshine_native_pen_touch"] !== undefined ? s2["sunshine_native_pen_touch"] : true;
            }
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
                visible: !page.isSunshineMode
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

            Button {
                text: "🔑 Pair Moonlight PIN"
                visible: page.isSunshineMode
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                scale: hovered ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                onClicked: {
                    pairMoonlightPopup.open()
                }
                background: Rectangle {
                    implicitWidth: page.actionButtonWidth
                    implicitHeight: page.actionButtonHeight
                    color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                    border.color: theme.accent
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
                }
                contentItem: Text {
                    text: parent.text
                    color: theme.accent
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Button {
                text: "⚙ Sunshine Settings"
                visible: page.isSunshineMode
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                scale: hovered ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                onClicked: {
                    sunshineSettingsPopup.open()
                }
                background: Rectangle {
                    implicitWidth: page.actionButtonWidth
                    implicitHeight: page.actionButtonHeight
                    color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                    border.color: theme.border
                    radius: 8
                    Behavior on color { ColorAnimation { duration: 150 } }
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
                text: backend.secondStreamActive
                    ? (page.isSunshineMode ? "Remove Second Display" : "Remove Third Display")
                    : "Add Another Display"
                visible: (backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland")
                Layout.preferredWidth: page.actionButtonWidth
                Layout.preferredHeight: page.actionButtonHeight
                implicitWidth: page.actionButtonWidth
                implicitHeight: page.actionButtonHeight
                padding: 0
                onClicked: {
                    if (backend.secondStreamActive) {
                        backend.stopSecondStream()
                    } else {
                        addDisplayWindow.show()
                        addDisplayWindow.raise()
                        addDisplayWindow.requestActivate()
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
        id: pairMoonlightPopup
        modal: true
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        width: 380
        height: pairMoonlightContent.implicitHeight + 48
        padding: 0
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#80000000" }

        onOpened: {
            pairPinField.text = ""
            pairStatusText.text = ""
            pairPinField.forceActiveFocus()
        }

        ColumnLayout {
            id: pairMoonlightContent
            anchors.fill: parent
            anchors.margins: 24
            spacing: 12

            Text {
                text: "📱 Pair Moonlight Device"
                color: theme.cardTextPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            Text {
                Layout.fillWidth: true
                text: "Enter the 4-digit PIN displayed on your Moonlight client to pair this device."
                color: theme.cardTextMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            CustomTextField {
                id: pairPinField
                Layout.fillWidth: true
                placeholderText: "4-digit PIN (e.g. 1234)"
                maximumLength: 4
                font.pixelSize: 18
                font.weight: Font.Bold
                horizontalAlignment: TextInput.AlignHCenter
                inputMethodHints: Qt.ImhDigitsOnly
                validator: RegularExpressionValidator { regularExpression: /^[0-9]{4}$/ }
                onAccepted: submitPinButton.clicked()
            }
            Text {
                id: pairStatusText
                Layout.fillWidth: true
                text: ""
                font.pixelSize: 12
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                Button {
                    text: "Cancel"
                    onClicked: pairMoonlightPopup.close()
                    background: Rectangle {
                        implicitWidth: 90
                        implicitHeight: 36
                        color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                        border.color: parent.hovered ? theme.borderHover : theme.border
                        border.width: 1
                        radius: theme.controlRadius
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
                Button {
                    id: submitPinButton
                    text: "Pair Device"
                    enabled: pairPinField.text.length === 4
                    opacity: enabled ? 1.0 : 0.5
                    onClicked: {
                        let res = backend.pairMoonlightPin(pairPinField.text)
                        if (res && res.success) {
                            pairStatusText.color = theme.accent
                            pairStatusText.text = "✅ " + res.message
                            closeTimer.start()
                        } else {
                            pairStatusText.color = "#fca5a5"
                            pairStatusText.text = "❌ " + (res ? res.message : "Pairing failed.")
                        }
                    }
                    background: Rectangle {
                        implicitWidth: 110
                        implicitHeight: 36
                        color: parent.enabled ? (parent.down ? theme.accentPressed : (parent.hovered ? theme.accentHover : theme.accent)) : theme.surfaceAlt
                        radius: theme.controlRadius
                    }
                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#000000" : theme.cardTextMuted
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }

        Timer {
            id: closeTimer
            interval: 1500
            repeat: false
            onTriggered: pairMoonlightPopup.close()
        }
    }

    Popup {
        id: sunshineSettingsPopup
        modal: true
        focus: true
        x: Math.round((page.width - width) / 2)
        y: Math.round((page.height - height) / 2)
        width: 400
        height: sunshineSettingsContent.implicitHeight + 48
        padding: 0
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#80000000" }

        ColumnLayout {
            id: sunshineSettingsContent
            anchors.fill: parent
            anchors.margins: 24
            spacing: 16

            Text {
                text: "⚙ Sunshine Web Dashboard"
                color: theme.cardTextPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
            }

            Text {
                Layout.fillWidth: true
                text: "Select which virtual monitor's Sunshine dashboard to open in your browser:"
                color: theme.cardTextMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                // Monitor 1 option card
                Rectangle {
                    Layout.fillWidth: true
                    height: 60
                    radius: 8
                    color: mon1Mouse.containsMouse ? (mon1Mouse.pressed ? theme.surfaceAlt : theme.borderHover) : theme.surfaceAlt
                    border.color: mon1Mouse.containsMouse ? theme.accent : theme.border
                    border.width: 1
                    Behavior on color { ColorAnimation { duration: 120 } }
                    Behavior on border.color { ColorAnimation { duration: 120 } }

                    MouseArea {
                        id: mon1Mouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            sunshineSettingsPopup.close()
                            backend.openSunshineWebUi(1)
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        spacing: 12

                        Text {
                            text: "🖥️"
                            font.pixelSize: 22
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: "Monitor 1 (Primary Display)"
                                color: theme.cardTextPrimary
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                            Text {
                                text: "Port 47989 · Web UI https://localhost:47990"
                                color: theme.cardTextMuted
                                font.pixelSize: 11
                            }
                        }

                        Text {
                            text: "➜"
                            color: mon1Mouse.containsMouse ? theme.accent : theme.cardTextMuted
                            font.pixelSize: 16
                            font.weight: Font.Bold
                        }
                    }
                }

                // Monitor 2 option card
                Rectangle {
                    Layout.fillWidth: true
                    height: 60
                    radius: 8
                    color: mon2Mouse.containsMouse ? (mon2Mouse.pressed ? theme.surfaceAlt : theme.borderHover) : theme.surfaceAlt
                    border.color: mon2Mouse.containsMouse ? theme.accent : theme.border
                    border.width: 1
                    Behavior on color { ColorAnimation { duration: 120 } }
                    Behavior on border.color { ColorAnimation { duration: 120 } }

                    MouseArea {
                        id: mon2Mouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            sunshineSettingsPopup.close()
                            backend.openSunshineWebUi(2)
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        spacing: 12

                        Text {
                            text: "🖥️"
                            font.pixelSize: 22
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: "Monitor 2 (Secondary Display)"
                                color: theme.cardTextPrimary
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                            Text {
                                text: "Port 49089 · Web UI https://localhost:49090"
                                color: theme.cardTextMuted
                                font.pixelSize: 11
                            }
                        }

                        Text {
                            text: "➜"
                            color: mon2Mouse.containsMouse ? theme.accent : theme.cardTextMuted
                            font.pixelSize: 16
                            font.weight: Font.Bold
                        }
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                Layout.topMargin: 4
                spacing: 10

                Button {
                    text: "Close"
                    onClicked: sunshineSettingsPopup.close()
                    background: Rectangle {
                        implicitWidth: 80
                        implicitHeight: 34
                        color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                        border.color: parent.hovered ? theme.borderHover : theme.border
                        border.width: 1
                        radius: theme.controlRadius
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

    // Native add-display dialog: independent from the QQuickWidget surface.
    Window {
        id: addDisplayWindow
        title: "Add Another Display"
        visible: false
        width: 720
        height: 640
        minimumWidth: 520
        minimumHeight: 420
        flags: Qt.Dialog
        modality: Qt.ApplicationModal

        Theme {
            id: theme
        }

        Rectangle {
            anchors.fill: parent
            color: theme.surface

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 24
                spacing: 14

                Text {
                    text: page.isSunshineMode ? "Add Another Display (Sunshine)" : "Add Another Display"
                    font.pixelSize: 18
                    font.weight: Font.ExtraBold
                    color: theme.cardTextPrimary
                }

                Text {
                    text: page.isSunshineMode
                        ? "Creates Monitorize Display 2 and starts Sunshine Instance 2 on port 49089.\nOn your second device, click '+ Add PC' in Moonlight and enter " + backend.localIp + ":49089."
                        : (backend.detectedDe === "kde"
                            ? "Creates Monitorize Display 2 in KDE.\nArrange it in System Settings → Display Configuration."
                            : backend.detectedDe === "gnome"
                            ? "Creates a second native GNOME virtual display.\nArrange it in Settings → Displays; GNOME may show matching monitor labels."
                            : "Creates a second Hyprland HEADLESS display.\nWhen the portal opens, select that new display.")
                    font.pixelSize: 12
                    color: theme.cardTextMuted
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border }

                ScrollView {
                    id: addDisplayScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    contentHeight: settingsGrid.implicitHeight
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical.policy: ScrollBar.AsNeeded

                    // Settings grid
                    GridLayout {
                        id: settingsGrid
                        width: addDisplayScroll.availableWidth
                        columns: 2
                        columnSpacing: 16
                        rowSpacing: 10

                Text { text: "Resolution:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2ResCombo
                    Layout.preferredWidth: page.optionChipWidth
                    model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "Custom..."]
                    currentIndex: 2
                    onActivated: page.secondResolutionOrFpsChanged()
                }

                Text {
                    text: ""
                    visible: s2ResCombo.currentText === "Custom..."
                }
                RowLayout {
                    spacing: 8
                    visible: s2ResCombo.currentText === "Custom..."

                    CustomTextField {
                        id: s2CustomW
                        text: "1920"
                        placeholderText: "Width"
                        maximumLength: 4
                        validator: IntValidator { bottom: 320; top: 7680 }
                        Layout.preferredWidth: 92
                        onTextEdited: page.secondResolutionOrFpsChanged()
                    }
                    Text { text: "×"; color: theme.cardTextSecondary; font.pixelSize: 18 }
                    CustomTextField {
                        id: s2CustomH
                        text: "1080"
                        placeholderText: "Height"
                        maximumLength: 4
                        validator: IntValidator { bottom: 240; top: 4320 }
                        Layout.preferredWidth: 92
                        onTextEdited: page.secondResolutionOrFpsChanged()
                    }
                    Text {
                        text: "320–7680 × 240–4320"
                        color: theme.cardTextMuted
                        font.pixelSize: 10
                    }
                }

                Text { text: "FPS:"; color: theme.cardTextSecondary; font.pixelSize: 13 }
                CustomComboBox {
                    id: s2FpsCombo
                    Layout.preferredWidth: page.optionChipWidth
                    model: ["30", "60", "90", "120", "Custom..."]
                    currentIndex: 1
                    onActivated: page.secondResolutionOrFpsChanged()
                }

                Text {
                    text: ""
                    visible: s2FpsCombo.currentText === "Custom..."
                }
                RowLayout {
                    spacing: 8
                    visible: s2FpsCombo.currentText === "Custom..."

                    CustomTextField {
                        id: s2CustomFps
                        text: "60"
                        placeholderText: "FPS"
                        maximumLength: 3
                        validator: IntValidator { bottom: 24; top: 240 }
                        Layout.preferredWidth: 92
                        onTextEdited: page.secondResolutionOrFpsChanged()
                    }
                    Text {
                        text: "24–240"
                        color: theme.cardTextMuted
                        font.pixelSize: 10
                    }
                }

                Text {
                    text: "Bitrate (Mbps):"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode
                }
                RowLayout {
                    spacing: 8
                    visible: !page.isSunshineMode

                    CustomSlider {
                        id: s2BitrateSlider
                        from: 0.25
                        to: 50
                        stepSize: 0.25
                        value: 8
                        snapMode: Slider.SnapAlways
                        Layout.preferredWidth: page.optionChipWidth * 1.5
                        onMoved: page.setSecondBitrateMbps(value, true, false)
                    }

                    CustomTextField {
                        id: s2BitrateField
                        text: "8"
                        maximumLength: 5
                        Layout.preferredWidth: page.optionChipWidth * 0.5
                        validator: DoubleValidator {
                            bottom: 0.25
                            top: 100
                            decimals: 2
                            notation: DoubleValidator.StandardNotation
                        }
                        onTextEdited: {
                            if (page.syncingSecondBitrate) return
                            page.secondAutoBitrate = false
                            let mbps = parseFloat(text)
                            if (!isNaN(mbps)) {
                                s2BitrateSlider.value = Math.min(50, page.clampMbps(mbps))
                                page.saveSecondDisplaySettings()
                            }
                        }
                        onEditingFinished: page.setSecondBitrateMbps(parseFloat(text), true, false)
                    }

                    CustomButton {
                        text: "Use auto"
                        primary: page.secondAutoBitrate
                        implicitWidth: page.optionChipWidth
                        implicitHeight: 30
                        Layout.preferredWidth: page.optionChipWidth
                        onClicked: page.setSecondBitrateMbps(
                            page.secondRecommendedBitrateKbps() / 1000, true, true
                        )
                    }
                }

                Text {
                    text: "Packet-loss recovery:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode && backend.isWifiStreaming
                }
                ChoiceChips {
                    id: s2FecCombo
                    visible: !page.isSunshineMode && backend.isWifiStreaming
                    chipWidth: page.optionChipWidth
                    model: ["Off", "RS-FEC 10%"]
                    currentIndex: 0
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text {
                    text: "Video Codec:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: page.isSunshineMode
                }
                ChoiceChips {
                    id: s2SunshineCodecCombo
                    visible: page.isSunshineMode
                    chipWidth: 118
                    currentIndex: 0
                    model: [
                        "Auto",
                        "H.264 (AVC)",
                        "H.265 (HEVC)",
                        "AV1"
                    ]
                    onActivated: {
                        if (page.isSunshineMode) {
                            backend.setSunshineCodec(currentText, 2)
                        }
                        page.saveSecondDisplaySettings()
                    }
                }

                Text {
                    text: "Encoder:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: page.isSunshineMode
                }
                ChoiceChips {
                    id: s2SunshineEncoderCombo
                    visible: page.isSunshineMode
                    chipWidth: 118
                    currentIndex: 0
                    model: [
                        "Auto",
                        "NVIDIA",
                        "VA-API",
                        "Software Enc"
                    ]
                    onActivated: {
                        if (page.isSunshineMode) {
                            backend.setSunshineEncoder(currentText, 2)
                        }
                        page.saveSecondDisplaySettings()
                    }
                }

                Text {
                    text: "Input:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: page.isSunshineMode
                }
                CustomToggle {
                    id: s2SunshineTouchStylusToggle
                    visible: page.isSunshineMode
                    text: "Touch and Stylus Input"
                    Layout.alignment: Qt.AlignLeft
                    checked: true
                    onToggled: {
                        if (page.isSunshineMode) {
                            backend.setSunshineNativePenTouch(checked, 2)
                        }
                        page.saveSecondDisplaySettings()
                    }
                }

                Text {
                    text: "Encoder:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode
                }
                ChoiceChips {
                    id: s2EncoderCombo
                    visible: !page.isSunshineMode
                    chipWidth: page.optionChipWidth
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text {
                    text: "Encoder Profile:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode
                }
                ChoiceChips {
                    id: s2EncoderProfileCombo
                    visible: !page.isSunshineMode
                    chipWidth: page.optionChipWidth
                    currentIndex: 0
                    model: ["Low Latency", "Balanced", "Quality"]
                    onActivated: page.saveSecondDisplaySettings()
                }

                Text {
                    text: "Touch:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode
                }
                CustomToggle {
                    id: s2TouchToggle
                    visible: !page.isSunshineMode
                    text: "Enable touch for this display"
                    Layout.alignment: Qt.AlignLeft
                    checked: page.secondTouchEnabled
                    onToggled: {
                        page.secondTouchEnabled = checked
                        page.saveSecondDisplaySettings()
                    }
                }

                Text {
                    text: "Stylus:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: !page.isSunshineMode
                }
                CustomToggle {
                    id: s2StylusToggle
                    visible: !page.isSunshineMode
                    text: "Enable stylus features for this display"
                    Layout.alignment: Qt.AlignLeft
                    checked: page.secondStylusEnabled
                    onToggled: {
                        page.secondStylusEnabled = checked
                        page.saveSecondDisplaySettings()
                    }
                }

                Text {
                    text: "Audio:"
                    color: theme.cardTextSecondary
                    font.pixelSize: 13
                    visible: backend.isWifiStreaming
                }
                CustomToggle {
                    id: s2AudioToggle
                    text: "Enable Audio"
                    visible: backend.isWifiStreaming
                    Layout.alignment: Qt.AlignLeft
                    checked: page.secondAudioEnabled
                    onToggled: {
                        page.secondAudioEnabled = checked
                        if (page.isSunshineMode) {
                            backend.saveSunshineConfig({"stream_audio": checked ? "enabled" : "disabled"}, 2)
                        }
                        page.saveSecondDisplaySettings()
                    }
                }

                Text { text: ""; visible: page.isSunshineMode }
                Rectangle {
                    visible: page.isSunshineMode
                    Layout.fillWidth: true
                    Layout.preferredWidth: page.optionChipWidth * 2.2
                    color: theme.surfaceCard || "#1E293B"
                    radius: 8
                    border.color: theme.accent
                    border.width: 1
                    implicitHeight: sunshineCol2.implicitHeight + 20

                    ColumnLayout {
                        id: sunshineCol2
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 4

                        Text {
                            text: "✨ Sunshine Instance 2 (Port 49089)"
                            color: theme.accent
                            font.pixelSize: 13
                            font.weight: Font.Bold
                        }
                        Text {
                            text: "Monitorize will create the second virtual display and launch Sunshine Instance 2. In Moonlight on your second device, connect to " + backend.localIp + ":49089."
                            color: theme.textSecondary
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }

                    }
                }

            Item { Layout.preferredHeight: 6 }

            // Action buttons
            RowLayout {
                spacing: 12
                Layout.alignment: Qt.AlignRight

                Button {
                    text: "Cancel"
                    onClicked: addDisplayWindow.hide()
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
                        : "▶  Create Headless Display"
                    implicitWidth: 170
                    implicitHeight: 36
                    onClicked: {
                        if (page.isSunshineMode) {
                            if (s2SunshineEncoderCombo) backend.setSunshineEncoder(s2SunshineEncoderCombo.currentText, 2)
                            if (s2SunshineCodecCombo) backend.setSunshineCodec(s2SunshineCodecCombo.currentText, 2)
                            if (s2SunshineTouchStylusToggle) backend.setSunshineNativePenTouch(s2SunshineTouchStylusToggle.checked, 2)
                        }
                        backend.startSecondStream(
                            page.secondResolutionValue(),
                            page.secondFpsValue(),
                            page.secondBitrateKbpsText(),
                            s2EncoderCombo.currentText,
                            s2EncoderProfileCombo.currentText,
                            backend.isWifiStreaming ? s2FecCombo.currentText : "Off",
                            page.secondTouchEnabled,
                            page.secondStylusEnabled,
                            backend.isWifiStreaming && page.secondAudioEnabled,
                            s2SunshineEncoderCombo ? s2SunshineEncoderCombo.currentText : "Auto",
                            s2SunshineCodecCombo ? s2SunshineCodecCombo.currentText : "Auto",
                            s2SunshineTouchStylusToggle ? s2SunshineTouchStylusToggle.checked : true
                        )
                        page.saveSecondDisplaySettings()
                        addDisplayWindow.hide()
                    }
                }
            }
        }
    }
}

    Rectangle {
        visible: page.telemetry.available === true
        z: 10
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 18
        anchors.bottomMargin: 18
        width: 360
        height: 148
        radius: 6
        color: "#cc000000"
        border.color: "#4b5563"
        border.width: 1

        Text {
            id: telemetryLabel
            anchors.fill: parent
            anchors.margins: 8
            color: "#ffffff"
            font.family: "Fira Code, JetBrains Mono, DejaVu Sans Mono, Consolas, monospace"
            font.pixelSize: 10
            lineHeightMode: Text.FixedHeight
            lineHeight: 12
            text: "Wi-Fi RTP/UDP\n" +
                "host capture/pace/enc " + page.telemetryNumber(page.telemetry.hostCaptureFps, 1) +
                " / " + page.telemetryNumber(page.telemetry.hostPacedFps, 1) +
                " / " + page.telemetryNumber(page.telemetry.hostEncodedFps, 1) + " fps · encode path " +
                page.telemetryText(page.telemetry.encodePath) + "\n" +
                "TX " + page.telemetryNumber(page.telemetry.hostTxKbps, 0) + " kbps · " +
                page.telemetryNumber(page.telemetry.hostRtpPps, 0) + " pps · ceiling " +
                page.telemetryNumber(page.telemetry.pacingKbps, 0) + " kbps\n" +
                "sender q/delay/drop/err " +
                page.telemetryNumber(page.telemetry.senderQueue, 0) + "/" +
                page.telemetryNumber(page.telemetry.senderDelayMs, 1) + "ms/" +
                page.telemetryNumber(page.telemetry.senderDrops, 0) + "/" +
                page.telemetryNumber(page.telemetry.senderErrors, 0) + "\n" +
                "RX " + page.telemetryNumber(page.telemetry.clientRxKbps, 0) + " kbps · " +
                page.telemetryNumber(page.telemetry.clientPps, 0) + " pps · loss " +
                page.telemetryNumber(page.telemetry.clientLossPercent, 1) + "%\n" +
                "render " + page.telemetryNumber(page.telemetry.clientRenderFps, 1) + " fps · q " +
                page.telemetryNumber(page.telemetry.clientQueue, 0) + " · incomplete/drop " +
                page.telemetryNumber(page.telemetry.clientIncomplete, 0) + "/" +
                page.telemetryNumber(page.telemetry.clientDropped, 0) + "\n" +
                "decode/display " +
                page.telemetryNumber(page.telemetry.clientDecodeMs, 1) + "/" +
                page.telemetryNumber(page.telemetry.clientDisplayMs, 1) + " ms · IDR scheduled/recovery " +
                page.telemetryNumber(page.telemetry.scheduledIdr, 0) + "/" +
                page.telemetryNumber(page.telemetry.recoveryIdr, 0) + "\n" +
                "assembly p95/late " +
                page.telemetryNumber(page.telemetry.clientAssemblyP95Ms, 1) + " ms/" +
                page.telemetryNumber(page.telemetry.clientLateFrames, 0) + " · IDR confirmed/coalesced " +
                page.telemetryNumber(page.telemetry.confirmedIdr, 0) + "/" +
                page.telemetryNumber(page.telemetry.coalescedIdr, 0) + " · " +
                page.telemetryNumber(page.telemetry.idrKiB, 1) + " KiB\n" +
                "budget/video " + page.telemetryNumber(page.telemetry.bitrateKbps, 0) + "/" +
                page.telemetryNumber(page.telemetry.videoBitrateKbps, 0) + " kbps · FEC " +
                page.telemetryNumber(page.telemetry.effectiveFecPercent, 0) + "% · " +
                page.telemetryNumber(page.telemetry.hostFecPps, 0) + " pps\n" +
                "FEC packets/recovered/unrecoverable " +
                page.telemetryNumber(page.telemetry.clientFecPackets, 0) + "/" +
                page.telemetryNumber(page.telemetry.clientFecRecovered, 0) + "/" +
                page.telemetryNumber(page.telemetry.clientFecUnrecoverable, 0) +
                " · residual " + page.telemetryNumber(page.telemetry.clientResidualLost, 0)
        }
    }
}
