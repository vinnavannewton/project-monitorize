import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 20
        width: Math.min(parent.width - 40, 600)

        Text {
            text: "Monitorize"
            font.pixelSize: 32
            font.weight: Font.ExtraBold
            color: "#e0e2ff"
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: "Linux → Android Display Bridge"
            font.pixelSize: 14
            color: "#6a6c96"
            Layout.alignment: Qt.AlignHCenter
        }

        // Desktop Badge
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 180
            implicitHeight: 32
            radius: 16
            color: "#161726"
            border.color: "#2a2d55"
            border.width: 1

            RowLayout {
                anchors.centerIn: parent
                spacing: 8

                Image {
                    source: {
                        if (backend.detectedDe === "kde") return "../assets/svg/kde-logo.svg"
                        if (backend.detectedDe === "gnome") return "../assets/svg/gnome-logo.svg"
                        if (backend.detectedDe === "hyprland") return "../assets/svg/hyprland-logo.svg"
                        return ""
                    }
                    sourceSize.width: 14
                    sourceSize.height: 14
                    Layout.alignment: Qt.AlignVCenter
                    visible: source !== ""
                }

                Text {
                    text: "Desktop: " + (backend.detectedDe === "kde" ? "KDE Plasma" : (backend.detectedDe === "gnome" ? "GNOME" : (backend.detectedDe === "hyprland" ? "Hyprland" : backend.detectedDe.toUpperCase())))
                    color: "#6a6cbb"
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
                radius: 16
                color: usbMouseArea.containsMouse ? "#1c1e3a" : "#12142a"
                border.color: usbMouseArea.containsMouse ? "#4c4fd0" : "#2a2d55"
                border.width: 1
                scale: usbMouseArea.containsMouse ? 1.03 : 1.0
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
                        color: "#c0c2ee"
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
                radius: 16
                color: wifiMouseArea.containsMouse ? "#1c1e3a" : "#12142a"
                border.color: wifiMouseArea.containsMouse ? "#4c4fd0" : "#2a2d55"
                border.width: 1
                scale: wifiMouseArea.containsMouse ? 1.03 : 1.0
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
                        color: "#c0c2ee"
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
        }

        Item { Layout.preferredHeight: 30 }

        Text {
            text: "Select a connection mode to begin"
            font.pixelSize: 12
            color: "#5a5c82"
            Layout.alignment: Qt.AlignHCenter
        }
    }
}
