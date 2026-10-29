import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: message
    property alias text: body.text
    property alias color: body.color
    property alias wrapMode: body.wrapMode
    visible: text.length > 0
    spacing: 8
    TextArea {
        id: body
        objectName: message.objectName + "Body"
        readOnly: true
        selectByMouse: true
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.Wrap
        padding: 0
        font.pixelSize: 12
        color: theme.textSecondary
        selectedTextColor: theme.buttonText
        selectionColor: theme.buttonBackground
        Layout.fillWidth: true
        background: Item {}
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.RightButton
            onClicked: contextMenu.popup()
        }
        Menu {
            id: contextMenu
            MenuItem { text: "Copy"; enabled: body.selectedText.length > 0; onTriggered: body.copy() }
            MenuItem { text: "Select all"; onTriggered: body.selectAll() }
        }
    }
    AbstractButton {
        id: copyMessage
        objectName: message.objectName + "Copy"
        implicitWidth: 30
        implicitHeight: 30
        Layout.alignment: Qt.AlignTop
        Accessible.name: "Copy message"
        ToolTip.visible: hovered
        ToolTip.text: "Copy message"
        background: Rectangle {
            radius: theme.controlRadius
            color: copyMessage.hovered ? theme.surfaceAlt : "transparent"
            border.width: copyMessage.visualFocus ? 1 : 0
            border.color: theme.borderHover
        }
        contentItem: Item {
            LineIcon { symbol: "copy"; width: 17; height: 17; anchors.centerIn: parent }
        }
        onClicked: backend.copySettingsMessage(message.text)
    }
}
