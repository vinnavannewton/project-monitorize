import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    property string text: "WARNING"
    
    Layout.fillWidth: true
    Layout.preferredHeight: warningText.implicitHeight + 20
    radius: 8
    color: "#FFF3CD"
    border.color: "#FFEBAA"
    border.width: 1

    Text {
        id: warningText
        anchors.fill: parent
        anchors.margins: 10
        text: root.text
        color: "#856404"
        font.pixelSize: 12
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }
}
