import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    Component.onCompleted: {
        backend.startHostDiscovery()
    }

    Component.onDestruction: {
        backend.stopHostDiscovery()
    }

    Connections {
        target: backend
        function onDiscoveredDevicesChanged() {
            deviceRepeater.model = backend.discoveredDevices
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
                color: "#e0e2ff"
            }

            Item { Layout.fillWidth: true }

            // Refresh button
            Rectangle {
                implicitWidth: refreshRow.implicitWidth + 20
                implicitHeight: 30
                radius: 8
                color: refreshArea.containsMouse ? "#2a2d55" : "#161726"
                border.color: "#2a2d55"
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
                        color: "#8a8cc0"
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
            color: "#6a6c96"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: "#1a1c30"
        }

        // Discovered Devices Section
        Text {
            text: "DISCOVERED HOSTS"
            font.pixelSize: 11
            font.weight: Font.Bold
            color: "#5a5c82"
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
                    color: "#5a5c82"
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
                        color: devMouseArea.containsMouse ? "#1c1e3a" : "#12142a"
                        border.color: devMouseArea.containsMouse ? "#4c4fd0" : "#2a2d55"
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
                                    color: "#e0e2ff"
                                }
                                Text {
                                    text: modelData.ip || ""
                                    font.pixelSize: 12
                                    color: "#6a6c96"
                                }
                            }

                            // Badge
                            Rectangle {
                                implicitWidth: badgeText.implicitWidth + 16
                                implicitHeight: 22
                                radius: 6
                                color: "#7c3aed20"
                                border.color: "#7c3aed40"
                                border.width: 1

                                Text {
                                    id: badgeText
                                    anchors.centerIn: parent
                                    text: "wifi"
                                    font.pixelSize: 10
                                    font.weight: Font.ExtraBold
                                    color: "#a78bfa"
                                }
                            }
                        }

                        MouseArea {
                            id: devMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                backend.connectToHost(modelData.ip)
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: "#1a1c30"
        }

        // Manual IP Connection
        Text {
            text: "MANUAL CONNECTION"
            font.pixelSize: 11
            font.weight: Font.Bold
            color: "#5a5c82"
        }

        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            CustomTextField {
                id: manualIpField
                placeholderText: "Enter host IP address"
                Layout.fillWidth: true
            }

            CustomButton {
                text: "▶  Connect"
                implicitWidth: 130
                implicitHeight: 38
                onClicked: {
                    if (manualIpField.text.trim() !== "") {
                        backend.connectToHost(manualIpField.text.trim())
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
    }
}
