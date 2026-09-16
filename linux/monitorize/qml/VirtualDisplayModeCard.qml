import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card

    required property int displayNumber
    required property var displayConfig
    property bool canRemove: false
    property bool vkmsSelected: false
    property var nativeResolutionOptions: []
    property var vkmsResolutionOptions: []
    property bool vkmsCustomCapabilityChecking: false
    property string vkmsCustomEdidCapability: "unknown"
    property bool customModeActive: false
    property bool syncing: false

    signal configurationChanged(var configuration)
    signal removeRequested()
    signal customCapabilityFailed(string capability)

    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 32
    radius: 14
    color: theme.surface
    border.color: theme.border
    gradient: Gradient {
        GradientStop { position: 0; color: theme.surfaceAlt }
        GradientStop { position: 1; color: theme.surface }
    }

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

    function firstNormalResolution() {
        for (let i = 0; i < resolutionOptions.length; ++i) {
            if (resolutionOptions[i] !== "Custom...") return resolutionOptions[i]
        }
        return ""
    }

    function applyConfiguration() {
        if (!displayConfig) return
        syncing = true
        let requestedResolution = displayConfig.resolution || "1920x1080"
        if (!resolution.selectValue(requestedResolution, true))
            resolution.selectValue(requestedResolution)
        if (!customWidth.activeFocus)
            customWidth.text = displayConfig.custom_w || "1920"
        if (!customHeight.activeFocus)
            customHeight.text = displayConfig.custom_h || "1080"
        let requestedRefresh = displayConfig.custom_fps
            ? "Custom..." : refreshLabel(displayConfig.fps || "60")
        refresh.selectValue(requestedRefresh, true)
        if (refresh.currentIndex < 0) refresh.selectValue("60 Hz")
        if (!customRefresh.activeFocus)
            customRefresh.text = displayConfig.custom_fps || "60"
        customModeActive = !vkmsSelected || (customSelected && vkmsCustomEdidCapability !== "unsupported")
        syncing = false
        checkCustomMode()
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
        commit(getCurrentMode())
    }

    function checkCustomMode() {
        if (!vkmsSelected || !customSelected) return
        if (vkmsCustomEdidCapability === "unsupported") {
            customModeActive = false
            restoreNormalMode()
            customCapabilityFailed(vkmsCustomEdidCapability)
            return
        }
        customModeActive = true
        if (vkmsCustomEdidCapability === "unknown") backend.checkVkmsCustomEdidSupport()
    }

    function restoreNormalMode() {
        let fallback = firstNormalResolution()
        if (!fallback) return
        syncing = true
        resolution.selectValue(fallback, true)
        refresh.selectValue("60 Hz", true)
        syncing = false
        commitCurrentMode()
    }

    onDisplayConfigChanged: applyConfiguration()
    onResolutionOptionsChanged: applyConfiguration()
    onVkmsSelectedChanged: applyConfiguration()
    onVkmsCustomEdidCapabilityChanged: {
        if (!vkmsSelected || !customSelected) return
        if (vkmsCustomEdidCapability === "unsupported") {
            customModeActive = false
            restoreNormalMode()
            customCapabilityFailed(vkmsCustomEdidCapability)
            return
        }
        customModeActive = true
        commitCurrentMode()
    }
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
            CustomButton {
                visible: card.canRemove
                text: "Remove"
                primary: false
                implicitHeight: 32
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
                color: card.vkmsSelected && !card.customModeActive ? theme.textMuted : theme.textSecondary
                Layout.fillWidth: true
            }
            CustomComboBox {
                id: resolution
                Layout.fillWidth: true
                enabled: !card.vkmsCustomCapabilityChecking
                opacity: enabled ? 1 : 0.45
                model: card.resolutionOptions
                onActivated: {
                    if (card.syncing) return
                    if (card.vkmsSelected && currentText === "Custom...") {
                        card.checkCustomMode()
                    } else {
                        card.customModeActive = !card.vkmsSelected
                    }
                    card.commitCurrentMode()
                }
            }
            CustomComboBox {
                id: refresh
                Layout.fillWidth: true
                model: ["30 Hz", "60 Hz", "75 Hz", "90 Hz", "120 Hz", "144 Hz", "Custom..."]
                enabled: !card.vkmsSelected || card.customModeActive
                opacity: enabled ? 1 : 0.45
                displayText: card.vkmsSelected && !card.customModeActive
                    ? "~60 Hz — Managed by VKMS" : currentText
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
                visible: refresh.currentText === "Custom..." && (!card.vkmsSelected || card.customModeActive)
                Layout.fillWidth: true
                placeholderText: "24–240"; maximumLength: 3
                onEditingFinished: card.commitCurrentMode()
            }
        }
        Text {
            visible: card.vkmsSelected && card.vkmsCustomCapabilityChecking
            text: "Checking custom VKMS resolution support…"
            color: theme.textMuted; font.pixelSize: 12
        }
    }
}
