import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool secondLoading: true

    function appendLog(type, message) {
        let lines = String(message).split(/\r?\n/)
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].length > 0) logArea.text += "[" + type + "] " + lines[i] + "\n"
        }
    }

    function secondResolution() {
        return secondRes.currentText === "Custom..."
            ? secondW.text + "x" + secondH.text
            : secondRes.currentText.split(" ")[0]
    }

    function secondFpsValue() {
        return secondFps.currentText === "Custom..." ? secondCustomFps.text : secondFps.currentText
    }

    function loadSecondSettings() {
        secondLoading = true
        let saved = backend.loadSecondDisplaySettings()
        secondRes.selectValue(saved["resolution"] || "1920x1080")
        secondW.text = saved["custom_w"] || "1920"
        secondH.text = saved["custom_h"] || "1080"
        secondFps.selectValue(saved["fps"] || "60")
        secondCustomFps.text = saved["custom_fps"] || "60"
        secondEncoder.selectValue(saved["sunshine_encoder"] || "Auto")
        secondCodec.selectValue(saved["sunshine_codec"] || "Auto")
        secondInput.checked = saved["sunshine_native_pen_touch"] !== false
        secondAudio.checked = saved["enable_audio"] === true
        secondLoading = false
    }

    function saveSecondSettings() {
        if (secondLoading) return
        backend.saveSecondDisplaySettings(
            secondRes.currentText,
            secondRes.currentText === "Custom..." ? secondW.text : "",
            secondRes.currentText === "Custom..." ? secondH.text : "",
            secondFps.currentText,
            secondFps.currentText === "Custom..." ? secondCustomFps.text : "",
            secondEncoder.currentText,
            secondCodec.currentText,
            secondInput.checked,
            secondAudio.checked
        )
    }

    Component.onCompleted: page.loadSecondSettings()

    Connections {
        target: backend
        function onLogAppended(type, message) { page.appendLog(type, message) }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 116
            radius: theme.cardRadius
            color: theme.surface
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8
                Text {
                    text: "Sunshine Session Active"
                    color: theme.textPrimary
                    font.pixelSize: 20
                    font.weight: Font.Bold
                }
                Text {
                    text: backend.streamingStatus
                    color: theme.accent
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    spacing: 16
                    Text { text: "Host  " + backend.localIp; color: theme.textSecondary; font.pixelSize: 12 }
                    Text { text: "Display 1  port 47989"; color: theme.textSecondary; font.pixelSize: 12 }
                    Text {
                        visible: backend.secondStreamActive
                        text: "Display 2  " + backend.localIp + ":49089"
                        color: theme.textSecondary
                        font.pixelSize: 12
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            CustomButton {
                text: "Pair Moonlight PIN"
                onClicked: {
                    pinField.text = ""
                    pinMessage.text = ""
                    pinPopup.open()
                    pinField.forceActiveFocus()
                }
            }
            CustomButton { text: "Sunshine Settings"; onClicked: backend.openSunshineWebUi(1) }
            CustomButton {
                text: backend.secondStreamActive ? "Remove Second Display" : "Add Second Display"
                onClicked: backend.secondStreamActive ? backend.stopSecondStream() : secondPopup.open()
            }
            CustomButton { text: "Display Settings"; onClicked: backend.configureDisplay() }
            Item { Layout.fillWidth: true }
            CustomButton {
                text: "Save Preset"
                onClicked: {
                    presetName.text = ""
                    presetMessage.text = ""
                    presetPopup.open()
                }
            }
            CustomButton { text: "Stop"; primary: true; onClicked: backend.stopStreaming() }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: theme.cardRadius
            color: theme.surface
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                Text {
                    text: "Session log"
                    color: theme.cardTextPrimary
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    TextArea {
                        id: logArea
                        readOnly: true
                        wrapMode: TextEdit.Wrap
                        color: theme.textSecondary
                        selectionColor: theme.accent
                        font.family: "monospace"
                        font.pixelSize: 11
                        background: Rectangle { color: theme.background; radius: theme.controlRadius }
                    }
                }
            }
        }
    }

    Popup {
        id: pinPopup
        modal: true
        anchors.centerIn: parent
        width: 380
        padding: 22
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        ColumnLayout {
            width: parent.width
            spacing: 12
            Text { text: "Pair Moonlight"; color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold }
            Text { text: "Enter the four-digit PIN shown by Moonlight."; color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            CustomTextField {
                id: pinField
                Layout.fillWidth: true
                maximumLength: 4
                validator: RegularExpressionValidator { regularExpression: /[0-9]{0,4}/ }
                onAccepted: pairButton.clicked()
            }
            Text { id: pinMessage; color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: pinPopup.close() }
                CustomButton {
                    id: pairButton
                    text: "Pair"
                    primary: true
                    onClicked: {
                        let result = backend.pairMoonlightPin(pinField.text)
                        pinMessage.text = result["message"]
                        pinMessage.color = result["success"] ? "#86efac" : "#fca5a5"
                    }
                }
            }
        }
    }

    Popup {
        id: presetPopup
        modal: true
        anchors.centerIn: parent
        width: 380
        padding: 22
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        ColumnLayout {
            width: parent.width
            spacing: 12
            Text { text: "Save Session Preset"; color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold }
            CustomTextField { id: presetName; Layout.fillWidth: true; placeholderText: "Preset name"; maximumLength: 32 }
            Text { id: presetMessage; color: "#fca5a5"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: presetPopup.close() }
                CustomButton {
                    text: "Save"
                    primary: true
                    onClicked: {
                        let result = backend.saveCurrentPreset(presetName.text, -1)
                        if (result === "") presetPopup.close()
                        else presetMessage.text = result === "full" ? "Delete or replace an existing preset first." : result
                    }
                }
            }
        }
    }

    Popup {
        id: secondPopup
        modal: true
        anchors.centerIn: parent
        width: Math.min(680, page.width - 40)
        height: Math.min(520, page.height - 30)
        padding: 22
        onOpened: page.loadSecondSettings()
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }

        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            Text { text: "Add a Second Sunshine Display"; color: theme.textPrimary; font.pixelSize: 20; font.weight: Font.Bold }
            Text {
                text: "Moonlight connects to the second instance at " + backend.localIp + ":49089."
                color: theme.textSecondary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                GridLayout {
                    width: parent.width
                    columns: 2
                    columnSpacing: 16
                    rowSpacing: 12
                    Text { text: "Resolution"; color: theme.textSecondary }
                    CustomComboBox {
                        id: secondRes
                        Layout.preferredWidth: 260
                        model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "Custom..."]
                        onActivated: page.saveSecondSettings()
                    }
                    Text { text: ""; visible: secondRes.currentText === "Custom..." }
                    RowLayout {
                        visible: secondRes.currentText === "Custom..."
                        CustomTextField { id: secondW; placeholderText: "Width"; maximumLength: 4; onEditingFinished: page.saveSecondSettings() }
                        Text { text: "×"; color: theme.textSecondary }
                        CustomTextField { id: secondH; placeholderText: "Height"; maximumLength: 4; onEditingFinished: page.saveSecondSettings() }
                    }
                    Text { text: "Refresh rate"; color: theme.textSecondary }
                    CustomComboBox { id: secondFps; Layout.preferredWidth: 260; model: ["30", "60", "90", "120", "Custom..."]; onActivated: page.saveSecondSettings() }
                    Text { text: ""; visible: secondFps.currentText === "Custom..." }
                    CustomTextField { id: secondCustomFps; visible: secondFps.currentText === "Custom..."; placeholderText: "24–240"; maximumLength: 3; onEditingFinished: page.saveSecondSettings() }
                    Text { text: "Encoder"; color: theme.textSecondary }
                    ChoiceChips { id: secondEncoder; model: ["Auto", "NVIDIA", "VA-API", "Software Enc"]; chipWidth: 112; onActivated: page.saveSecondSettings() }
                    Text { text: "Codec"; color: theme.textSecondary }
                    ChoiceChips { id: secondCodec; model: ["Auto", "H.264 (AVC)", "H.265 (HEVC)", "AV1"]; chipWidth: 112; onActivated: page.saveSecondSettings() }
                    Text { text: "" }
                    ColumnLayout {
                        CustomToggle { id: secondInput; text: "Moonlight touch and stylus input"; onCheckedChanged: page.saveSecondSettings() }
                        CustomToggle { id: secondAudio; text: "Stream audio"; onCheckedChanged: page.saveSecondSettings() }
                    }
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; onClicked: secondPopup.close() }
                CustomButton {
                    text: "Create Display"
                    primary: true
                    onClicked: {
                        page.saveSecondSettings()
                        backend.setSunshineEncoder(secondEncoder.currentText, 2)
                        backend.setSunshineCodec(secondCodec.currentText, 2)
                        backend.setSunshineNativePenTouch(secondInput.checked, 2)
                        backend.startSecondStream(
                            page.secondResolution(), page.secondFpsValue(),
                            secondEncoder.currentText, secondCodec.currentText,
                            secondInput.checked, secondAudio.checked
                        )
                        secondPopup.close()
                    }
                }
            }
        }
    }
}
