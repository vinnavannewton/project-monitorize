import QtQuick
import QtQuick.Controls

ComboBox {
    id: cb
    delegate: ItemDelegate {
        width: cb.width
        contentItem: Text {
            text: modelData
            color: highlighted ? "#ffffff" : theme.cardTextPrimary
            font.pixelSize: 13
            font.weight: Font.DemiBold
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: highlighted ? theme.surfaceAlt : theme.surface
        }
    }
    background: Rectangle {
        implicitWidth: 160
        implicitHeight: 38
        color: theme.surface
        border.color: cb.hovered ? theme.borderHover : theme.border
        border.width: 1
        radius: theme.controlRadius
    }
    contentItem: Text {
        leftPadding: 12
        text: cb.displayText
        font.pixelSize: 13
        font.weight: Font.DemiBold
        color: theme.cardTextPrimary
        verticalAlignment: Text.AlignVCenter
    }

    function selectValue(val, exactMatchOnly=false) {
        if (!val) return false;
        let exactIdx = cb.find(val);
        if (exactIdx !== -1) {
            cb.currentIndex = exactIdx;
            return true;
        }
        if (exactMatchOnly) return false;
        for (let i = 0; i < cb.count; i++) {
            if (cb.textAt(i).indexOf(val) === 0) {
                cb.currentIndex = i;
                return true;
            }
        }
        return false;
    }
}
