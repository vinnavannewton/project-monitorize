import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    property bool isWifi: true
    property bool enableStylusFeatures: false
    property bool loadingSettings: true
    property bool syncingBitrate: false
    property bool autoBitrate: true
    readonly property int optionChipWidth: 150
    readonly property string returnPageSource: page.isWifi ? "WifiPage.qml" : "UsbStep2Page.qml"
    readonly property bool stylusControlsVisible: (
        backend.detectedDe === "kde"
        || backend.detectedDe === "gnome"
        || backend.detectedDe === "hyprland"
    )

    function saveGeneralSettings() {
        if (page.loadingSettings) {
            return
        }
        let gen = backend.loadGeneralSettings()
        backend.saveGeneralSettings(
            gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false,
            touchCheck.checked,
            stylusCheck.checked
        )
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

    function bitrateKbpsText() {
        return String(Math.round(page.clampMbps(parseFloat(bitrateField.text)) * 1000))
    }

    function audioBandwidthMbps() {
        return page.isWifi ? 0.13 : 0.77
    }

    function totalBandwidthText() {
        let video = page.clampMbps(parseFloat(bitrateField.text))
        let total = video + (audioCheck.checked ? page.audioBandwidthMbps() : 0)
        let rounded = Math.round(total * 100) / 100
        if (rounded % 1 === 0) return rounded.toFixed(0)
        return (rounded * 10) % 1 === 0 ? rounded.toFixed(1) : rounded.toFixed(2)
    }

    function selectedWidth() {
        return Number(resCombo.currentText === "Custom..."
            ? customW.text : resCombo.currentText.split("x")[0])
    }

    function selectedHeight() {
        return Number(resCombo.currentText === "Custom..."
            ? customH.text : resCombo.currentText.split("x")[1].split(" ")[0])
    }

    function selectedFps() {
        return Number(fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText)
    }

    function recommendedBitrateKbps() {
        return backend.recommendedWifiBitrateKbps(
            page.selectedWidth(), page.selectedHeight(), page.selectedFps()
        )
    }

    function resolutionOrFpsChanged() {
        if (page.isWifi && page.selectedWidth() > 0 &&
                page.selectedHeight() > 0 && page.selectedFps() > 0) {
            page.setBitrateMbps(page.recommendedBitrateKbps() / 1000, true, true)
        } else {
            page.saveSettings()
        }
    }

    function setBitrateMbps(value, save, autoSelected) {
        if (autoSelected !== undefined) page.autoBitrate = autoSelected
        page.syncingBitrate = true
        let mbps = page.clampMbps(value)
        bitrateSlider.value = Math.min(50, mbps)
        bitrateField.text = page.formatMbps(mbps)
        page.syncingBitrate = false
        if (save) page.saveSettings()
    }

    function saveSettings() {
        if (page.loadingSettings) return
        let resolution = resCombo.currentText
        let args = [
            resolution === "Custom..." ? resolution : resolution.split(" ")[0],
            resolution === "Custom..." ? customW.text : "",
            resolution === "Custom..." ? customH.text : "",
            fpsCombo.currentText,
            fpsCombo.currentText === "Custom..." ? customFps.text : "",
            page.bitrateKbpsText(),
            displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend",
            encoderCombo.currentText,
            encoderProfileCombo.currentText,
            videoCodecCombo ? videoCodecCombo.currentText : "H.264 (AVC)"
        ]
        if (page.isWifi) {
            backend.saveWifiSettings(...args, fecCombo.currentText, audioCheck.checked)
        } else {
            backend.saveUsbSettings(...args, audioCheck.checked)
        }
    }

    Transition {
        id: fastRebound
        SpringAnimation {
            properties: "y"
            spring: 12.0
            damping: 0.8
        }
    }

    Component.onCompleted: {
        let saved = page.isWifi ? backend.loadWifiSettings() : backend.loadUsbSettings();
        
        if (!resCombo.selectValue(saved["resolution"])) {
            resCombo.selectValue("1920x1080");
        }
        if (saved["resolution"] === "Custom...") {
            customW.text = saved["custom_w"] || "";
            customH.text = saved["custom_h"] || "";
        }
        
        if (!fpsCombo.selectValue(saved["fps"], true)) {
            fpsCombo.selectValue("60");
        }
        if (saved["fps"] === "Custom...") {
            customFps.text = saved["custom_fps"] || "";
        }
        
        let savedMbps = Number(saved["bitrate"] || "16000") / 1000;
        page.setBitrateMbps(
            savedMbps, false,
            page.isWifi && Math.abs(savedMbps - page.recommendedBitrateKbps() / 1000) < 0.001
        );
        
        if (displayTypeCombo) {
            if (!displayTypeCombo.selectValue(saved["display_type"], true)) {
                displayTypeCombo.selectValue("Extend");
            }
        }
        
        let savedEnc = saved["encoder"] || "Software (CPU / x264enc)";
        if (savedEnc === "Auto-detect" || savedEnc === "Auto-detect (Recommended)") {
            savedEnc = "Software (CPU / x264enc)";
        }
        if (!encoderCombo.selectValue(savedEnc, true)) {
            encoderCombo.selectValue("Software (CPU / x264enc)");
        }

        if (!encoderProfileCombo.selectValue(saved["encoder_profile"] || "Low Latency", true)) {
            encoderProfileCombo.selectValue("Low Latency");
        }

        let savedCodec = saved["video_codec"] || "H.264 (AVC)";
        if (savedEnc === "Software (CPU / x264enc)") {
            savedCodec = "H.264 (AVC)";
        }
        if (!videoCodecCombo.selectValue(savedCodec, true)) {
            videoCodecCombo.selectValue("H.264 (AVC)");
        }

        if (!fecCombo.selectValue(saved["fec_mode"] || "Off", true)) {
            fecCombo.selectValue("Off");
        }
        
        let gen = backend.loadGeneralSettings();
        let enableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true;
        page.enableStylusFeatures = gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false;
        stylusCheck.checked = page.enableStylusFeatures;
        touchCheck.checked = enableTouch;
        audioCheck.checked = saved["enable_audio"] !== undefined ? saved["enable_audio"] : false;
        page.loadingSettings = false;

        wifiScroll.contentItem.rebound = fastRebound;
    }

    ScrollView {
        id: wifiScroll
        anchors.fill: parent
        contentWidth: parent.width
        contentHeight: wifiColumn.implicitHeight + 40

        ColumnLayout {
            id: wifiColumn
            width: parent.width - 40
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 16

            // Recent Wi-Fi Connections
            ColumnLayout {
                visible: page.isWifi && backend.recentWifiDevices.length > 0
                Layout.fillWidth: true
                spacing: 8
                Layout.maximumWidth: 500
                Layout.alignment: Qt.AlignHCenter

                Text {
                    text: "Recent Connections"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    color: theme.textMuted
                }

                Repeater {
                    model: backend.recentWifiDevices

                    Rectangle {
                        Layout.fillWidth: true
                        height: 52
                        radius: theme.controlRadius
                        color: theme.surface
                        border.color: theme.border
                        border.width: 1

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 12

                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: modelData.online ? "#4caf50" : theme.textMuted
                                Layout.alignment: Qt.AlignVCenter
                            }

                            ColumnLayout {
                                spacing: 1
                                Layout.fillWidth: true
                                Layout.alignment: Qt.AlignVCenter

                                Text {
                                    text: modelData.name
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    color: theme.textPrimary
                                }

                                Text {
                                    text: "IP: " + modelData.ip
                                    font.pixelSize: 11
                                    color: theme.textSecondary
                                }
                            }

                            Text {
                                text: modelData.online ? "Online" : "Offline"
                                font.pixelSize: 12
                                font.weight: Font.Bold
                                color: modelData.online ? theme.accent : theme.textMuted
                                Layout.alignment: Qt.AlignVCenter
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: theme.border
                    Layout.topMargin: 8
                    Layout.bottomMargin: 8
                }
            }

            // Fields Grid
            GridLayout {
                columns: 2
                columnSpacing: 20
                rowSpacing: 12
                Layout.alignment: Qt.AlignHCenter

                Text { text: "Resolution:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: resCombo
                    Layout.preferredWidth: page.optionChipWidth
                    model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "3840x2160 (16:9)", "Custom..."]
                    onActivated: page.resolutionOrFpsChanged()
                }

                // Custom Res fields row
                Text { text: ""; visible: resCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: resCombo.currentText === "Custom..."

                    CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4; onTextEdited: page.resolutionOrFpsChanged() }
                    Text { text: "×"; color: theme.textSecondary; font.pixelSize: 18; font.weight: Font.Bold }
                    CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4; onTextEdited: page.resolutionOrFpsChanged() }
                    Text { text: "(500 - 4000)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "FPS:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: fpsCombo
                    Layout.preferredWidth: page.optionChipWidth
                    model: ["30", "60", "90", "120", "Custom..."]
                    onActivated: page.resolutionOrFpsChanged()
                }

                // Custom FPS field row
                Text { text: ""; visible: fpsCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: fpsCombo.currentText === "Custom..."

                    CustomTextField { id: customFps; placeholderText: "FPS"; maximumLength: 3; onTextEdited: page.resolutionOrFpsChanged() }
                    Text { text: "(24 - 240)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "Video Bitrate (Mbps):"; color: theme.textSecondary; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8

                    CustomSlider {
                        id: bitrateSlider
                        from: 0.25
                        to: 50
                        stepSize: 0.25
                        value: 8
                        snapMode: Slider.SnapAlways
                        Layout.preferredWidth: page.optionChipWidth * 1.5
                        onMoved: page.setBitrateMbps(value, true, false)
                    }

                    CustomTextField {
                        id: bitrateField
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
                            if (page.syncingBitrate) return
                            page.autoBitrate = false
                            let mbps = parseFloat(text)
                            if (!isNaN(mbps)) {
                                bitrateSlider.value = Math.min(50, page.clampMbps(mbps))
                                page.saveSettings()
                            }
                        }
                        onEditingFinished: page.setBitrateMbps(parseFloat(text), true, false)
                    }

                    Text {
                        text: "Mbps"
                        color: theme.textMuted
                        font.pixelSize: 12
                        visible: !page.isWifi
                    }

                    CustomButton {
                        text: "Use auto"
                        visible: page.isWifi
                        primary: page.autoBitrate
                        implicitWidth: page.optionChipWidth
                        implicitHeight: 30
                        Layout.preferredWidth: page.optionChipWidth
                        onClicked: page.setBitrateMbps(
                            page.recommendedBitrateKbps() / 1000, true, true
                        )
                    }
                }

                Text {
                    text: "Packet-loss recovery:"
                    color: theme.textSecondary
                    font.pixelSize: 14
                    visible: page.isWifi
                }
                ChoiceChips {
                    id: fecCombo
                    visible: page.isWifi
                    chipWidth: page.optionChipWidth
                    model: ["Off", "RS-FEC 10%"]
                    currentIndex: 0
                    onActivated: page.saveSettings()
                }

                // Display Type (only on KDE/GNOME/Hyprland)
                Text {
                    text: "Display Type:"
                    color: theme.textSecondary
                    font.pixelSize: 14
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                }
                ChoiceChips {
                    id: displayTypeCombo
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                    chipWidth: page.optionChipWidth
                    model: ["Extend", "Mirror"]
                    onActivated: page.saveSettings()
                }

                Text { text: "Encoder:"; color: theme.textSecondary; font.pixelSize: 14 }
                ChoiceChips {
                    id: encoderCombo
                    chipWidth: page.optionChipWidth
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                    onActivated: {
                        if (encoderCombo.currentText === "Software (CPU / x264enc)" && videoCodecCombo.currentText === "H.265 (HEVC)") {
                            videoCodecCombo.selectValue("H.264 (AVC)")
                        }
                        page.saveSettings()
                    }
                }

                Text { text: "Video Codec:"; color: theme.textSecondary; font.pixelSize: 14 }
                ChoiceChips {
                    id: videoCodecCombo
                    chipWidth: page.optionChipWidth
                    model: ["H.264 (AVC)", "H.265 (HEVC)"]
                    currentIndex: 0
                    disabledValues: encoderCombo.currentText === "Software (CPU / x264enc)" ? ["H.265 (HEVC)"] : []
                    onActivated: {
                        if (encoderCombo.currentText === "Software (CPU / x264enc)" && videoCodecCombo.currentText === "H.265 (HEVC)") {
                            videoCodecCombo.selectValue("H.264 (AVC)")
                        }
                        page.saveSettings()
                    }
                }

                Text { text: "Encoder Profile:"; color: theme.textSecondary; font.pixelSize: 14 }
                ChoiceChips {
                    id: encoderProfileCombo
                    chipWidth: page.optionChipWidth
                    model: ["Low Latency", "Balanced", "Quality"]
                    currentIndex: 0
                    onActivated: page.saveSettings()
                }

                // Checkbox Settings, kept in the same grid column as the cards.
                Text { text: "" }
                ColumnLayout {
                    spacing: 8
                    Layout.alignment: Qt.AlignLeft

                    CustomToggle {
                        id: touchCheck
                        text: "Enable Touch Input"
                        Layout.alignment: Qt.AlignLeft
                        onCheckedChanged: {
                            page.enableStylusFeatures = stylusCheck.checked
                            page.saveGeneralSettings()
                        }
                    }

                    CustomToggle {
                        id: stylusCheck
                        text: "Enable Stylus Features"
                        visible: page.stylusControlsVisible
                        Layout.alignment: Qt.AlignLeft
                        onCheckedChanged: {
                            page.enableStylusFeatures = checked
                            page.saveGeneralSettings()
                        }
                    }

                    CustomToggle {
                        id: audioCheck
                        text: "Enable Audio"
                        Layout.alignment: Qt.AlignLeft
                        onCheckedChanged: page.saveSettings()
                    }

                    Text {
                        visible: page.isWifi
                        text: "Use Tailscale or WireGuard for encryption."
                        color: theme.textMuted
                        font.pixelSize: 11
                    }
                }
            }

            // Spacing
            Item { Layout.preferredHeight: 10 }

            RowLayout {
                spacing: 20
                Layout.alignment: Qt.AlignHCenter

                CustomButton {
                    text: "▶  Start Streaming"
                    implicitWidth: 200
                    implicitHeight: 44
                    onClicked: {
                        let cleanRes = resCombo.currentText;
                        if (cleanRes !== "Custom...") {
                            cleanRes = cleanRes.split(" ")[0];
                        }
                        // Save settings
                        page.saveGeneralSettings();
                        page.saveSettings();
                        // Start stream
                        backend.startStreaming(
                            resCombo.currentText === "Custom..." ? customW.text + "x" + customH.text : cleanRes,
                            fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText,
                            page.bitrateKbpsText(),
                            displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend",
                            encoderCombo.currentText,
                            encoderProfileCombo.currentText,
                            videoCodecCombo.currentText,
                            page.isWifi,
                            page.isWifi ? fecCombo.currentText : "Off",
                            audioCheck.checked
                        );
                    }
                }
            }
        }
    }
}
