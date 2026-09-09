import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string returnPageSource: "DisplaySetupPage.qml"
    property bool loading: true
    property bool advancedExpanded: false
    property var gpuOptions: []
    readonly property bool streamingCustomized: streamingMode.currentIndex === 1

    function resolutionValue() {
        return resCombo.currentText === "Custom..."
            ? customW.text + "x" + customH.text
            : resCombo.currentText.split(" ")[0]
    }

    function fpsValue() {
        return fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText
    }

    function encoderDisplayValue(value) {
        return String(value || "").toLowerCase().indexOf("software") === 0
            ? "Software"
            : (value || "Auto")
    }

    function codecDisplayValue(value) {
        let normalized = String(value || "").toLowerCase()
        if (normalized.indexOf("h.264") !== -1 || normalized === "h264" || normalized.indexOf("avc") !== -1) return "H.264"
        if (normalized.indexOf("h.265") !== -1 || normalized === "h265" || normalized.indexOf("hevc") !== -1) return "HEVC"
        if (normalized.indexOf("av1") !== -1) return "AV1"
        return "Auto"
    }

    function selectedGpuId() {
        if (gpuCombo.currentIndex < 0 || gpuCombo.currentIndex >= gpuOptions.length) return ""
        return gpuOptions[gpuCombo.currentIndex]["id"] || ""
    }

    function refreshGpuOptions(savedId) {
        gpuOptions = backend.getEncodingGpuOptions(encoder.currentText)
        let labels = []
        let selected = 0
        for (let i = 0; i < gpuOptions.length; i++) {
            labels.push(gpuOptions[i]["label"])
            if (savedId && gpuOptions[i]["id"] === savedId) selected = i
        }
        gpuCombo.model = labels
        gpuCombo.currentIndex = labels.length > 0 ? selected : -1
    }

    function saveSettings() {
        if (loading) return
        backend.saveDisplaySettings(
            resCombo.currentText,
            resCombo.currentText === "Custom..." ? customW.text : "",
            resCombo.currentText === "Custom..." ? customH.text : "",
            fpsCombo.currentText,
            fpsCombo.currentText === "Custom..." ? customFps.text : "",
            displayType.currentText,
            encoder.currentText,
            page.selectedGpuId(),
            codec.currentText,
            page.streamingCustomized,
            nativeInput.checked,
            audio.checked
        )
    }

    function selectAutomaticStreaming() {
        backend.setSunshineEncoder("Auto")
        backend.setSunshineCodec("Auto")
        page.saveSettings()
    }

    Component.onCompleted: {
        let saved = backend.loadDisplaySettings()
        resCombo.selectValue(saved["resolution"] || "1920x1080")
        customW.text = saved["custom_w"] || "1920"
        customH.text = saved["custom_h"] || "1080"
        fpsCombo.selectValue(saved["fps"] || "60")
        customFps.text = saved["custom_fps"] || "60"
        displayType.selectValue(saved["display_type"] || "Extend")
        encoder.selectValue(page.encoderDisplayValue(saved["sunshine_encoder"]))
        page.refreshGpuOptions(saved["sunshine_gpu"] || "")
        codec.selectValue(page.codecDisplayValue(saved["sunshine_codec"]))
        streamingMode.selectValue(
            saved["streaming_customized"] === true
                ? "Customize ›"
                : "Automatic (Recommended)"
        )
        nativeInput.checked = saved["sunshine_native_pen_touch"] !== false
        audio.checked = saved["enable_audio"] === true
        createOnly.checked = backend.streamingBackend === "none"
        loading = false
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 18

            Text {
                text: "Create a Display"
                color: theme.textPrimary
                font.pixelSize: 26
                font.weight: Font.Bold
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: createOnly.checked
                    ? "Create a virtual display for the streaming tool of your choice."
                    : "Monitorize creates the display and supervises Sunshine. Connect with Moonlight."
                color: theme.textSecondary
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                Layout.leftMargin: 30
                Layout.rightMargin: 30
            }

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(680, page.width - 40)
                implicitHeight: setupContent.implicitHeight + 40
                radius: theme.cardRadius
                color: theme.surface
                border.color: theme.border

                ColumnLayout {
                    id: setupContent
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 16

                    Text {
                        text: "Display"
                        color: theme.cardTextPrimary
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 18
                        rowSpacing: 14

                        Text { text: "Mode"; color: theme.textSecondary }
                        ChoiceChips {
                            id: displayType
                            model: ["Extend", "Mirror"]
                            chipWidth: 124
                            disabledValues: createOnly.checked || !backend.sunshineAvailable ? ["Mirror"] : []
                            onActivated: page.saveSettings()
                        }

                        Text { text: "Resolution"; color: theme.textSecondary }
                        CustomComboBox {
                            id: resCombo
                            Layout.preferredWidth: 300
                            model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "3840x2160 (16:9)", "Custom..."]
                            onActivated: page.saveSettings()
                        }

                        Item { visible: resCombo.currentText === "Custom..." }
                        RowLayout {
                            visible: resCombo.currentText === "Custom..."
                            CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                            Text { text: "×"; color: theme.textSecondary }
                            CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                        }

                        Text { text: "Refresh rate"; color: theme.textSecondary }
                        CustomComboBox {
                            id: fpsCombo
                            Layout.preferredWidth: 300
                            model: ["30", "60", "90", "120", "Custom..."]
                            onActivated: page.saveSettings()
                        }

                        Item { visible: fpsCombo.currentText === "Custom..." }
                        CustomTextField {
                            id: customFps
                            visible: fpsCombo.currentText === "Custom..."
                            placeholderText: "24–240"
                            maximumLength: 3
                            onEditingFinished: page.saveSettings()
                        }
                    }

                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border }

                    Text {
                        text: "Streaming"
                        color: theme.cardTextPrimary
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }

                    ChoiceChips {
                        id: streamingMode
                        model: ["Automatic (Recommended)", "Customize ›"]
                        chipWidth: 210
                        enabled: !createOnly.checked && backend.sunshineAvailable
                        opacity: enabled ? 1.0 : 0.45
                        onActivated: {
                            if (currentIndex === 0) page.selectAutomaticStreaming()
                            else page.saveSettings()
                        }
                    }

                    Rectangle {
                        visible: page.streamingCustomized && !createOnly.checked
                        Layout.fillWidth: true
                        implicitHeight: customStreaming.implicitHeight + 28
                        radius: theme.controlRadius
                        color: theme.background
                        border.color: theme.border

                        GridLayout {
                            id: customStreaming
                            anchors.fill: parent
                            anchors.margins: 14
                            columns: 2
                            columnSpacing: 18
                            rowSpacing: 14

                            Text { text: "Encoder"; color: theme.textSecondary }
                            ChoiceChips {
                                id: encoder
                                model: ["Auto", "NVIDIA", "VA-API", "Vulkan", "Software"]
                                chipWidth: 104
                                onActivated: {
                                    backend.setSunshineEncoder(currentText)
                                    page.refreshGpuOptions("")
                                    page.saveSettings()
                                }
                            }

                            Text { text: "Codec"; color: theme.textSecondary }
                            ChoiceChips {
                                id: codec
                                model: ["Auto", "H.264", "HEVC", "AV1"]
                                chipWidth: 104
                                onActivated: {
                                    backend.setSunshineCodec(currentText)
                                    page.saveSettings()
                                }
                            }

                            Text { text: "Encoding GPU"; color: theme.textSecondary; visible: gpuOptions.length > 0 }
                            CustomComboBox {
                                id: gpuCombo
                                Layout.preferredWidth: 300
                                visible: gpuOptions.length > 0
                                onActivated: page.saveSettings()
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border }

                    Text {
                        text: "Extras"
                        color: theme.cardTextPrimary
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 28
                        enabled: !createOnly.checked
                        opacity: enabled ? 1.0 : 0.45

                        CustomToggle {
                            id: nativeInput
                            text: "Touch input"
                            onCheckedChanged: {
                                if (!page.loading) backend.setSunshineNativePenTouch(checked)
                                page.saveSettings()
                            }
                        }
                        CustomToggle {
                            id: audio
                            text: "Audio"
                            onCheckedChanged: {
                                if (!page.loading) backend.saveSunshineConfig({"stream_audio": checked ? "enabled" : "disabled"})
                                page.saveSettings()
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border }

                    Button {
                        id: advancedButton
                        Layout.fillWidth: true
                        implicitHeight: 34
                        hoverEnabled: true
                        text: page.advancedExpanded ? "▾  Advanced settings" : "›  Advanced settings"
                        onClicked: page.advancedExpanded = !page.advancedExpanded
                        background: Rectangle {
                            color: advancedButton.hovered ? theme.surfaceAlt : "transparent"
                            radius: theme.controlRadius
                        }
                        contentItem: Text {
                            text: advancedButton.text
                            color: theme.textSecondary
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    RowLayout {
                        visible: page.advancedExpanded
                        Layout.fillWidth: true
                        spacing: 8

                        CustomCheckBox {
                            id: createOnly
                            text: "Create virtual display only"
                            enabled: backend.sunshineAvailable
                            Layout.fillWidth: true
                            onCheckedChanged: {
                                if (page.loading) return
                                if (checked && displayType.currentText === "Mirror") {
                                    displayType.selectValue("Extend")
                                }
                                backend.setStreamingBackend(checked ? "none" : "sunshine")
                                page.saveSettings()
                            }
                        }

                        Button {
                            id: virtualOnlyHelpButton
                            text: "?"
                            implicitWidth: 26
                            implicitHeight: 26
                            onClicked: virtualOnlyHint.open()
                            background: Rectangle {
                                radius: width / 2
                                color: virtualOnlyHelpButton.hovered ? theme.surfaceAlt : theme.background
                                border.color: virtualOnlyHelpButton.hovered ? theme.borderHover : theme.border
                            }
                            contentItem: Text {
                                text: virtualOnlyHelpButton.text
                                color: theme.textSecondary
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }
            }

            CustomButton {
                text: "Launch"
                primary: true
                implicitWidth: 240
                implicitHeight: 44
                Layout.alignment: Qt.AlignHCenter
                onClicked: {
                    page.saveSettings()
                    backend.startStreaming(
                        page.resolutionValue(), page.fpsValue(), displayType.currentText,
                        page.streamingCustomized ? encoder.currentText : "Auto",
                        page.streamingCustomized ? page.selectedGpuId() : "",
                        page.streamingCustomized ? codec.currentText : "Auto",
                        nativeInput.checked, audio.checked
                    )
                }
            }
        }
    }

    Popup {
        id: virtualOnlyHint
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(390, parent.width - 40)
        padding: 18
        modal: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: theme.surface
            border.color: theme.borderHover
            radius: theme.cardRadius
        }
        contentItem: Text {
            text: "Creates the virtual display without starting Monitorize’s streaming backend. Use your preferred streamer instead."
            color: theme.textSecondary
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
    }
}
