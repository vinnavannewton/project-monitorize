import QtQuick
import QtQuick.Controls

Button {
    id: btn

    property bool primary: true
    property bool danger: false
    property string iconSymbol: ""
    implicitWidth: Math.max(120, contentItem.implicitWidth + 32)
    implicitHeight: 42

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
    contentItem: Item {
        implicitWidth: buttonContents.implicitWidth
        implicitHeight: buttonContents.implicitHeight
        Row {
            id: buttonContents
            spacing: btn.iconSymbol.length > 0 ? 10 : 0
            anchors.centerIn: parent
            LineIcon {
                visible: btn.iconSymbol.length > 0
                symbol: btn.iconSymbol
                tint: !btn.enabled ? theme.textMuted : (btn.primary ? theme.buttonText : theme.accent)
                width: visible ? 18 : 0
                height: 20
            }
            Text {
                text: btn.text
                color: !btn.enabled ? theme.textMuted : (btn.primary ? theme.buttonText : theme.cardTextPrimary)
                font.pixelSize: 13
                font.weight: Font.DemiBold
                height: 20
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
