import QtQuick
import QtQuick.Controls

ComboBox {
    id: cb
    delegate: ItemDelegate {
        width: cb.width
        contentItem: Text {
            text: modelData
            color: highlighted ? "#ffffff" : "#c0c2ee"
            font.pixelSize: 13
            font.weight: Font.DemiBold
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: highlighted ? "#3538b0" : "#12142a"
        }
    }
    background: Rectangle {
        implicitWidth: 160
        implicitHeight: 38
        color: "#12142a"
        border.color: cb.hovered ? "#4c4fd0" : "#2a2d55"
        border.width: 1
        radius: 8
    }
    contentItem: Text {
        leftPadding: 12
        text: cb.displayText
        font.pixelSize: 13
        font.weight: Font.DemiBold
        color: "#c0c2ee"
        verticalAlignment: Text.AlignVCenter
    }
}
