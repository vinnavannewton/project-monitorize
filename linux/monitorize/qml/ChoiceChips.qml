import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: chips

    property var model: []
    property int currentIndex: 0
    property int chipWidth: 112
    readonly property string currentText: (
        currentIndex >= 0 && currentIndex < model.length ? model[currentIndex] : ""
    )

    signal activated(int index)

    spacing: 8
    Layout.fillWidth: true
    implicitHeight: 34

    function chipLabel(value) {
        if (value.indexOf("NVIDIA") === 0) return "NVIDIA"
        if (value.indexOf("Intel/AMD") === 0) return "VA-API"
        if (value.indexOf("Software") === 0) return "CPU"
        return value
    }

    function find(val) {
        if (!val) return -1
        for (let i = 0; i < model.length; i++) {
            if (model[i] === val) return i
        }
        return -1
    }

    function selectValue(val, exactMatchOnly=false) {
        if (!val) return false
        for (let i = 0; i < model.length; i++) {
            if (model[i] === val) {
                currentIndex = i
                return true
            }
        }
        if (exactMatchOnly) return false
        for (let j = 0; j < model.length; j++) {
            if (model[j].indexOf(val) === 0) {
                currentIndex = j
                return true
            }
        }
        return false
    }

    Repeater {
        model: chips.model

        Button {
            id: chip

            readonly property bool selected: index === chips.currentIndex

            text: chips.chipLabel(modelData)
            Layout.preferredWidth: chips.chipWidth
            Layout.preferredHeight: 34
            implicitWidth: Layout.preferredWidth
            implicitHeight: 34
            padding: 0
            onClicked: {
                chips.currentIndex = index
                chips.activated(index)
            }

            background: Rectangle {
                radius: theme.controlRadius
                color: chip.selected
                    ? (chip.hovered || chip.down ? theme.buttonBackgroundHover : theme.buttonBackground)
                    : (chip.down ? theme.surfaceAlt : (chip.hovered ? theme.borderHover : theme.surface))
                border.color: chip.selected
                    ? (chip.hovered || chip.down ? theme.buttonBackgroundHover : theme.buttonBackground)
                    : theme.border
                border.width: 1
                Behavior on color { ColorAnimation { duration: 120 } }
            }

            contentItem: Text {
                id: chipText
                text: chip.text
                color: chip.selected ? "#ffffff" : theme.cardTextPrimary
                font.pixelSize: 12
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }
}
