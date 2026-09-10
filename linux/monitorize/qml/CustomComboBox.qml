import QtQuick
import QtQuick.Controls

ComboBox {
    id: cb
    property int disabledIndex: -1
    delegate: ItemDelegate {
        width: cb.width
        enabled: index !== cb.disabledIndex
        contentItem: Text {
            text: modelData
            color: index === cb.disabledIndex ? theme.textMuted : (highlighted ? "#ffffff" : theme.cardTextPrimary)
            font.pixelSize: 13
            font.weight: Font.DemiBold
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: index !== cb.disabledIndex && highlighted ? theme.surfaceAlt : theme.surface
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
        rightPadding: 36
        text: cb.displayText
        font.pixelSize: 13
        font.weight: Font.DemiBold
        color: theme.cardTextPrimary
        verticalAlignment: Text.AlignVCenter
    }
    indicator: Text {
        x: cb.width - width - 12
        y: (cb.height - height) / 2
        text: "⌄"
        color: theme.textPrimary
        font.pixelSize: 20
        font.weight: Font.DemiBold
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
