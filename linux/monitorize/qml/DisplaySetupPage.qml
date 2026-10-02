import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string returnPageSource: "DisplaySetupPage.qml"
    property bool loading: true
    property var mirrorOutputs: []
    property string mirrorOutputId: ""
    property var nativeResolutionOptions: ["1280x720 (16:9)", "1280x800 (16:10)", "1920x1080 (16:9)", "1920x1200 (16:10)", "2560x1440 (16:9)", "2560x1600 (16:10)", "3840x2160 (16:9)", "Custom..."]
    property var virtualDisplays: []
    readonly property bool vkmsSelected: displayType.currentText === "Extend"
        && displayCreator.currentText === "VKMS (Experimental)"

    function mirrorResolutionLabel() {
        for (let i = 0; i < mirrorOutputs.length; ++i) {
            if (mirrorOutputs[i].id === mirrorOutputId
                    && mirrorOutputs[i].native_width > 0
                    && mirrorOutputs[i].native_height > 0) {
                return mirrorOutputs[i].native_width + "x"
                    + mirrorOutputs[i].native_height + " (display native)"
            }
        }
        return "Display native resolution"
    }

    function refreshMirrorOutputs() {
        mirrorOutputs = backend.getMirrorOutputs()
        let labels = ["Select a monitor…"]
        let selected = 0
        for (let i = 0; i < mirrorOutputs.length; ++i) {
            labels.push(mirrorOutputs[i].label)
            if (mirrorOutputs[i].id === mirrorOutputId) selected = i + 1
        }
        if (!mirrorOutputId && mirrorOutputs.length === 1) {
            mirrorOutputId = mirrorOutputs[0].id
            selected = 1
        }
        if (mirrorOutputId && selected === 0) {
            labels[0] = "Unavailable: " + mirrorOutputId
        }
        mirrorMonitor.model = labels
        mirrorMonitor.currentIndex = selected
    }

    Timer {
        interval: 2000; repeat: true; running: !page.loading && !backend.isStreaming
        onTriggered: page.refreshMirrorOutputs()
    }

    function primaryDisplay() {
        return virtualDisplays.length > 0 ? virtualDisplays[0] : {
            id: 1, resolution: vkmsSelected ? "" : "1920x1080",
            custom_w: "", custom_h: "", fps: vkmsSelected ? "" : "60",
            custom_fps: ""
        }
    }

    function saveDisplayModes() {
        if (!loading && virtualDisplays.length > 0)
            backend.saveVirtualDisplaySettings(virtualDisplays)
    }

    function updateDisplay(index, configuration) {
        if (index < 0 || index >= virtualDisplays.length) return
        let updated = virtualDisplays.slice()
        updated[index] = configuration
        virtualDisplays = updated
        if (index === 0) {
            page.saveSettings()
        } else {
            saveDisplayModes()
        }
    }

    function sameDisplayMode(left, right) {
        if (!left || !right) return false
        let keys = ["resolution", "custom_w", "custom_h", "fps", "custom_fps"]
        for (let i = 0; i < keys.length; ++i) {
            let key = keys[i]
            if (String(left[key] || "") !== String(right[key] || "")) return false
        }
        return true
    }

    function commitAllPendingDisplaySettings() {
        if (loading) return
        let updated = virtualDisplays.slice()
        let hasChanges = false
        for (let i = 0; i < displayRepeater.count; ++i) {
            let card = displayRepeater.itemAt(i)
            if (!card || typeof card.getCurrentMode !== "function" || !card.displayConfig) {
                console.warn("DisplaySetupPage: skipping uninitialized display card at index " + i)
                continue
            }
            let current = card.getCurrentMode()
            if (current && current.resolution && updated[i]
                    && !page.sameDisplayMode(current, updated[i])) {
                updated[i] = Object.assign({}, updated[i], current)
                hasChanges = true
            }
        }
        if (hasChanges) {
            virtualDisplays = updated
            page.saveSettings()
        }
    }

    function addDisplay() {
        if (vkmsSelected || virtualDisplays.length >= 2) return
        let duplicate = Object.assign({}, primaryDisplay(), { id: 2 })
        virtualDisplays = [primaryDisplay(), duplicate]
        saveDisplayModes()
    }

    function removeDisplay() {
        if (virtualDisplays.length < 2) return
        virtualDisplays = [primaryDisplay()]
        saveDisplayModes()
    }

    function saveSettings() {
        if (loading) return
        let primary = primaryDisplay()
        let saved = backend.loadDisplaySettings()
        backend.saveDisplaySettings(
            primary.resolution,
            primary.custom_w,
            primary.custom_h,
            primary.fps,
            primary.custom_fps,
            displayType.currentText,
            saved["sunshine_encoder"],
            saved["sunshine_gpu"],
            saved["sunshine_codec"],
            saved["streaming_customized"],
            saved["sunshine_native_pen_touch"],
            saved["enable_audio"],
            page.mirrorOutputId,
            displayCreator.currentText === "VKMS (Experimental)" ? "vkms" : "native"
        )
        saveDisplayModes()
    }

    Component.onCompleted: {
        backend.refreshVkmsHelperAvailability()
        let saved = backend.loadDisplaySettings()
        displayType.selectValue(saved["display_type"] || "Extend")
        displayCreator.selectValue(
            backend.vkmsCreatorAvailable && saved["virtual_display_creator"] === "vkms"
                ? "VKMS (Experimental)"
                : "Compositor"
        )
        virtualDisplays = backend.loadVirtualDisplaySettings()
        mirrorOutputId = saved["mirror_output"] || ""
        refreshMirrorOutputs()
        createOnly.checked = backend.streamingBackend === "none"
        loading = false
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 14
            Text { text: "Configuration"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold; Layout.bottomMargin: 6 }
            Text {
                visible: backend.isStreaming
                text: "Stop the session to change display configuration."
                color: theme.textMuted
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            SectionCard {
                title: "DISPLAY"; symbol: "display"
                Layout.fillWidth: true
                enabled: !backend.isStreaming
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2; columnSpacing: 24; rowSpacing: 12
                    Text { text: "Mode"; color: theme.textSecondary; Layout.preferredWidth: 145 }
                    ChoiceChips {
                        id: displayType; model: ["Extend", "Mirror"]; chipWidth: 124
                        disabledValues: createOnly.checked || !backend.sunshineAvailable ? ["Mirror"] : []
                        onActivated: page.saveSettings()
                    }
                    Text {
                        text: "Virtual Display Creator"
                        color: theme.textSecondary
                        visible: displayType.currentText === "Extend" && backend.vkmsCreatorAvailable
                    }
                    CustomComboBox {
                        id: displayCreator
                        Layout.fillWidth: true
                        visible: displayType.currentText === "Extend" && backend.vkmsCreatorAvailable
                        model: backend.vkmsCreatorAvailable
                            ? ["Compositor", "VKMS (Experimental)"]
                            : ["Compositor"]
                        onActivated: {
                            if (page.vkmsSelected) {
                                page.saveSettings()
                            } else {
                                backend.ensureNativeCompositor()
                                page.saveSettings()
                            }
                        }
                    }
                    Text {
                        visible: page.vkmsSelected && !backend.vkmsHelperAvailable
                        Layout.columnSpan: 2; Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: "#ff9a9a"
                        text: "Install monitorize-vkms before using VKMS displays."
                    }
                    CustomButton {
                        visible: page.vkmsSelected && !backend.vkmsHelperAvailable
                        text: "Install monitorize-vkms"
                        onClicked: backend.openMonitorizeVkmsInstallPage()
                    }
                    CustomButton {
                        visible: page.vkmsSelected && !backend.vkmsHelperAvailable
                        text: "Recheck"
                        primary: false
                        onClicked: backend.refreshVkmsHelperAvailability()
                    }
                    Text {
                        visible: page.vkmsSelected
                        Layout.columnSpan: 2; Layout.fillWidth: true
                        wrapMode: Text.WordWrap; color: theme.textMuted
                        text: "Preset and custom VKMS modes use monitorize-vkms. Adding another display is unavailable in VKMS mode."
                    }
                    Text { text: "Monitor"; color: theme.textSecondary; visible: displayType.currentText === "Mirror" }
                    CustomComboBox {
                        id: mirrorMonitor
                        visible: displayType.currentText === "Mirror"
                        Layout.fillWidth: true
                        disabledIndex: 0
                        onActivated: {
                            page.mirrorOutputId = currentIndex > 0 ? page.mirrorOutputs[currentIndex - 1].id : ""
                            page.saveSettings()
                        }
                    }
                    Text {
                        visible: displayType.currentText === "Mirror"
                        Layout.columnSpan: 2; Layout.fillWidth: true
                        wrapMode: Text.WordWrap; color: theme.textMuted
                        text: "If the desktop asks what to share, select this same monitor. A missing or different monitor will not be substituted."
                    }
                }
                Text {
                    visible: displayType.currentText !== "Mirror"
                    text: "Each virtual display keeps its own resolution and refresh rate."
                    color: theme.textMuted; font.pixelSize: 12
                }
                Repeater {
                    id: displayRepeater
                    model: displayType.currentText === "Extend" ? page.virtualDisplays : []
                    delegate: VirtualDisplayModeCard {
                        id: modeCard
                        required property int index
                        Layout.fillWidth: true
                        displayNumber: Number(modelData.id)
                        displayConfig: modelData
                        canRemove: Number(modelData.id) === 2
                        vkmsSelected: page.vkmsSelected
                        nativeResolutionOptions: page.nativeResolutionOptions
                        vkmsResolutionOptions: backend.vkmsResolutionOptions
                        sunshineEnabled: !backend.isStreaming && !backend.sessionBusy
                            && !createOnly.checked && backend.sunshineAvailable
                        onConfigurationChanged: function(configuration) {
                            let targetIndex = modeCard.displayNumber > 0 ? (modeCard.displayNumber - 1) : modeCard.index
                            page.updateDisplay(targetIndex, configuration)
                        }
                        onRemoveRequested: page.removeDisplay()
                    }
                }
                AbstractButton {
                    id: addDisplayButton
                    visible: displayType.currentText === "Extend" && page.virtualDisplays.length < 2
                    enabled: !page.vkmsSelected
                    opacity: enabled ? 1.0 : 0.4
                    Layout.fillWidth: true
                    implicitHeight: 54
                    onClicked: page.addDisplay()
                    background: Rectangle {
                        radius: theme.controlRadius
                        color: addDisplayButton.enabled && addDisplayButton.hovered ? theme.surfaceAlt : "transparent"
                        border.color: theme.borderHover
                    }
                    contentItem: RowLayout {
                        anchors { left: parent.left; right: parent.right; margins: 14 }
                        spacing: 10
                        LineIcon { symbol: "plus"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                        Text { text: "Add Display"; color: "#94caff"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    }
                }
            }
            SectionCard {
                title: "SUNSHINE · MIRROR"; symbol: "streaming"
                Layout.fillWidth: true
                visible: displayType.currentText === "Mirror"
                enabled: !backend.isStreaming && !backend.sessionBusy && !createOnly.checked && backend.sunshineAvailable
                SunshineDisplayCard {
                    instance: 1
                    showHeading: false
                    Layout.fillWidth: true
                }
            }
            SectionCard {
                title: "ADVANCED"; symbol: "settings"; expanded: false
                Layout.fillWidth: true
                enabled: !backend.isStreaming
                RowLayout {
                    Layout.fillWidth: true
                    CustomCheckBox {
                        id: createOnly; text: "Create virtual display only"
                        enabled: backend.sunshineAvailable; Layout.fillWidth: true
                        onCheckedChanged: {
                            if (page.loading) return
                            if (checked && displayType.currentText === "Mirror") displayType.selectValue("Extend")
                            backend.setStreamingBackend(checked ? "none" : "sunshine")
                            page.saveSettings()
                        }
                    }
                    CustomButton { text: "?"; primary: false; implicitWidth: 32; onClicked: virtualOnlyHint.open() }
                }
            }
        }
    }
    Popup {
        id: virtualOnlyHint; parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(390, parent.width - 40); padding: 18; focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: theme.surface; border.color: theme.borderHover; radius: theme.cardRadius }
        contentItem: Text {
            text: "Creates the virtual display without starting Monitorize’s streaming backend. Use your preferred streamer instead."
            color: theme.textSecondary; font.pixelSize: 12; wrapMode: Text.WordWrap
        }
    }
}
