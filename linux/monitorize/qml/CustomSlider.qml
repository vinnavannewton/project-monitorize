import QtQuick
import QtQuick.Controls

Slider {
    id: slider

    hoverEnabled: true

    background: Rectangle {
        x: slider.leftPadding
        y: slider.topPadding + slider.availableHeight / 2 - height / 2
        implicitWidth: 200
        implicitHeight: 4
        width: slider.availableWidth
        height: implicitHeight
        radius: height / 2
        color: theme.border

        Rectangle {
            width: slider.visualPosition * parent.width
            height: parent.height
            radius: height / 2
            color: slider.hovered || slider.pressed ? theme.buttonBackgroundHover : theme.buttonBackground
            Behavior on color { ColorAnimation { duration: 120 } }
        }
    }

    handle: Rectangle {
        x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
        y: slider.topPadding + slider.availableHeight / 2 - height / 2
        implicitWidth: 18
        implicitHeight: 18
        radius: width / 2
        color: slider.hovered || slider.pressed ? theme.buttonBackgroundHover : theme.buttonBackground
        border.color: theme.surfaceAlt
        border.width: 2
        Behavior on color { ColorAnimation { duration: 120 } }
    }
}
