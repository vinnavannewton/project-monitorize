import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card

    required property int displayId
    required property string modeResolution
    required property string customWidth
    required property string customHeight
    required property string refreshRate
    required property string customRefresh
    required property int displayNumber
    required property var displayConfig
    property bool canRemove: false
    property bool vkmsSelected: false
    property bool sunshineEnabled: true
    property var nativeResolutionOptions: []
    property var vkmsResolutionOptions: []
    property bool syncing: false

    signal configurationChanged(var configuration)
    signal removeRequested()

    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 32
    radius: 14
    color: theme.surface
    border.color: theme.border

    readonly property var resolutionOptions: vkmsSelected
        ? vkmsResolutionOptions : nativeResolutionOptions
    readonly property bool customSelected: resolution.currentText === "Custom..."
    readonly property bool customInputsVisible: customSelected

    function refreshLabel(value) {
        return String(value || "60") + " Hz"
    }

    function refreshValue() {
        return refresh.currentText === "Custom..."
            ? customRefresh.text : refresh.currentText.split(" ")[0]
    }

    function applyConfiguration() {
        if (!displayConfig) return
        syncing = true
        let requestedResolution = displayConfig.resolution || (vkmsSelected ? "" : "1920x1080")
        if (!resolution.selectValue(requestedResolution, true)) {
            if (vkmsSelected && /^\d+x\d+$/.test(requestedResolution))
                resolution.selectValue("Custom...", true)
            else if (vkmsSelected) resolution.currentIndex = -1
            else resolution.selectValue(requestedResolution)
        }
        let legacySize = /^\d+x\d+$/.test(requestedResolution)
            ? requestedResolution.split("x") : []
        if (!customWidth.activeFocus)
            customWidth.text = displayConfig.custom_w || legacySize[0] || "1920"
        if (!customHeight.activeFocus)
            customHeight.text = displayConfig.custom_h || legacySize[1] || "1080"
        let requestedRefresh = displayConfig.custom_fps
            ? "Custom..." : (vkmsSelected && !displayConfig.fps
                ? "" : refreshLabel(displayConfig.fps || "60"))
        if (!refresh.selectValue(requestedRefresh, true)) {
            if (vkmsSelected && Number(displayConfig.fps) > 0)
                refresh.selectValue("Custom...", true)
            else refresh.selectValue("60 Hz")
        }
        if (!customRefresh.activeFocus)
            customRefresh.text = displayConfig.custom_fps || displayConfig.fps || "60"
        syncing = false
    }

    function commit(changes) {
        if (syncing || !displayConfig) return
        let updated = Object.assign({}, displayConfig, changes)
        configurationChanged(updated)
    }

    function getCurrentMode() {
        return {
            resolution: resolution.currentText,
            custom_w: customSelected ? (customWidth.text.trim() || (displayConfig && displayConfig.custom_w) || "1920") : "",
            custom_h: customSelected ? (customHeight.text.trim() || (displayConfig && displayConfig.custom_h) || "1080") : "",
            fps: refreshValue(),
            custom_fps: refresh.currentText === "Custom..." ? customRefresh.text.trim() : ""
        }
    }

    function commitCurrentMode() {
        if (resolution.currentIndex < 0 || refresh.currentIndex < 0) return
        commit(getCurrentMode())
    }

    onDisplayConfigChanged: applyConfiguration()
    onResolutionOptionsChanged: applyConfiguration()
    onVkmsSelectedChanged: applyConfiguration()
    Component.onCompleted: applyConfiguration()

    ColumnLayout {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            LineIcon { symbol: "display"; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
            Text {
                text: "Virtual Display " + card.displayNumber
                color: "#b1d6ff"; font.pixelSize: 14; font.weight: Font.DemiBold
                Layout.fillWidth: true
            }
            AbstractButton {
                id: removeButton
                visible: card.canRemove
                implicitWidth: 36
                implicitHeight: 36
                Accessible.name: "Remove Virtual Display " + card.displayNumber
                ToolTip.visible: hovered
                ToolTip.text: Accessible.name
                background: Rectangle {
                    radius: theme.controlRadius
                    color: removeButton.hovered ? "#74303d" : "transparent"
                }
                LineIcon {
                    symbol: "trash"
                    tint: removeButton.hovered ? "#ffffff" : "#ff777f"
                    anchors.centerIn: parent
                    width: 22
                    height: 22
                }
                onClicked: card.removeRequested()
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 16
            rowSpacing: 8

            Text { text: "Resolution"; color: theme.textSecondary; Layout.fillWidth: true }
            Text {
                text: "Refresh Rate"
                color: theme.textSecondary
                Layout.fillWidth: true
            }
            CustomComboBox {
                id: resolution
                Layout.fillWidth: true
                model: card.resolutionOptions
                displayText: currentIndex < 0 ? "Select an available mode" : currentText
                onActivated: {
                    if (card.syncing) return
                    refresh.selectValue("60 Hz", true)
                    card.commitCurrentMode()
                }
            }
            CustomComboBox {
                id: refresh
                Layout.fillWidth: true
                model: ["30 Hz", "60 Hz", "75 Hz", "90 Hz", "120 Hz", "144 Hz", "Custom..."]
                enabled: count > 0
                opacity: enabled ? 1 : 0.45
                displayText: currentIndex < 0 ? "Select an available rate" : currentText
                onActivated: card.commitCurrentMode()
            }
            RowLayout {
                visible: card.customInputsVisible
                Layout.fillWidth: true
                CustomTextField {
                    id: customWidth
                    Layout.fillWidth: true
                    placeholderText: "Width"; maximumLength: 4
                    onEditingFinished: card.commitCurrentMode()
                }
                Text { text: "×"; color: theme.textSecondary }
                CustomTextField {
                    id: customHeight
                    Layout.fillWidth: true
                    placeholderText: "Height"; maximumLength: 4
                    onEditingFinished: card.commitCurrentMode()
                }
            }
            CustomTextField {
                id: customRefresh
                visible: refresh.currentText === "Custom..."
                Layout.fillWidth: true
                placeholderText: "24–240"; maximumLength: 3
                onEditingFinished: card.commitCurrentMode()
            }
        }
        SunshineDisplayCard {
            instance: card.displayNumber
            Layout.fillWidth: true
            enabled: card.sunshineEnabled
        }
    }
}
