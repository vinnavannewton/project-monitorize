import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: btn

    property bool primary: true
    property bool danger: false
    property string iconSymbol: ""
    property color iconTint: primary ? theme.buttonText : theme.accent
    implicitWidth: Math.max(120, contentItem.implicitWidth + 32)
    implicitHeight: Math.max(42, buttonLabel.implicitHeight + 16)

    background: Rectangle {
        implicitWidth: 120
        implicitHeight: 38
        color: !btn.enabled ? theme.surface : btn.danger ? (btn.hovered ? "#a53e49" : "#74303d") : btn.primary
            ? (btn.down ? theme.buttonBackgroundPressed : (btn.hovered ? theme.buttonBackgroundHover : theme.buttonBackground))
            : (btn.down ? theme.surfaceAlt : (btn.hovered ? theme.borderHover : theme.surface))
        border.color: btn.primary ? theme.border : (btn.hovered ? theme.borderHover : theme.border)
        border.width: 1
        radius: theme.controlRadius
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    contentItem: RowLayout {
        spacing: btn.iconSymbol.length > 0 ? 10 : 0
        LineIcon {
            visible: btn.iconSymbol.length > 0
            symbol: btn.iconSymbol
            tint: !btn.enabled ? theme.textMuted : btn.iconTint
            Layout.preferredWidth: 18
            Layout.preferredHeight: 20
        }
        Text {
            id: buttonLabel
            text: btn.text
            color: !btn.enabled ? theme.textMuted : (btn.primary ? theme.buttonText : theme.cardTextPrimary)
            font.pixelSize: 13
            font.weight: Font.DemiBold
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }
}
