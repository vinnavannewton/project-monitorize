import QtQuick
import QtQuick.Controls

TextField {
    id: tf
    placeholderTextColor: "#4a4c70"
    color: "#c0c2ee"
    font.pixelSize: 13
    font.weight: Font.DemiBold
    padding: 8
    background: Rectangle {
        implicitWidth: 80
        implicitHeight: 38
        color: "#12142a"
        border.color: tf.hovered ? "#4c4fd0" : "#2a2d55"
        border.width: 1
        radius: 8
    }
}
