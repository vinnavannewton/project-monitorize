import QtQuick
import QtQuick.Controls

MenuItem {
    id: control
    implicitHeight: 40
    contentItem: Text {
        text: control.text
        color: control.enabled ? theme.textPrimary : theme.textMuted
        font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter
        leftPadding: 12
    }
    background: Rectangle {
        radius: 6
        color: control.highlighted ? theme.surfaceAlt : "transparent"
    }
}
