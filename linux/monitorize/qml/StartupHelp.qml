import QtQuick
import QtQuick.Controls

ToolButton {
    id: control
    required property string helpText
    property bool hoverOnly: false
    hoverEnabled: true
    text: "?"
    Accessible.name: helpText
    ToolTip {
        objectName: "helpToolTip"
        visible: control.hovered || (control.hoverOnly && control.visualFocus)
        text: control.helpText
        x: control.width - width
        y: control.height + 6
        padding: 12
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.controlRadius }
        width: Math.min(360, control.Window.window ? control.Window.window.width - 32 : 360)
        contentItem: Text {
            text: control.helpText
            font: control.font
            color: theme.textPrimary
            wrapMode: Text.WordWrap
        }
    }
    onClicked: if (!hoverOnly) helpPopup.open()
    Popup {
        id: helpPopup
        objectName: "helpClickPopup"
        width: Math.min(360, control.Window.window ? control.Window.window.width - 32 : 360)
        x: 0
        y: control.height
        padding: 12
        contentItem: Text { text: control.helpText; color: theme.textPrimary; wrapMode: Text.WordWrap }
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.controlRadius }
    }
}
