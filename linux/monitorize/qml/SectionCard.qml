import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    property string title: ""
    property string symbol: "display"
    property bool expanded: true
    default property alias content: body.data
    implicitHeight: contents.implicitHeight + 32
    radius: 14
    color: theme.surface
    opacity: enabled ? 1.0 : 0.4
    border.color: theme.border
    gradient: Gradient {
        GradientStop { position: 0; color: theme.surfaceAlt }
        GradientStop { position: 1; color: theme.surface }
    }
    ColumnLayout {
        id: contents
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
        spacing: 16
        AbstractButton {
            Layout.fillWidth: true
            implicitHeight: 28
            onClicked: card.expanded = !card.expanded
            Accessible.name: card.title
            contentItem: RowLayout {
                spacing: 12
                LineIcon { symbol: card.symbol; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                Text { text: card.title; color: "#b1d6ff"; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
                Text { text: card.expanded ? "⌃" : "⌄"; color: theme.textSecondary; font.pixelSize: 20 }
            }
        }
        ColumnLayout {
            id: body
            visible: card.expanded
            Layout.fillWidth: true
            spacing: 12
        }
    }
}
