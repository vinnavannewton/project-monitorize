import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property int selectedPresetIndex: -1
    property string selectedPresetName: ""
    readonly property int modeCardWidth: 220
    readonly property int modeCardSpacing: 30
    readonly property int modeCardsWidth: modeCardWidth * 3 + modeCardSpacing * 2

    ColumnLayout {
        anchors.centerIn: parent
        spacing: backend.presets.length > 0 ? 14 : 20
        width: Math.min(parent.width - 40, page.modeCardsWidth)

        Text {
            text: "Monitorize"
            font.pixelSize: 32
            font.weight: Font.ExtraBold
            color: theme.textPrimary
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
                    color: theme.cardTextPrimary
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    Layout.alignment: Qt.AlignVCenter
                }
            }
        }

        Item { Layout.preferredHeight: 20 }

        RowLayout {
            id: modeCardsRow
            spacing: page.modeCardSpacing
            Layout.alignment: Qt.AlignHCenter

            // USB Mode Card
            Rectangle {
                id: usbCard
                implicitWidth: page.modeCardWidth
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
                implicitWidth: page.modeCardWidth
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
                implicitWidth: page.modeCardWidth
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

        Item { Layout.preferredHeight: 8 }

        Text {
            text: "Saved Presets"
            visible: backend.presets.length > 0
            font.pixelSize: 13
            font.weight: Font.Bold
            color: theme.textSecondary
            width: modeCardsRow.implicitWidth
            Layout.preferredWidth: modeCardsRow.implicitWidth
            Layout.alignment: Qt.AlignHCenter
            horizontalAlignment: Text.AlignLeft
        }

        Flow {
            Layout.preferredWidth: modeCardsRow.implicitWidth
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredHeight: backend.presets.length > 0 ? 82 : 0
            spacing: 12
            visible: backend.presets.length > 0

            Repeater {
                model: backend.presets

                Rectangle {
                    id: presetCard
                    required property int index
                    required property var modelData
                    width: (parent.width - 36) / 4
                    height: 82
                    radius: 8
                    color: presetMouse.containsMouse ? theme.surfaceAlt : theme.surface
                    border.color: presetMouse.containsMouse ? theme.borderHover : theme.border
                    border.width: 1

                    MouseArea {
                        id: presetMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: backend.launchPreset(presetCard.index)
                    }

                    Column {
                        anchors.left: parent.left
                        anchors.right: menuButton.left
                        anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 5

                        Text {
                            width: parent.width
                            text: presetCard.modelData["name"]
                            color: theme.cardTextPrimary
                            font.pixelSize: 13
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                        }

                        Text {
                            width: parent.width
                            text: (presetCard.modelData["mode"] === "wifi" ? "Wi-Fi" : "USB")
                                + "  " + presetCard.modelData["primary"]["resolution"]
                                + " @ " + presetCard.modelData["primary"]["fps"]
                            color: theme.cardTextSecondary
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }

                        Text {
                            width: parent.width
                            text: (presetCard.modelData["mode"] === "wifi"
                                ? (presetCard.modelData["wifi"]["use_encryption"] ? "Encrypted" : "Plain")
                                : "Local")
                                + (presetCard.modelData["third"]["enabled"] ? "  + extra display" : "")
                            color: theme.cardTextMuted
                            font.pixelSize: 10
                            elide: Text.ElideRight
                        }
                    }

                    Button {
                        id: menuButton
                        z: 2
                        width: 28
                        height: 28
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.margins: 6
                        flat: true
                        text: "⋮"
                        ToolTip.visible: hovered
                        ToolTip.text: "Preset options"
                        onClicked: presetMenu.open()
                        contentItem: Text {
                            text: parent.text
                            color: theme.cardTextSecondary
                            font.pixelSize: 18
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        Menu {
                            id: presetMenu

                            width: 132
                            padding: 6
                            background: Rectangle {
                                color: theme.surface
                                border.color: theme.border
                                border.width: 1
                                radius: theme.controlRadius
                            }

                            MenuItem {
                                id: renameMenuItem
                                text: "Rename"
                                implicitWidth: 120
                                implicitHeight: 34
                                onTriggered: {
                                    page.selectedPresetIndex = presetCard.index
                                    page.selectedPresetName = presetCard.modelData["name"]
                                    renameField.text = page.selectedPresetName
                                    renameError.text = ""
                                    renamePopup.open()
                                    renameField.forceActiveFocus()
                                }
                                background: Rectangle {
                                    radius: 5
                                    color: renameMenuItem.highlighted ? theme.surfaceAlt : theme.surface
                                    Behavior on color { ColorAnimation { duration: 120 } }
                                }
                                contentItem: Text {
                                    text: renameMenuItem.text
                                    color: renameMenuItem.highlighted ? theme.textPrimary : theme.cardTextPrimary
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 12
                                }
                            }
                            MenuItem {
                                id: deleteMenuItem
                                text: "Delete"
                                implicitWidth: 120
                                implicitHeight: 34
                                onTriggered: {
                                    page.selectedPresetIndex = presetCard.index
                                    page.selectedPresetName = presetCard.modelData["name"]
                                    deletePopup.open()
                                }
                                background: Rectangle {
                                    radius: 5
                                    color: deleteMenuItem.highlighted ? theme.surfaceAlt : theme.surface
                                    Behavior on color { ColorAnimation { duration: 120 } }
                                }
                                contentItem: Text {
                                    text: deleteMenuItem.text
                                    color: deleteMenuItem.highlighted ? "#fca5a5" : theme.cardTextPrimary
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 12
                                }
                            }
                        }
                    }
                }
            }
        }

        Text {
            text: backend.presets.length === 0
                ? "No saved presets"
                : backend.presetLaunchStatus
            visible: backend.presets.length === 0 || backend.presetLaunchStatus.length > 0
            font.pixelSize: 12
            color: backend.presetLaunchStatus.indexOf("Error:") === 0
                ? "#fca5a5" : theme.textMuted
            Layout.alignment: Qt.AlignHCenter
        }
    }

    Popup {
        id: renamePopup
        modal: true
        anchors.centerIn: parent
        width: 360
        height: 190
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            radius: theme.cardRadius
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Text {
                text: "Rename Preset"
                color: theme.cardTextPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            CustomTextField {
                id: renameField
                Layout.fillWidth: true
                maximumLength: 32
                onAccepted: renameButton.clicked()
            }
            Text {
                id: renameError
                Layout.fillWidth: true
                color: "#fca5a5"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button { text: "Cancel"; onClicked: renamePopup.close() }
                CustomButton {
                    id: renameButton
                    text: "Rename"
                    onClicked: {
                        let error = backend.renamePreset(
                            page.selectedPresetIndex, renameField.text
                        )
                        renameError.text = error
                        if (error.length === 0) renamePopup.close()
                    }
                }
            }
        }
    }

    Popup {
        id: deletePopup
        modal: true
        anchors.centerIn: parent
        width: 360
        height: 160
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            radius: theme.cardRadius
        }
        ColumnLayout {
            anchors.fill: parent
            spacing: 16
            Text {
                Layout.fillWidth: true
                text: "Delete “" + page.selectedPresetName + "”?"
                color: theme.cardTextPrimary
                font.pixelSize: 16
                font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button { text: "Cancel"; onClicked: deletePopup.close() }
                Button {
                    text: "Delete"
                    onClicked: {
                        backend.deletePreset(page.selectedPresetIndex)
                        deletePopup.close()
                    }
                }
            }
        }
    }
}
