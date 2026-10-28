import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    property string title: ""
    property string symbol: "display"
    property bool expanded: true
    property Component headerAction: null
    default property alias content: body.data
    implicitHeight: contents.implicitHeight + 32
    radius: theme.cardRadius
    color: theme.surface
    opacity: enabled ? 1.0 : 0.4
    border.color: theme.border
    ColumnLayout {
        id: contents
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            AbstractButton {
                Layout.fillWidth: true
                implicitHeight: 28
                onClicked: card.expanded = !card.expanded
                Accessible.name: card.title
                contentItem: RowLayout {
                    spacing: 12
                    LineIcon { symbol: card.symbol; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                    Text { text: card.title; color: theme.headingText; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    Text { text: card.expanded ? "⌃" : "⌄"; color: theme.textPrimary; font.pixelSize: 20 }
                }
            }
            Loader {
                sourceComponent: card.headerAction
                visible: active
                active: card.headerAction !== null
                Layout.preferredWidth: item ? item.implicitWidth : 0
                Layout.preferredHeight: item ? item.implicitHeight : 0
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
