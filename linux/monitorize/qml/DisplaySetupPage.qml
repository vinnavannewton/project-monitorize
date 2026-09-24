import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string returnPageSource: "DisplaySetupPage.qml"
    property bool loading: true
    property var gpuOptions: []
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
    readonly property bool streamingCustomized: streamingMode.currentIndex === 1

    function primaryDisplay() {
        return virtualDisplays.length > 0 ? virtualDisplays[0] : {
            id: 1, resolution: "1920x1080", custom_w: "", custom_h: "",
            fps: "60", custom_fps: ""
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
            let item = displayRepeater.itemAt(i)
            if (!item || typeof item.getCurrentMode !== "function" || !item.displayConfig) {
                console.warn("DisplaySetupPage: skipping uninitialized display card at index " + i)
                continue
            }
            let current = item.getCurrentMode()
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

    function normalizeVkmsModes() {
        if (!vkmsSelected) return
        let options = backend.vkmsResolutionOptions
        let fallback = ""
        for (let i = 0; i < options.length; ++i) {
            if (options[i] !== "Custom...") { fallback = options[i]; break }
        }
        if (!fallback) return
        let updated = virtualDisplays.slice()
        let changed = false
        for (let i = 0; i < updated.length; ++i) {
            if (updated[i].resolution !== "Custom..." && options.indexOf(updated[i].resolution) === -1) {
                updated[i] = Object.assign({}, updated[i], {
                    resolution: fallback, custom_w: "", custom_h: "", fps: "60", custom_fps: ""
                })
                changed = true
            }
        }
        if (changed) virtualDisplays = updated
    }

    function encoderDisplayValue(value) {
        return String(value || "").toLowerCase().indexOf("software") === 0
            ? "Software"
            : (value || "Auto")
    }

    function codecDisplayValue(value) {
        let normalized = String(value || "").toLowerCase()
        if (normalized.indexOf("h.264") !== -1 || normalized === "h264" || normalized.indexOf("avc") !== -1) return "H.264"
        if (normalized.indexOf("h.265") !== -1 || normalized === "h265" || normalized.indexOf("hevc") !== -1) return "HEVC"
        if (normalized.indexOf("av1") !== -1) return "AV1"
        return "Auto"
    }

    function selectedGpuId() {
        if (gpuCombo.currentIndex < 0 || gpuCombo.currentIndex >= gpuOptions.length) return ""
        return gpuOptions[gpuCombo.currentIndex]["id"] || ""
    }

    function refreshGpuOptions(savedId) {
        gpuOptions = backend.getEncodingGpuOptions(encoder.currentText)
        let labels = []
        let selected = 0
        for (let i = 0; i < gpuOptions.length; i++) {
            labels.push(gpuOptions[i]["label"])
            if (savedId && gpuOptions[i]["id"] === savedId) selected = i
        }
        gpuCombo.model = labels
        gpuCombo.currentIndex = labels.length > 0 ? selected : -1
    }

    function saveSettings() {
        if (loading) return
        let primary = primaryDisplay()
        backend.saveDisplaySettings(
            primary.resolution,
            primary.custom_w,
            primary.custom_h,
            primary.fps,
            primary.custom_fps,
            displayType.currentText,
            encoder.currentText,
            page.selectedGpuId(),
            codec.currentText,
            page.streamingCustomized,
            nativeInput.checked,
            audio.checked,
            page.mirrorOutputId,
            displayCreator.currentText === "VKMS (Experimental)" ? "vkms" : "native"
        )
        saveDisplayModes()
    }

    function selectAutomaticStreaming() {
        backend.setSunshineEncoder("Auto")
        backend.setSunshineCodec("Auto")
        page.saveSettings()
    }

    Component.onCompleted: {
        let saved = backend.loadDisplaySettings()
        displayType.selectValue(saved["display_type"] || "Extend")
        displayCreator.selectValue(
            backend.vkmsCreatorAvailable && saved["virtual_display_creator"] === "vkms"
                ? "VKMS (Experimental)"
                : "Compositor"
        )
        backend.refreshVkmsResolutionOptions()
        virtualDisplays = backend.loadVirtualDisplaySettings()
        encoder.selectValue(page.encoderDisplayValue(saved["sunshine_encoder"]))
        page.refreshGpuOptions(saved["sunshine_gpu"] || "")
        codec.selectValue(page.codecDisplayValue(saved["sunshine_codec"]))
        streamingMode.selectValue(
            saved["streaming_customized"] === true
                ? "Customize ›"
                : "Automatic (Recommended)"
        )
        nativeInput.checked = saved["sunshine_native_pen_touch"] !== false
        mirrorOutputId = saved["mirror_output"] || ""
        refreshMirrorOutputs()
        audio.checked = saved["enable_audio"] === true
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
                                backend.refreshVkmsResolutionOptions()
                                page.normalizeVkmsModes()
                            } else {
                                backend.ensureNativeCompositor()
                            }
                            page.saveSettings()
                        }
                    }
                    Text {
                        visible: page.vkmsSelected
                        Layout.columnSpan: 2; Layout.fillWidth: true
                        wrapMode: Text.WordWrap; color: theme.textMuted
                        text: "Creates a display using Linux's experimental VKMS path. Adding another display is unavailable in VKMS mode. Display layout and positioning are managed by your desktop environment."
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
                        Layout.fillWidth: true
                        displayNumber: Number(modelData.id)
                        displayConfig: modelData
                        canRemove: Number(modelData.id) === 2
                        vkmsSelected: page.vkmsSelected
                        nativeResolutionOptions: page.nativeResolutionOptions
                        vkmsResolutionOptions: backend.vkmsResolutionOptions
                        vkmsCustomCapabilityChecking: backend.vkmsCustomCapabilityChecking
                        vkmsCustomEdidCapability: backend.vkmsCustomEdidCapability
                        onConfigurationChanged: function(configuration) {
                            let targetIndex = modeCard.displayNumber > 0 ? (modeCard.displayNumber - 1) : index
                            page.updateDisplay(targetIndex, configuration)
                        }
                        onRemoveRequested: page.removeDisplay()
                        onCustomCapabilityFailed: function(capability) {
                            if (capability === "unsupported") vkmsCustomUnsupported.open()
                            else vkmsCustomCheckFailed.open()
                        }
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
                title: "STREAMING"; symbol: "streaming"
                Layout.fillWidth: true
                enabled: !backend.isStreaming && !backend.sessionBusy && !createOnly.checked && backend.sunshineAvailable
                CustomComboBox {
                    id: streamingMode; Layout.fillWidth: true
                    model: ["Automatic (Recommended)", "Customize ›"]
                    onActivated: {
                        if (currentIndex === 0) page.selectAutomaticStreaming()
                        else page.saveSettings()
                    }
                }
                GridLayout {
                    visible: page.streamingCustomized; Layout.fillWidth: true
                    columns: 2; columnSpacing: 24; rowSpacing: 12
                    Text { text: "Encoder"; color: theme.textSecondary; Layout.preferredWidth: 145 }
                    CustomComboBox {
                        id: encoder; Layout.fillWidth: true
                        model: ["Auto", "NVIDIA", "VA-API", "Vulkan", "Software"]
                        onActivated: { backend.setSunshineEncoder(currentText); page.refreshGpuOptions(""); page.saveSettings() }
                    }
                    Text { text: "Codec"; color: theme.textSecondary }
                    CustomComboBox {
                        id: codec; Layout.fillWidth: true; model: ["Auto", "H.264", "HEVC", "AV1"]
                        onActivated: { backend.setSunshineCodec(currentText); page.saveSettings() }
                    }
                    Text { text: "Encoding GPU"; color: theme.textSecondary; visible: gpuOptions.length > 0 }
                    CustomComboBox { id: gpuCombo; Layout.fillWidth: true; visible: gpuOptions.length > 0; onActivated: page.saveSettings() }
                }
            }
            SectionCard {
                title: "EXTRAS"; symbol: "extras"
                Layout.fillWidth: true
                enabled: !createOnly.checked && !backend.isStreaming && !backend.sessionBusy
                CustomToggle {
                    id: nativeInput; text: "Touch input"
                    onCheckedChanged: {
                        if (!page.loading) backend.setSunshineNativePenTouch(checked)
                        page.saveSettings()
                    }
                }
                CustomToggle {
                    id: audio; text: "Audio"
                    onCheckedChanged: {
                        if (!page.loading) backend.saveSunshineConfig({"stream_audio": checked ? "enabled" : "disabled"})
                        page.saveSettings()
                    }
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
    Popup {
        id: vkmsCustomUnsupported; parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(430, parent.width - 40); padding: 18; focus: true
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: theme.surface; border.color: theme.borderHover; radius: theme.cardRadius }
        contentItem: ColumnLayout {
            spacing: 16
            Text {
                text: "Custom VKMS resolution unavailable"
                color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold
                wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            Text {
                text: "Your current VKMS driver does not support custom resolutions and refresh rates.\n\nCustom VKMS resolutions require monitorize-vkms."
                color: theme.textSecondary; font.pixelSize: 13; wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 10
                CustomButton { text: "Cancel"; primary: false; onClicked: vkmsCustomUnsupported.close() }
                CustomButton {
                    text: "Install monitorize-vkms"
                    onClicked: {
                        backend.openMonitorizeVkmsInstallPage()
                        vkmsCustomUnsupported.close()
                    }
                }
            }
        }
    }
    Popup {
        id: vkmsCustomCheckFailed; parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(430, parent.width - 40); padding: 18; focus: true
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: theme.surface; border.color: theme.borderHover; radius: theme.cardRadius }
        contentItem: ColumnLayout {
            spacing: 16
            Text {
                text: "Could not check VKMS custom-resolution support"
                color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold
                wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            Text {
                text: "Monitorize could not determine whether the current VKMS driver supports custom resolutions."
                color: theme.textSecondary; font.pixelSize: 13; wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 10
                CustomButton { text: "Cancel"; primary: false; onClicked: vkmsCustomCheckFailed.close() }
                CustomButton {
                    text: "Try Again"
                    onClicked: {
                        vkmsCustomCheckFailed.close()
                        backend.checkVkmsCustomEdidSupport()
                    }
                }
            }
        }
    }
}
