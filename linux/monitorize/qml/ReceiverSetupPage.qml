import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string setupMessage: ""

    function selectedPort(device) {
        let port = portField.text.trim()
        return port.length > 0 ? port : (device.port || 7110)
    }

    function connectDevice(device) {
        backend.connectToHost(device.ip, device.port,
                              device.decoder || decoderCombo.currentText)
    }

    function requestConnection(device) {
        let target = {
            "ip": device.ip,
            "port": selectedPort(device),
            "decoder": decoderCombo.currentText
        }
        setupMessage = ""
        connectDevice(target)
    }

    Component.onCompleted: {
        backend.startHostDiscovery()
        let rec = backend.loadReceiverSettings()
        if (rec) {
            manualIpField.text = rec["manual_ip"] || ""
            portField.text = rec["port"] || "7110"
            decoderCombo.currentIndex = rec["decoder"] === "Hardware" ? 1 : 0
            statsToggle.checked = rec["show_stats"] === true
        }
    }

    Component.onDestruction: {
        backend.stopHostDiscovery()
    }

    ScrollView {
        id: receiverScroll
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        contentHeight: receiverContent.implicitHeight

        ColumnLayout {
            id: receiverContent
            width: receiverScroll.availableWidth
            spacing: 16

        // Header
        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            Text {
                text: "Receiver Mode"
                font.pixelSize: 24
                font.weight: Font.ExtraBold
                color: theme.textPrimary
            }

            Item { Layout.fillWidth: true }

            // Refresh button
            Rectangle {
                implicitWidth: refreshRow.implicitWidth + 20
                implicitHeight: 30
                radius: 8
                color: refreshArea.containsMouse ? theme.surfaceAlt : theme.surface
                border.color: theme.border
                border.width: 1
                Behavior on color { ColorAnimation { duration: 150 } }

                RowLayout {
                    id: refreshRow
                    anchors.centerIn: parent
                    spacing: 6

                    Text {
                        text: "🔄"
                        font.pixelSize: 12
                    }
                    Text {
                        text: "Refresh"
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        color: theme.cardTextSecondary
                    }
                }

                MouseArea {
                    id: refreshArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: backend.startHostDiscovery()
                }
            }
        }

        Text {
            text: "Connect this laptop to one of the host's virtual displays"
            font.pixelSize: 13
            color: theme.textSecondary
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: theme.border
        }

        // Discovered Devices Section
        Text {
            text: "DISCOVERED HOSTS"
            font.pixelSize: 11
            font.weight: Font.Bold
            color: theme.textMuted
            Layout.topMargin: 4
        }

        // Device list area
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 120
            spacing: 8

                // Empty state
                Text {
                    visible: deviceRepeater.count === 0
                    text: "Searching for Monitorize hosts on the network…\n(Make sure the other PC has Monitorize running)"
                    font.pixelSize: 13
                    color: theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 30
                }

                Repeater {
                    id: deviceRepeater
                    model: backend.discoveredDevices

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 60
                        radius: theme.cardRadius
                        color: devMouseArea.containsMouse ? theme.surfaceAlt : theme.surface
                        border.color: devMouseArea.containsMouse ? theme.borderHover : theme.border
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            spacing: 12

                            ColumnLayout {
                                spacing: 2
                                Layout.fillWidth: true

                                Text {
                                    text: modelData.name || "Unknown"
                                    font.pixelSize: 15
                                    font.weight: Font.Bold
                                    color: theme.cardTextPrimary
                                }
                                Text {
                                    text: (modelData.ip || "")
                                        + "  •  Second display"
                                        + (modelData.thirdAvailable ? "  •  Third display available" : "")
                                    font.pixelSize: 12
                                    color: theme.cardTextMuted
                                }
                            }

                            // Badge
                            Rectangle {
                                implicitWidth: badgeText.implicitWidth + 16
                                implicitHeight: 22
                                radius: 6
                                color: theme.accentAlpha20
                                border.color: theme.accentAlpha40
                                border.width: 1

                                Text {
                                    id: badgeText
                                    anchors.centerIn: parent
                                    text: "udp"
                                    font.pixelSize: 10
                                    font.weight: Font.ExtraBold
                                    color: theme.accent
                                }
                            }
                        }

                        MouseArea {
                            id: devMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                page.requestConnection(modelData)
                            }
                        }
                    }
                }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: theme.border
        }

        CustomToggle {
            id: statsToggle
            text: "Streaming stats overlay"
            Layout.alignment: Qt.AlignLeft
            onToggled: backend.setReceiverStatsVisible(checked)
        }

        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            Text {
                text: "Decoder:"
                color: theme.cardTextSecondary
                font.pixelSize: 13
            }

            ChoiceChips {
                id: decoderCombo
                model: ["Software", "Hardware"]
                onActivated: backend.saveReceiverSettings(
                    manualIpField.text.trim(),
                    portField.text.trim().length > 0 ? portField.text.trim() : "7110",
                    currentText
                )
            }

            Item { Layout.fillWidth: true }
        }

        // Manual IP Connection
        Text {
            text: "MANUAL CONNECTION"
            font.pixelSize: 11
            font.weight: Font.Bold
            color: theme.textMuted
        }

        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            CustomTextField {
                id: manualIpField
                placeholderText: "Enter host IP address"
                Layout.fillWidth: true
                onTextEdited: {
                    backend.saveReceiverSettings(
                        text.trim(),
                        portField.text.trim().length > 0 ? portField.text.trim() : "7110",
                        decoderCombo.currentText
                    )
                }
                onAccepted: {
                    connectButton.clicked()
                }
            }

            CustomTextField {
                id: portField
                text: "7110"
                placeholderText: "Port"
                maximumLength: 5
                validator: IntValidator { bottom: 1; top: 65535 }
                Layout.preferredWidth: 120
                onTextEdited: {
                    backend.saveReceiverSettings(
                        manualIpField.text.trim(),
                        text.trim().length > 0 ? text.trim() : "7110",
                        decoderCombo.currentText
                    )
                }
            }

            CustomButton {
                id: connectButton
                text: backend.isReceiving ? "Disconnect" : "▶  Connect"
                implicitWidth: 130
                implicitHeight: 38
                onClicked: {
                    if (backend.isReceiving) {
                        backend.stopReceiving()
                        return
                    }
                    if (manualIpField.text.trim() !== "") {
                        let ip = manualIpField.text.trim()
                        let p = portField.text.trim().length > 0 ? portField.text.trim() : "7110"
                        backend.saveReceiverSettings(
                            ip, p, decoderCombo.currentText
                        )
                        page.requestConnection({
                            "ip": ip,
                            "port": p,
                            "thirdAvailable": true
                        })
                    }
                }
            }
        }

        Text {
            text: "Wi-Fi video is plaintext. Use Tailscale or WireGuard for encrypted networking."
            color: theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            id: backendStatus
            text: page.setupMessage !== "" ? page.setupMessage : backend.receiverStatus
            color: text.toLowerCase().includes("failed") || text.toLowerCase().includes("not active")
                ? "#fca5a5" : theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        }

    }
}
