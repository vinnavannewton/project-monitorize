import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    property bool isWifi: true
    property bool enableStylusFeatures: false
    property bool loadingSettings: true
    property bool syncingBitrate: false
    readonly property string returnPageSource: page.isWifi ? "WifiPage.qml" : "UsbStep2Page.qml"
    readonly property bool stylusControlsVisible: (
        backend.detectedDe === "kde"
        || backend.detectedDe === "gnome"
        || backend.detectedDe === "hyprland"
        || backend.detectedDe === "sway"
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

    function setBitrateMbps(value, save) {
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
            encoderProfileCombo.currentText
        ]
        if (page.isWifi) {
            backend.saveWifiSettings(
                ...args,
                streamTypeCombo.currentText.indexOf("Speed") === 0 ? "Speed" : "Stability",
                encryptionCheck.checked
            )
        } else {
            backend.saveUsbSettings(...args)
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
            resCombo.selectValue("2560x1600");
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
        
        page.setBitrateMbps(Number(saved["bitrate"] || "8000") / 1000, false);
        
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
        
        if (page.isWifi) {
            let savedStreamType = saved["stream_type"] || "Speed";
            streamTypeCombo.selectValue(savedStreamType === "Speed" ? "Speed" : "Stability");
            encryptionCheck.checked = saved["use_encryption"] !== false;
        }

        let gen = backend.loadGeneralSettings();
        let enableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true;
        page.enableStylusFeatures = gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false;
        stylusCheck.checked = page.enableStylusFeatures;
        touchCheck.checked = enableTouch;
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

            Text {
                text: page.isWifi ? "Wi-Fi Mode Settings" : "USB Mode  ·  Step 2 of 2"
                font.pixelSize: 12
                font.weight: Font.Bold
                color: theme.textMuted
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.border
            }

            RowLayout {
                visible: page.isWifi
                Layout.alignment: Qt.AlignHCenter
                spacing: 10
                Text { text: "📶"; font.pixelSize: 22 }
                Text {
                    text: "Your Local IP Address is: " + backend.localIp
                    font.pixelSize: 16
                    font.weight: Font.Bold
                    color: "#2e7d32"
                }
            }

            Text {
                text: page.isWifi
                    ? "Enter this IP in the Monitorize Android app and tap Receive."
                    : "Please open the Monitorize app on your tablet."
                font.pixelSize: 14
                color: theme.textSecondary
                Layout.alignment: Qt.AlignHCenter
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
                    model: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "3840x2160 (16:9)", "Custom..."]
                    onActivated: page.saveSettings()
                }

                // Custom Res fields row
                Text { text: ""; visible: resCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: resCombo.currentText === "Custom..."

                    CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4; onTextEdited: page.saveSettings() }
                    Text { text: "×"; color: theme.textSecondary; font.pixelSize: 18; font.weight: Font.Bold }
                    CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4; onTextEdited: page.saveSettings() }
                    Text { text: "(500 - 4000)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "FPS:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: fpsCombo
                    model: ["30", "60", "90", "120", "Custom..."]
                    onActivated: page.saveSettings()
                }

                // Custom FPS field row
                Text { text: ""; visible: fpsCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: fpsCombo.currentText === "Custom..."

                    CustomTextField { id: customFps; placeholderText: "FPS"; maximumLength: 3; onTextEdited: page.saveSettings() }
                    Text { text: "(24 - 240)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "Video Bitrate (Mbps):"; color: theme.textSecondary; font.pixelSize: 14 }
                RowLayout {
                    spacing: 10

                    Slider {
                        id: bitrateSlider
                        from: 0.25
                        to: 50
                        stepSize: 0.25
                        value: 8
                        snapMode: Slider.SnapAlways
                        Layout.preferredWidth: 240
                        onMoved: page.setBitrateMbps(value, true)
                    }

                    CustomTextField {
                        id: bitrateField
                        text: "8"
                        maximumLength: 5
                        validator: DoubleValidator {
                            bottom: 0.25
                            top: 100
                            decimals: 2
                            notation: DoubleValidator.StandardNotation
                        }
                        onTextEdited: {
                            if (page.syncingBitrate) return
                            let mbps = parseFloat(text)
                            if (!isNaN(mbps)) {
                                bitrateSlider.value = Math.min(50, page.clampMbps(mbps))
                                page.saveSettings()
                            }
                        }
                        onEditingFinished: page.setBitrateMbps(parseFloat(text), true)
                    }

                    Text {
                        text: "Mbps"
                        color: theme.textMuted
                        font.pixelSize: 12
                    }
                }

                // Display Type (only on KDE/GNOME/Hyprland)
                Text {
                    text: "Display Type:"
                    color: theme.textSecondary
                    font.pixelSize: 14
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland" || backend.detectedDe === "sway"
                }
                CustomComboBox {
                    id: displayTypeCombo
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland" || backend.detectedDe === "sway"
                    model: ["Extend", "Mirror"]
                    onActivated: page.saveSettings()
                }

                Text { text: "Encoder:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: encoderCombo
                    currentIndex: 2
                    model: [
                        "NVIDIA NVENC (nvh264enc)",
                        "Intel/AMD VA-API (vah264enc)",
                        "Software (CPU / x264enc)"
                    ]
                    onActivated: page.saveSettings()
                }

                Text { text: "Encoder Profile:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: encoderProfileCombo
                    model: ["Low Latency", "Balanced", "Quality"]
                    currentIndex: 0
                    onActivated: page.saveSettings()
                }

                Text { text: "Stream Type:"; visible: page.isWifi; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: streamTypeCombo
                    visible: page.isWifi
                    currentIndex: 0
                    model: ["Speed (Lowest Latency)", "Stability (Low-spec Wi-Fi)"]
                    onActivated: page.saveSettings()
                }
            }

            // Checkbox Settings
            ColumnLayout {
                spacing: 8
                Layout.alignment: Qt.AlignHCenter

                CustomCheckBox {
                    id: encryptionCheck
                    visible: page.isWifi
                    text: "Use encryption (recommended)"
                    checked: true
                    onCheckedChanged: page.saveSettings()
                }

                CustomCheckBox {
                    id: touchCheck
                    text: "Enable Touch Input"
                    onCheckedChanged: {
                        page.enableStylusFeatures = stylusCheck.checked
                        page.saveGeneralSettings()
                    }
                }

                CustomCheckBox {
                    id: stylusCheck
                    text: "Enable Stylus Features"
                    visible: page.stylusControlsVisible
                    onCheckedChanged: {
                        page.enableStylusFeatures = checked
                        page.saveGeneralSettings()
                    }
                }
            }

            WarningCard {
                text: page.isWifi
                    ? (encryptionCheck.checked
                        ? "Encrypted mode requires the 6-digit pairing code shown after streaming starts."
                        : "WARNING: Encryption is off. Other devices on this network may view the stream or inject input.")
                    : "WARNING: The Resolution set here MUST EXACTLY MATCH the settings in the Android tablet app, or the stream will corrupt!"
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
                            page.isWifi
                        );
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: !backend.isStreaming && backend.streamingStatus.length > 0
                text: backend.streamingStatus
                color: backend.streamingStatus.indexOf("did not") >= 0 || backend.streamingStatus.indexOf("ERROR") >= 0
                       ? "#fca5a5"
                       : theme.textMuted
                font.pixelSize: 12
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
        }
    }
}
