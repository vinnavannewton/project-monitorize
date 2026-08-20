import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 680)
        spacing: 18

        Text {
            text: "Monitorize"
            color: theme.textPrimary
            font.pixelSize: 32
            font.weight: Font.ExtraBold
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: "Sunshine virtual displays for Moonlight"
            color: theme.textSecondary
            font.pixelSize: 14
            Layout.alignment: Qt.AlignHCenter
        }

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 220
            implicitHeight: 32
            radius: theme.controlRadius
            color: theme.surfaceAlt
            border.color: theme.border

            Text {
                anchors.centerIn: parent
                text: "Desktop: " + (backend.detectedDe === "kde" ? "KDE Plasma" :
                    backend.detectedDe === "gnome" ? "GNOME" :
                    backend.detectedDe === "hyprland" ? "Hyprland" : backend.detectedDe)
                color: theme.cardTextPrimary
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 150
            radius: theme.cardRadius
            color: startMouse.containsMouse ? theme.surfaceAlt : theme.surface
            border.color: startMouse.containsMouse ? theme.borderHover : theme.border
            border.width: 1

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 10
                Image {
                    source: "../assets/svg/display-add.svg"
                    sourceSize.width: 46
                    sourceSize.height: 46
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    text: "Create a Virtual Display"
                    color: theme.cardTextPrimary
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    text: "Connect from Moonlight on Android, Linux, Windows, macOS or iOS"
                    color: theme.cardTextSecondary
                    font.pixelSize: 12
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            MouseArea {
                id: startMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: page.StackView.view.push("DisplaySetupPage.qml")
            }
        }

        Text {
            text: "Saved presets"
            visible: backend.presets.length > 0
            color: theme.textSecondary
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }

        Flow {
            Layout.fillWidth: true
            spacing: 10
            visible: backend.presets.length > 0

            Repeater {
                model: backend.presets
                Rectangle {
                    required property int index
                    required property var modelData
                    width: (page.width > 600 ? 210 : 190)
                    height: 78
                    radius: theme.controlRadius
                    color: presetMouse.containsMouse ? theme.surfaceAlt : theme.surface
                    border.color: presetMouse.containsMouse ? theme.borderHover : theme.border

                    MouseArea {
                        id: presetMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: backend.launchPreset(index)
                    }

                    Column {
                        anchors.left: parent.left
                        anchors.right: deleteButton.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 12
                        spacing: 5
                        Text {
                            width: parent.width
                            text: modelData["name"]
                            elide: Text.ElideRight
                            color: theme.cardTextPrimary
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            text: modelData["primary"]["resolution"] + " @ " +
                                modelData["primary"]["fps"] + " Hz" +
                                (modelData["second"]["enabled"] ? " + second" : "")
                            elide: Text.ElideRight
                            color: theme.cardTextMuted
                            font.pixelSize: 10
                        }
                    }

                    Button {
                        id: deleteButton
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.rightMargin: 6
                        width: 32
                        height: 32
                        text: "×"
                        flat: true
                        onClicked: backend.deletePreset(index)
                        contentItem: Text {
                            text: parent.text
                            color: theme.textMuted
                            font.pixelSize: 18
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }
        }

        Text {
            text: backend.presetLaunchStatus
            visible: text.length > 0
            color: "#fca5a5"
            font.pixelSize: 12
            Layout.alignment: Qt.AlignHCenter
        }
    }
}
