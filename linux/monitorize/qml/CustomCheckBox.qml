import QtQuick
import QtQuick.Controls

CheckBox {
    id: chk
    hoverEnabled: true

    indicator: Rectangle {
        implicitWidth: 16
        implicitHeight: 16
        x: chk.leftPadding
        y: parent.height / 2 - height / 2
        radius: 4
        color: chk.checked
            ? (chk.hovered || chk.down ? theme.buttonBackgroundHover : theme.buttonBackground)
            : (chk.hovered || chk.down ? theme.surfaceAlt : theme.surface)
        border.color: chk.checked
            ? (chk.hovered || chk.down ? theme.buttonBackgroundHover : theme.buttonBackground)
            : (chk.hovered || chk.down ? theme.borderHover : theme.border)
        border.width: 1

        Text {
            anchors.centerIn: parent
            text: "✓"
            color: "#ffffff"
            font.pixelSize: 11
            font.weight: Font.Bold
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
