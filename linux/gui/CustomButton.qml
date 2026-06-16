import QtQuick
import QtQuick.Controls

Button {
    id: btn
    background: Rectangle {
        implicitWidth: 120
        implicitHeight: 38
        color: btn.down ? theme.buttonBackgroundPressed : (btn.hovered ? theme.buttonBackgroundHover : theme.buttonBackground)
        border.color: theme.border
        border.width: 1
        radius: 8
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    contentItem: Text {
        text: btn.text
        color: theme.buttonText
        font.pixelSize: 13
        font.weight: Font.Bold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
