import QtQuick
import QtQuick.Controls

Button {
    id: btn

    property bool primary: true

    scale: btn.hovered ? theme.hoverScale : 1.0
    Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }

    background: Rectangle {
        implicitWidth: 120
        implicitHeight: 38
        color: btn.primary
            ? (btn.down ? theme.buttonBackgroundPressed : (btn.hovered ? theme.buttonBackgroundHover : theme.buttonBackground))
            : (btn.down ? theme.surfaceAlt : (btn.hovered ? theme.borderHover : theme.surface))
        border.color: btn.primary ? theme.border : (btn.hovered ? theme.borderHover : theme.border)
        border.width: 1
        radius: theme.controlRadius
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    contentItem: Text {
        text: btn.text
        color: btn.primary ? theme.buttonText : theme.cardTextPrimary
        font.pixelSize: 13
        font.weight: Font.Bold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
