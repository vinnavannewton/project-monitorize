import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: notice
    property string message: "Monitorize VKMS not installed"
    property string description: ""
    property string installText: "Install"
    signal installRequested()
    spacing: 10
    Text {
        text: notice.message
        color: theme.textSecondary
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Text {
        text: notice.description
        visible: text.length > 0
        color: theme.textSecondary
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    CustomButton {
        objectName: "vkmsInstallButton"
        text: notice.installText
        onClicked: notice.installRequested()
    }
}
