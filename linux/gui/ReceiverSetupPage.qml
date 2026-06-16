import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    Component.onCompleted: {
        backend.startHostDiscovery()
        let rec = backend.loadReceiverSettings()
        if (rec) {
            manualIpField.text = rec["manual_ip"] || ""
            manualPortField.text = rec["manual_port"] || "7110"
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
            text: "Connect to another PC running Monitorize to use this laptop as a second screen"
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
                        radius: 12
                        color: devMouseArea.containsMouse ? theme.surfaceAlt : theme.surface
                        border.color: devMouseArea.containsMouse ? theme.accent : theme.border
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
                                    text: (modelData.ip || "") + (modelData.port ? ":" + modelData.port : "")
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
                                    text: "wifi"
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
                                backend.connectToHost(modelData.ip, modelData.port || 7110)
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

            CustomTextField {
                id: manualIpField
                placeholderText: "Enter host IP address"
                Layout.fillWidth: true
                onTextEdited: {
                    backend.saveReceiverSettings(text.trim(), manualPortField.text.trim())
                }
                onAccepted: {
                    connectButton.clicked()
                }
            }

            CustomTextField {
                id: manualPortField
                placeholderText: "Port"
                text: "7110"
                implicitWidth: 80
                validator: IntValidator { bottom: 1024; top: 65535 }
                onTextEdited: {
                    backend.saveReceiverSettings(manualIpField.text.trim(), text.trim())
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
                        let p = parseInt(manualPortField.text.trim()) || 7110
                        backend.saveReceiverSettings(ip, manualPortField.text.trim())
                        backend.connectToHost(ip, p)
                    }
                }
            }
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
}
