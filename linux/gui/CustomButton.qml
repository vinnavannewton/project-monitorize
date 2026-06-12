import QtQuick
import QtQuick.Controls

Button {
    id: btn
    background: Rectangle {
        implicitWidth: 120
        implicitHeight: 38
        color: btn.down ? "#2a2c98" : (btn.hovered ? "#4042c8" : "#3538b0")
        border.color: "#2a2d55"
        border.width: 1
        radius: 8
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    contentItem: Text {
        text: btn.text
        color: "#ffffff"
        font.pixelSize: 13
        font.weight: Font.Bold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
