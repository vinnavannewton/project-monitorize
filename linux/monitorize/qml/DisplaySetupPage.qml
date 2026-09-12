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

    function resolutionValue() {
        return resCombo.currentText === "Custom..."
            ? customW.text + "x" + customH.text
            : resCombo.currentText.split(" ")[0]
    }

    function fpsValue() {
        return fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText
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
        backend.saveDisplaySettings(
            resCombo.currentText,
            resCombo.currentText === "Custom..." ? customW.text : "",
            resCombo.currentText === "Custom..." ? customH.text : "",
            page.vkmsSelected ? "60" : fpsCombo.currentText,
            !page.vkmsSelected && fpsCombo.currentText === "Custom..." ? customFps.text : "",
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
                : "Desktop Native"
        )
        resCombo.selectValue(saved["resolution"] || "1920x1080")
        customW.text = saved["custom_w"] || "1920"
        customH.text = saved["custom_h"] || "1080"
        fpsCombo.selectValue(saved["fps"] || "60")
        customFps.text = saved["custom_fps"] || "60"
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
                            ? ["Desktop Native", "VKMS (Experimental)"]
                            : ["Desktop Native"]
                        onActivated: {
                            if (page.vkmsSelected) {
                                if (!resCombo.selectValue(page.resolutionValue(), true))
                                    resCombo.selectValue("1920x1080")
                                fpsCombo.selectValue("60")
                            }
                            page.saveSettings()
                        }
                    }
                    Text {
                        visible: page.vkmsSelected
                        Layout.columnSpan: 2; Layout.fillWidth: true
                        wrapMode: Text.WordWrap; color: theme.textMuted
                        text: "Creates one display using Linux's experimental VKMS path. Display layout and positioning are managed by your desktop environment."
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
                    Text {
                        text: "Resolution"
                        color: displayType.currentText === "Mirror" ? theme.textMuted : theme.textSecondary
                    }
                    CustomComboBox {
                        id: resCombo; Layout.fillWidth: true
                        enabled: displayType.currentText !== "Mirror"
                        opacity: enabled ? 1 : 0.45
                        displayText: displayType.currentText === "Mirror"
                            ? page.mirrorResolutionLabel() : currentText
                        model: page.vkmsSelected ? backend.vkmsResolutionOptions : page.nativeResolutionOptions
                        onActivated: page.saveSettings()
                    }
                    Item { visible: displayType.currentText !== "Mirror" && !page.vkmsSelected && resCombo.currentText === "Custom..." }
                    RowLayout {
                        visible: displayType.currentText !== "Mirror" && !page.vkmsSelected && resCombo.currentText === "Custom..."
                        CustomTextField { id: customW; Layout.fillWidth: true; placeholderText: "Width"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                        Text { text: "×"; color: theme.textSecondary }
                        CustomTextField { id: customH; Layout.fillWidth: true; placeholderText: "Height"; maximumLength: 4; onEditingFinished: page.saveSettings() }
                    }
                    Text { text: "Refresh rate"; color: page.vkmsSelected ? theme.textMuted : theme.textSecondary }
                    CustomComboBox {
                        id: fpsCombo; Layout.fillWidth: true; model: ["30", "60", "90", "120", "Custom..."]
                        enabled: !page.vkmsSelected
                        opacity: enabled ? 1 : 0.45
                        displayText: page.vkmsSelected ? "~60 Hz — Managed by VKMS" : currentText
                        onActivated: page.saveSettings()
                    }
                    Item { visible: !page.vkmsSelected && fpsCombo.currentText === "Custom..." }
                    CustomTextField { id: customFps; visible: !page.vkmsSelected && fpsCombo.currentText === "Custom..."; placeholderText: "24–240"; maximumLength: 3; onEditingFinished: page.saveSettings() }
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
}
