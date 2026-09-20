import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property int renameIndex: -1
    ScrollView {
        anchors.fill: parent; clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 18
            Text { text: "Presets"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold }
            Text {
                visible: backend.presets.length === 0
                text: "No saved presets yet. Save a preset from your session."
                color: theme.textSecondary; Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            SectionCard {
                title: "Saved presets"; symbol: "logs"; expanded: true
                Layout.fillWidth: true
                visible: true
                Repeater {
                    model: backend.presets
                    delegate: RowLayout {
                        id: presetRow
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Text { text: modelData.name; color: theme.textPrimary; Layout.fillWidth: true }
                        CustomButton { text: "Start"; enabled: !backend.isStreaming && !backend.sessionBusy; onClicked: backend.launchPreset(index) }
                        CustomButton {
                            text: "⋮"; primary: false; implicitWidth: 38
                            onClicked: presetMenu.open()
                            Menu {
                                id: presetMenu
                                popupType: Popup.Item
                                width: 200
                                background: Rectangle { color: theme.surface; border.color: theme.border; radius: 8 }
                                CardMenuItem {
                                    text: "Rename"
                                    onTriggered: {
                                        page.renameIndex = presetRow.index
                                        renameField.text = presetRow.modelData.name
                                        renameMessage.text = ""
                                        renamePopup.open()
                                    }
                                }
                                CardMenuItem { text: "Remove"; onTriggered: backend.deletePreset(presetRow.index) }
                            }
                        }
                    }
                }
            }
        }
    }
    Popup {
        id: renamePopup
        modal: true; anchors.centerIn: parent; width: 360; padding: 20
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: 14 }
        ColumnLayout {
            width: parent.width
            Text { text: "Rename preset"; color: theme.textPrimary; font.pixelSize: 18 }
            CustomTextField { id: renameField; Layout.fillWidth: true; maximumLength: 32 }
            Text { id: renameMessage; color: "#fca5a5"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                CustomButton { text: "Cancel"; primary: false; onClicked: renamePopup.close() }
                CustomButton {
                    text: "Save"
                    onClicked: {
                        let error = backend.renamePreset(page.renameIndex, renameField.text)
                        if (!error) renamePopup.close()
                        else renameMessage.text = error
                    }
                }
            }
        }
    }
}
