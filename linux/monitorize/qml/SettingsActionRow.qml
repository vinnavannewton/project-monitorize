import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: row
    property string title: ""
    property string description: ""
    property string symbol: "reset"
    property string actionText: ""
    property bool actionEnabled: true
    signal clicked()

    implicitHeight: Math.max(92, copy.implicitHeight + 32)
    color: theme.background
    border.color: theme.border
    radius: theme.cardRadius

    RowLayout {
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 16 }
        spacing: 16
        Rectangle {
            Layout.preferredWidth: 48; Layout.preferredHeight: 48
            Layout.alignment: Qt.AlignVCenter
            radius: 10
            color: theme.surfaceAlt
            LineIcon {
                anchors.centerIn: parent
                width: 30; height: 30
                symbol: row.symbol
                tint: row.actionEnabled ? theme.accent : theme.textMuted
            }
        }
        ColumnLayout {
            id: copy
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 6
            Text {
                text: row.title
                color: theme.textPrimary
                font.pixelSize: 14; font.weight: Font.DemiBold
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            Text {
                text: row.description
                color: theme.textSecondary
                font.pixelSize: 12
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
        }
        Button {
            id: action
            text: row.actionText
            enabled: row.actionEnabled
            Layout.preferredWidth: 140
            Layout.preferredHeight: 40
            Layout.alignment: Qt.AlignVCenter
            onClicked: row.clicked()
            background: Rectangle {
                radius: theme.controlRadius
                color: action.hovered && action.enabled ? theme.surfaceAlt : "transparent"
                border.color: action.enabled ? theme.accent : theme.border
            }
            contentItem: Text {
                text: action.text
                color: action.enabled ? theme.accent : theme.textMuted
                font.pixelSize: 12; font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
