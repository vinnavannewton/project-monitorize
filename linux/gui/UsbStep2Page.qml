import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    property bool minimizeToTray: false
    property bool enableStylusFeatures: false
    property bool stylusOnly: false
    property bool loadingSettings: true
    readonly property bool stylusControlsVisible: (
        backend.detectedDe === "kde"
        || backend.detectedDe === "gnome"
        || backend.detectedDe === "hyprland"
    )

    function saveGeneralSettings() {
        if (page.loadingSettings) {
            return
        }
        backend.saveGeneralSettings(
            page.minimizeToTray,
            touchCheck.checked,
            stylusCheck.checked,
            stylusOnlyCheck.checked
        )
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
        let saved = backend.loadUsbSettings();
        
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
        
        bitrateField.text = saved["bitrate"] || "8000";
        
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

        let gen = backend.loadGeneralSettings();
        page.minimizeToTray = gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false;
        let enableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true;
        page.enableStylusFeatures = (gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false) && enableTouch;
        page.stylusOnly = (gen["stylus_only"] !== undefined ? gen["stylus_only"] : false) && enableTouch && page.enableStylusFeatures;
        stylusCheck.checked = page.enableStylusFeatures;
        stylusOnlyCheck.checked = page.stylusOnly;
        touchCheck.checked = enableTouch;
        page.loadingSettings = false;

        usbScroll.contentItem.rebound = fastRebound;
    }

    ScrollView {
        id: usbScroll
        anchors.fill: parent
        contentWidth: parent.width
        contentHeight: columnLayout.implicitHeight + 40

        ColumnLayout {
            id: columnLayout
            width: parent.width - 40
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 16

            Text {
                text: "USB Mode  ·  Step 2 of 2"
                font.pixelSize: 12
                font.weight: Font.Bold
                color: theme.textMuted
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.border
            }

            Text {
                text: "Please open the Monitorize app on your tablet."
                font.pixelSize: 15
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
                }

                // Custom Res fields row
                Text { text: ""; visible: resCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: resCombo.currentText === "Custom..."

                    CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4 }
                    Text { text: "×"; color: theme.textSecondary; font.pixelSize: 18; font.weight: Font.Bold }
                    CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4 }
                    Text { text: "(500 - 4000)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "FPS:"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomComboBox {
                    id: fpsCombo
                    model: ["30", "60", "90", "120", "Custom..."]
                }

                // Custom FPS field row
                Text { text: ""; visible: fpsCombo.currentText === "Custom..."; font.pixelSize: 14 }
                RowLayout {
                    spacing: 8
                    visible: fpsCombo.currentText === "Custom..."

                    CustomTextField { id: customFps; placeholderText: "FPS"; maximumLength: 3 }
                    Text { text: "(24 - 240)"; color: theme.textMuted; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "Video Bitrate (kbps):"; color: theme.textSecondary; font.pixelSize: 14 }
                CustomTextField {
                    id: bitrateField
                    text: "8000"
                    maximumLength: 5
                }

                // Display Type (only on KDE/GNOME/Hyprland)
                Text {
                    text: "Display Type:"
                    color: theme.textSecondary
                    font.pixelSize: 14
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                }
                CustomComboBox {
                    id: displayTypeCombo
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                    model: ["Extend", "Mirror"]
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
                }
            }

            // Checkbox Settings
            ColumnLayout {
                spacing: 8
                Layout.alignment: Qt.AlignHCenter

                CustomCheckBox {
                    id: touchCheck
                    text: "Enable Touch Input"
                    onCheckedChanged: {
                        if (!checked) {
                            stylusCheck.checked = false
                            stylusOnlyCheck.checked = false
                        }
                        page.enableStylusFeatures = stylusCheck.checked
                        page.stylusOnly = stylusOnlyCheck.checked
                        page.saveGeneralSettings()
                    }
                }

                CustomCheckBox {
                    id: stylusCheck
                    text: "Enable Stylus Features"
                    visible: page.stylusControlsVisible
                    enabled: touchCheck.checked
                    onCheckedChanged: {
                        if (!checked) {
                            stylusOnlyCheck.checked = false
                        }
                        page.enableStylusFeatures = checked
                        page.stylusOnly = stylusOnlyCheck.checked
                        page.saveGeneralSettings()
                    }
                }

                CustomCheckBox {
                    id: stylusOnlyCheck
                    text: "Disable Touch and Only Enable Stylus"
                    visible: page.stylusControlsVisible
                    enabled: touchCheck.checked && stylusCheck.checked
                    onCheckedChanged: {
                        page.stylusOnly = checked
                        page.saveGeneralSettings()
                    }
                }
            }

            WarningCard {
                text: "WARNING: The Resolution set here MUST EXACTLY MATCH the settings in the Android tablet app, or the stream will corrupt!"
            }

            // Spacing
            Item { Layout.preferredHeight: 10 }

            // Navigation buttons
            RowLayout {
                spacing: 20
                Layout.alignment: Qt.AlignHCenter

                Button {
                    text: "← Back"
                    onClicked: {
                        page.StackView.view.pop()
                    }
                    background: Rectangle {
                        implicitWidth: 100
                        implicitHeight: 38
                        color: "transparent"
                        border.color: theme.border
                        radius: 8
                    }
                    contentItem: Text {
                        text: parent.text
                        color: theme.textSecondary
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

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
                        backend.saveUsbSettings(
                            cleanRes,
                            resCombo.currentText === "Custom..." ? customW.text : "",
                            resCombo.currentText === "Custom..." ? customH.text : "",
                            fpsCombo.currentText,
                            fpsCombo.currentText === "Custom..." ? customFps.text : "",
                            bitrateField.text,
                            displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend",
                            encoderCombo.currentText
                        );
                        // Start stream
                        backend.startStreaming(
                            resCombo.currentText === "Custom..." ? customW.text + "x" + customH.text : cleanRes,
                            fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText,
                            bitrateField.text,
                            displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend",
                            encoderCombo.currentText,
                            false // isWifi = false
                        );
                    }
                }
            }
        }
    }
}
