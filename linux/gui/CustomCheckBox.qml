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
        color: "#12142a"
        border.color: chk.checked ? "#4c4fd0" : "#2a2d55"
        border.width: 1

        Rectangle {
            width: 8
            height: 8
            x: 4
            y: 4
            radius: 2
            color: "#4c4fd0"
            visible: chk.checked
        }
    }
    contentItem: Text {
        text: chk.text
        font.pixelSize: 12
        color: chk.hovered ? "#9a9cc0" : "#5a5c82"
        leftPadding: chk.indicator.width + chk.spacing
        verticalAlignment: Text.AlignVCenter
    }
}
