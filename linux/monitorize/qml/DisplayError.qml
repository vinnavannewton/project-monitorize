import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: errorRow
    required property int instance
    readonly property string message: {
        let revision = backend.sessionError
        return backend.displayError(instance)
    }
    visible: message.length > 0
    spacing: 8
    TextArea {
        objectName: "displayErrorText" + errorRow.instance
        text: errorRow.message
        textFormat: TextEdit.PlainText
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.Wrap
        padding: 0
        color: theme.errorText
        font.pixelSize: 12
        Layout.fillWidth: true
        background: Item {}
    }
    AbstractButton {
        id: copyError
        objectName: "copyDisplayError" + errorRow.instance
        implicitWidth: 30; implicitHeight: 30
        Layout.alignment: Qt.AlignTop
        Accessible.name: "Copy display " + errorRow.instance + " error"
        ToolTip.visible: hovered
        ToolTip.text: "Copy error"
        background: Rectangle {
            radius: theme.controlRadius
            color: copyError.hovered ? theme.surfaceAlt : "transparent"
            border.width: copyError.visualFocus ? 1 : 0
            border.color: theme.borderHover
        }
        contentItem: Item {
            LineIcon { symbol: "copy"; width: 17; height: 17; anchors.centerIn: parent }
        }
        onClicked: backend.copyDisplayError(errorRow.instance)
    }
}
