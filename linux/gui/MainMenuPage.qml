import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    readonly property string detectedDe: backend ? backend.detectedDe : ""

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 20
        width: Math.min(parent.width - 40, 760)

        Text {
            text: "Monitorize"
            font.pixelSize: 32
            font.weight: Font.ExtraBold
            color: theme.textPrimary
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: "Linux → Android Display Bridge"
            font.pixelSize: 14
            color: theme.textSecondary
            Layout.alignment: Qt.AlignHCenter
        }

        // Desktop Badge
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 180
            implicitHeight: 32
            radius: theme.controlRadius
            color: theme.surfaceAlt
            border.color: theme.border
            border.width: 1

            RowLayout {
                anchors.centerIn: parent
                spacing: 8

                Image {
                    source: {
                        if (page.detectedDe === "kde") return "../assets/svg/kde-logo.svg"
                        if (page.detectedDe === "gnome") return "../assets/svg/gnome-logo.svg"
                        if (page.detectedDe === "hyprland") return "../assets/svg/hyprland-logo.svg"
                        return ""
                    }
                    sourceSize.width: 14
                    sourceSize.height: 14
                    Layout.alignment: Qt.AlignVCenter
                    visible: source !== ""
                }

                Text {
                    text: "Desktop: " + (page.detectedDe === "kde" ? "KDE Plasma" : (page.detectedDe === "gnome" ? "GNOME" : (page.detectedDe === "hyprland" ? "Hyprland" : (page.detectedDe === "sway" ? "Sway" : page.detectedDe.toUpperCase()))))
                    color: theme.cardTextPrimary
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    Layout.alignment: Qt.AlignVCenter
                }
            }
        }

        Item { Layout.preferredHeight: 20 }

        RowLayout {
            spacing: 30
            Layout.alignment: Qt.AlignHCenter

            // USB Mode Card
            Rectangle {
                id: usbCard
                implicitWidth: 220
                implicitHeight: 140
                radius: theme.cardRadius
                color: usbMouseArea.containsMouse ? theme.surfaceAlt : theme.surface
                border.color: usbMouseArea.containsMouse ? theme.borderHover : theme.border
                border.width: 1
                scale: usbMouseArea.containsMouse ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                Behavior on color { ColorAnimation { duration: 150 } }
                Behavior on border.color { ColorAnimation { duration: 150 } }

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12
                    Image {
                        source: "../assets/svg/usb-logo.svg"
                        sourceSize.width: 48
                        sourceSize.height: 48
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Text {
                        text: "USB Mode"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        color: theme.cardTextPrimary
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                MouseArea {
                    id: usbMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        page.StackView.view.push("UsbStep1Page.qml")
                    }
                }
            }

            // Wi-Fi Mode Card
            Rectangle {
                id: wifiCard
                implicitWidth: 220
                implicitHeight: 140
                radius: theme.cardRadius
                color: wifiMouseArea.containsMouse ? theme.surfaceAlt : theme.surface
                border.color: wifiMouseArea.containsMouse ? theme.borderHover : theme.border
                border.width: 1
                scale: wifiMouseArea.containsMouse ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                Behavior on color { ColorAnimation { duration: 150 } }
                Behavior on border.color { ColorAnimation { duration: 150 } }

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12
                    Image {
                        source: "../assets/svg/wifi-logo.svg"
                        sourceSize.width: 48
                        sourceSize.height: 48
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Text {
                        text: "Wi-Fi Mode"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        color: theme.cardTextPrimary
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                MouseArea {
                    id: wifiMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        page.StackView.view.push("WifiPage.qml")
                    }
                }
            }

            // Receiver Mode Card
            Rectangle {
                id: receiverCard
                implicitWidth: 220
                implicitHeight: 140
                radius: theme.cardRadius
                color: receiverMouseArea.containsMouse ? theme.surfaceAlt : theme.surface
                border.color: receiverMouseArea.containsMouse ? theme.borderHover : theme.border
                border.width: 1
                scale: receiverMouseArea.containsMouse ? theme.hoverScale : 1.0
                Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                Behavior on color { ColorAnimation { duration: 150 } }
                Behavior on border.color { ColorAnimation { duration: 150 } }

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12
                    Image {
                        source: "../assets/svg/receiver-logo.svg"
                        sourceSize.width: 48
                        sourceSize.height: 48
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Text {
                        text: "Receiver Mode"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        color: theme.cardTextPrimary
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                MouseArea {
                    id: receiverMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        page.StackView.view.push("ReceiverSetupPage.qml")
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 30 }

        Text {
            text: "Select a connection mode to begin"
            font.pixelSize: 12
            color: theme.textMuted
            Layout.alignment: Qt.AlignHCenter
        }
    }
}
