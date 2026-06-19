import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property var pendingDevice: null
    property string setupMessage: ""

    function selectedPort(device) {
        return displayCombo.currentIndex === 1
            ? (device.thirdPort || 7114)
            : (device.port || 7110)
    }

    function connectDevice(device, code) {
        backend.connectToHost(
            device.ip,
            device.port,
            device.encrypted === true,
            device.fingerprint || "",
            code || ""
        )
    }

    function requestConnection(device) {
        let target = {
            "ip": device.ip,
            "port": selectedPort(device),
            "encrypted": device.encrypted === true,
            "fingerprint": device.fingerprint || ""
        }
        if (displayCombo.currentIndex === 1
                && device.thirdAvailable === false) {
            setupMessage = "Third display is not active on this host."
            return
        }
        setupMessage = ""
        if (target.encrypted
                && backend.receiverNeedsPairing(target.ip, target.fingerprint)) {
            pendingDevice = target
            pairingCodeField.text = ""
            pairingPopup.open()
        } else {
            connectDevice(target, "")
        }
    }

    Component.onCompleted: {
        backend.startHostDiscovery()
        let rec = backend.loadReceiverSettings()
        if (rec) {
            manualIpField.text = rec["manual_ip"] || ""
            displayCombo.currentIndex = (rec["manual_port"] || "7110") === "7114" ? 1 : 0
            encryptionCheck.checked = rec["use_encryption"] !== false
        }
    }

    Component.onDestruction: {
        backend.stopHostDiscovery()
    }

    Connections {
        target: backend
        function onDiscoveredDevicesChanged() {
            let devs = backend.discoveredDevices
            deviceRepeater.model = null
            deviceRepeater.model = devs
        }
        function onReceiverPairingRequired(host, port, fingerprint) {
            page.setupMessage = "Saved authorization was rejected. Enter a new pairing code."
            pendingDevice = {
                "ip": host,
                "port": port,
                "encrypted": true,
                "fingerprint": fingerprint
            }
            pairingCodeField.text = ""
            pairingPopup.open()
        }
    }

    ColumnLayout {
        anchors.fill: parent
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
            height: 1
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
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            clip: true

            ColumnLayout {
                width: parent.width
                spacing: 8

                // Empty state
                Text {
                    visible: !deviceRepeater.model || deviceRepeater.model.length === 0
                    text: "Searching for Monitorize hosts on the network…\n(Make sure the other PC has Monitorize running)"
                    font.pixelSize: 13
                    color: theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 30
                }

                Repeater {
                    id: deviceRepeater
                    model: []

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
                                    text: modelData.encrypted === true ? "encrypted" : "wifi"
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
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: theme.border
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

            Text {
                text: "Receive:"
                color: theme.cardTextSecondary
                font.pixelSize: 13
            }

            CustomComboBox {
                id: displayCombo
                model: ["Second display", "Third display"]
                Layout.preferredWidth: 180
                onActivated: backend.saveReceiverSettings(
                    manualIpField.text.trim(),
                    currentIndex === 1 ? "7114" : "7110",
                    encryptionCheck.checked
                )
            }

            Item { Layout.fillWidth: true }
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
                        displayCombo.currentIndex === 1 ? "7114" : "7110",
                        encryptionCheck.checked
                    )
                }
                onAccepted: {
                    connectButton.clicked()
                }
            }

            CustomButton {
                id: connectButton
                text: "▶  Connect"
                implicitWidth: 130
                implicitHeight: 38
                onClicked: {
                    if (manualIpField.text.trim() !== "") {
                        let ip = manualIpField.text.trim()
                        let p = displayCombo.currentIndex === 1 ? 7114 : 7110
                        backend.saveReceiverSettings(ip, p.toString(), encryptionCheck.checked)
                        page.requestConnection({
                            "ip": ip,
                            "port": 7110,
                            "thirdPort": 7114,
                            "encrypted": encryptionCheck.checked,
                            "fingerprint": "",
                            "thirdAvailable": true
                        })
                    }
                }
            }
        }

        CustomCheckBox {
            id: encryptionCheck
            text: "Use encryption"
            checked: true
            onCheckedChanged: backend.saveReceiverSettings(
                manualIpField.text.trim(),
                displayCombo.currentIndex === 1 ? "7114" : "7110",
                checked
            )
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

        // Bottom Navigation
        Item { Layout.preferredHeight: 4 }

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
    }

    Popup {
        id: pairingPopup
        modal: true
        anchors.centerIn: parent
        width: 360
        height: 190

        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            radius: theme.cardRadius
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Enter the pairing code shown on the host"
                color: theme.cardTextPrimary
                font.weight: Font.Bold
            }

            CustomTextField {
                id: pairingCodeField
                placeholderText: "6-digit code"
                maximumLength: 6
                validator: IntValidator { bottom: 0; top: 999999 }
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: "Cancel"
                    onClicked: {
                        pairingPopup.close()
                        page.pendingDevice = null
                    }
                }
                CustomButton {
                    text: "Pair"
                    enabled: pairingCodeField.text.length === 6
                    onClicked: {
                        pairingPopup.close()
                        page.connectDevice(page.pendingDevice, pairingCodeField.text)
                    }
                }
            }
        }
    }
}
