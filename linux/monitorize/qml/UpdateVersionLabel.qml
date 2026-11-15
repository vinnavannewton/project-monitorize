import QtQuick

Text {
    id: label
    required property var status
    visible: text.length > 0
    wrapMode: Text.WordWrap
    text: status.state === "available" ? "Update available · " + status.installed + " → " + status.latest
        : status.state === "current" ? "Latest version · " + status.installed
        : status.installed ? "Installed version · " + status.installed : ""
    color: status.state === "available" ? theme.warningText
        : status.state === "current" ? theme.successText : theme.textSecondary
}
