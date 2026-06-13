import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    Transition {
        id: fastRebound
        SpringAnimation {
            properties: "y"
            spring: 12.0
            damping: 0.8
        }
    }

    Component.onCompleted: {
        let saved = backend.loadWifiSettings();
        let savedRes = saved["resolution"] || "2560x1600";
        let foundIdx = -1;
        for (let i = 0; i < resCombo.count; i++) {
            if (resCombo.textAt(i).indexOf(savedRes) === 0) {
                foundIdx = i;
                break;
            }
        }
        resCombo.currentIndex = (foundIdx !== -1) ? foundIdx : 5;
        if (saved["resolution"] === "Custom...") {
            customW.text = saved["custom_w"] || "";
            customH.text = saved["custom_h"] || "";
        }
        fpsCombo.currentIndex = fpsCombo.find(saved["fps"] || "60");
        if (saved["fps"] === "Custom...") {
            customFps.text = saved["custom_fps"] || "";
        }
        bitrateField.text = saved["bitrate"] || "8000";
        if (saved["display_type"] && displayTypeCombo) {
            let idx = displayTypeCombo.find(saved["display_type"]);
            if (idx === -1) {
                idx = displayTypeCombo.find("Extend");
            }
            displayTypeCombo.currentIndex = (idx !== -1) ? idx : 0;
        }
        let savedEnc = saved["encoder"] || "Software (CPU / x264enc)";
        if (savedEnc === "Auto-detect" || savedEnc === "Auto-detect (Recommended)") {
            savedEnc = "Software (CPU / x264enc)";
        }
        let encIdx = encoderCombo.find(savedEnc);
        if (encIdx === -1) {
            encIdx = encoderCombo.find("Software (CPU / x264enc)");
        }
        encoderCombo.currentIndex = (encIdx !== -1) ? encIdx : 2;

        let gen = backend.loadGeneralSettings();
        trayCheck.checked = gen["minimize_to_tray"] || false;
        touchCheck.checked = gen["enable_touch"] || false;

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
                text: "Wi-Fi Mode Settings"
                font.pixelSize: 12
                font.weight: Font.Bold
                color: "#5a5c82"
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: "#1a1c30"
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 10
                Text { text: "📶"; font.pixelSize: 22 }
                Text {
                    text: "Your Local IP Address is: " + backend.localIp
                    font.pixelSize: 16
                    font.weight: Font.Bold
                    color: "#4cd68d"
                }
            }

            Text {
                text: "Enter this IP in the Monitorize Android app and tap Receive."
                font.pixelSize: 14
                color: "#8a8cc0"
                Layout.alignment: Qt.AlignHCenter
            }

            // Fields Grid
            GridLayout {
                columns: 2
                columnSpacing: 20
                rowSpacing: 12
                Layout.alignment: Qt.AlignHCenter

                Text { text: "Resolution:"; color: "#b0b2d0"; font.pixelSize: 14 }
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
                    Text { text: "×"; color: "#6a6c96"; font.pixelSize: 18; font.weight: Font.Bold }
                    CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4 }
                    Text { text: "(500 - 4000)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "FPS:"; color: "#b0b2d0"; font.pixelSize: 14 }
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
                    Text { text: "(24 - 240)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                }

                Text { text: "Video Bitrate (kbps):"; color: "#b0b2d0"; font.pixelSize: 14 }
                CustomTextField {
                    id: bitrateField
                    text: "8000"
                    maximumLength: 5
                }

                // Display Type (only on KDE/GNOME/Hyprland)
                Text {
                    text: "Display Type:"
                    color: "#b0b2d0"
                    font.pixelSize: 14
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                }
                CustomComboBox {
                    id: displayTypeCombo
                    visible: backend.detectedDe === "kde" || backend.detectedDe === "gnome" || backend.detectedDe === "hyprland"
                    model: ["Extend", "Mirror"]
                }

                Text { text: "Encoder:"; color: "#b0b2d0"; font.pixelSize: 14 }
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

            // Checkbox Settings row
            RowLayout {
                spacing: 24
                Layout.alignment: Qt.AlignHCenter

                CustomCheckBox {
                    id: trayCheck
                    text: "Minimize to tray on close"
                }

                CustomCheckBox {
                    id: touchCheck
                    text: "Enable Touch Input"
                }
            }

            // Warning card
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: warningText.implicitHeight + 20
                radius: 8
                color: "#161109"
                border.color: "#3d2a0a"
                border.width: 1

                Text {
                    id: warningText
                    anchors.fill: parent
                    anchors.margins: 10
                    text: "WARNING: The Resolution set here MUST EXACTLY MATCH the settings in the Android tablet app, or the stream will corrupt!"
                    color: "#e8a840"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    wrapMode: Text.Wrap
                }
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
                    text: "▶  Start Streaming"
                    implicitWidth: 200
                    implicitHeight: 44
                    onClicked: {
                        let cleanRes = resCombo.currentText;
                        if (cleanRes !== "Custom...") {
                            cleanRes = cleanRes.split(" ")[0];
                        }
                        // Save settings
                        backend.saveGeneralSettings(trayCheck.checked, touchCheck.checked);
                        backend.saveWifiSettings(
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
                            true // isWifi = true
                        );
                    }
                }
            }
        }
    }
}
