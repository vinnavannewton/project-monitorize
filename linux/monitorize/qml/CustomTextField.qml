import QtQuick
import QtQuick.Controls

TextField {
    id: tf
    placeholderTextColor: theme.cardTextMuted
    color: tf.enabled ? theme.cardTextPrimary : theme.textMuted
    font.pixelSize: 13
    font.weight: Font.DemiBold
    padding: 8
    background: Rectangle {
        implicitWidth: 80
        implicitHeight: 38
        color: theme.surface
        border.color: tf.hovered ? theme.borderHover : theme.border
        border.width: 1
        radius: theme.controlRadius
    }
}
