import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string returnPageSource: "DisplaySetupPage.qml"
    property bool loading: true
    property var gpuOptions: []

    function resolutionValue() {
        return resCombo.currentText === "Custom..."
            ? customW.text + "x" + customH.text
            : resCombo.currentText.split(" ")[0]
    }

    function fpsValue() {
        return fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText
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
            nativeInput.checked,
            audio.checked
        )
    }

    Component.onCompleted: {
        let saved = backend.loadDisplaySettings()
        resCombo.selectValue(saved["resolution"] || "1920x1080")
        customW.text = saved["custom_w"] || "1920"
        customH.text = saved["custom_h"] || "1080"
        fpsCombo.selectValue(saved["fps"] || "60")
        customFps.text = saved["custom_fps"] || "60"
        displayType.selectValue(saved["display_type"] || "Extend")
        encoder.selectValue(saved["sunshine_encoder"] || "Auto")
        page.refreshGpuOptions(saved["sunshine_gpu"] || "")
        codec.selectValue(saved["sunshine_codec"] || "Auto")
        nativeInput.checked = saved["sunshine_native_pen_touch"] !== false
        audio.checked = saved["enable_audio"] === true
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
                text: backendChips.currentText === "None"
                    ? "Create a virtual display managed by Monitorize. No streaming server is attached."
                    : "Monitorize creates the display and supervises its bundled Sunshine instance. Connect with Moonlight."
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
                Layout.preferredWidth: Math.min(620, page.width - 40)
                implicitHeight: form.implicitHeight + 40
                radius: theme.cardRadius
                color: theme.surface
                border.color: theme.border

                GridLayout {
                    id: form
                    anchors.fill: parent
                    anchors.margins: 20
                    columns: 2
                    columnSpacing: 18
                    rowSpacing: 14

                    Text { text: "Resolution"; color: theme.textSecondary }
                    CustomComboBox {
                        id: resCombo
                        Layout.preferredWidth: 260
                        model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "3840x2160 (16:9)", "Custom..."]
                        onActivated: page.saveSettings()
                    }

                    Text { text: ""; visible: resCombo.currentText === "Custom..." }
                    RowLayout {
                        visible: resCombo.currentText === "Custom..."
                        CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                        Text { text: "×"; color: theme.textSecondary }
                        CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                    }

                    Text { text: "Refresh rate"; color: theme.textSecondary }
                    CustomComboBox {
                        id: fpsCombo
                        Layout.preferredWidth: 260
                        model: ["30", "60", "90", "120", "Custom..."]
                        onActivated: page.saveSettings()
                    }

                    Text { text: ""; visible: fpsCombo.currentText === "Custom..." }
                    CustomTextField {
                        id: customFps
                        visible: fpsCombo.currentText === "Custom..."
                        placeholderText: "24–240"
                        maximumLength: 3
                        onEditingFinished: page.saveSettings()
                    }

                    Text { text: "Streaming backend"; color: theme.textSecondary; visible: backend.sunshineAvailable }
                    ChoiceChips {
                        id: backendChips
                        visible: backend.sunshineAvailable
                        model: ["None", "Sunshine"]
                        chipWidth: 124
                        onActivated: {
                            backend.setStreamingBackend(currentText.toLowerCase())
                            if (currentText === "None" && displayType.currentText === "Mirror") {
                                displayType.selectValue("Extend")
                            }
                            page.saveSettings()
                        }
                        Component.onCompleted: selectValue(backend.streamingBackend === "none" ? "None" : "Sunshine")
                    }

                    Text { text: "Display type"; color: theme.textSecondary }
                    ChoiceChips {
                        id: displayType
                        model: ["Extend", "Mirror"]
                        chipWidth: 124
                        disabledValues: backendChips.currentText === "None" ? ["Mirror"] : []
                        onActivated: page.saveSettings()
                    }

                    Text { text: "Sunshine encoder"; color: theme.textSecondary; visible: backendChips.currentText === "Sunshine" }
                    ChoiceChips {
                        id: encoder
                        visible: backendChips.currentText === "Sunshine"
                        model: ["Auto", "NVIDIA", "VA-API", "Software Enc"]
                        chipWidth: 112
                        onActivated: {
                            backend.setSunshineEncoder(currentText)
                            page.refreshGpuOptions("")
                            page.saveSettings()
                        }
                    }

                    Text {
                        text: "Encoding GPU"
                        color: theme.textSecondary
                        visible: gpuOptions.length > 0 && backendChips.currentText === "Sunshine"
                    }
                    CustomComboBox {
                        id: gpuCombo
                        Layout.preferredWidth: 260
                        visible: gpuOptions.length > 0 && backendChips.currentText === "Sunshine"
                        onActivated: page.saveSettings()
                    }

                    Text { text: "Video codec"; color: theme.textSecondary; visible: backendChips.currentText === "Sunshine" }
                    ChoiceChips {
                        id: codec
                        visible: backendChips.currentText === "Sunshine"
                        model: ["Auto", "H.264 (AVC)", "H.265 (HEVC)", "AV1"]
                        chipWidth: 112
                        onActivated: {
                            backend.setSunshineCodec(currentText)
                            page.saveSettings()
                        }
                    }

                    Text { text: ""; visible: backendChips.currentText === "Sunshine" }
                    ColumnLayout {
                        visible: backendChips.currentText === "Sunshine"
                        CustomToggle {
                            id: nativeInput
                            text: "Moonlight touch and stylus input"
                            onCheckedChanged: {
                                if (!page.loading) backend.setSunshineNativePenTouch(checked)
                                page.saveSettings()
                            }
                        }
                        CustomToggle {
                            id: audio
                            text: "Stream audio"
                            onCheckedChanged: {
                                if (!page.loading) backend.saveSunshineConfig({"stream_audio": checked ? "enabled" : "disabled"})
                                page.saveSettings()
                            }
                        }
                    }
                }
            }

            CustomButton {
                text: displayType.currentText === "Mirror"
                    ? "Start Sunshine Mirror"
                    : (backendChips.currentText === "None" ? "Create Virtual Display" : "Create Virtual Display")
                primary: true
                implicitWidth: 240
                implicitHeight: 44
                Layout.alignment: Qt.AlignHCenter
                onClicked: {
                    page.saveSettings()
                    backend.startStreaming(
                        page.resolutionValue(), page.fpsValue(), displayType.currentText,
                        encoder.currentText, page.selectedGpuId(), codec.currentText,
                        nativeInput.checked, audio.checked
                    )
                }
            }

            Text {
                text: backendChips.currentText === "None"
                    ? "Virtual display will be created without a streaming backend."
                    : "Moonlight will discover the Sunshine host on your network. For a manual connection use " + backend.localIp + "."
                color: theme.textMuted
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                Layout.leftMargin: 40
                Layout.rightMargin: 40
            }
        }
    }
}
