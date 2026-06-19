import QtQuick
import QtQuick.Controls

CheckBox {
    id: chk
    indicator: Rectangle {
        implicitWidth: 16
        implicitHeight: 16
        x: chk.leftPadding
        y: parent.height / 2 - height / 2
        radius: 4
        color: chk.checked ? theme.accent : theme.surface
        border.color: chk.checked ? theme.accent : theme.border
        border.width: 1

        Rectangle {
            width: 8
            height: 8
            x: 4
            y: 4
            radius: 2
            color: "#ffffff"
            visible: chk.checked
        }
    }
    contentItem: Text {
        text: chk.text
        font.pixelSize: 12
        color: chk.hovered ? theme.textSecondary : theme.textMuted
        leftPadding: chk.indicator.width + chk.spacing
        wrapMode: Text.WordWrap
        verticalAlignment: Text.AlignVCenter
    }
}
