import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: card
    required property int instance
    property bool showHeading: true
    objectName: "sunshineDisplay" + instance
    property bool loading: true
    property var gpuOptions: []
    readonly property bool customized: streamingMode.currentIndex === 1

    spacing: 12
    opacity: enabled ? 1.0 : 0.4

    function selectedGpuId() {
        if (gpuCombo.currentIndex < 0 || gpuCombo.currentIndex >= gpuOptions.length) return ""
        return gpuOptions[gpuCombo.currentIndex]["id"] || ""
    }

    function refreshGpuOptions(savedId) {
        gpuOptions = backend.getEncodingGpuOptions(encoder.currentText)
        let labels = []
        let selected = 0
        for (let i = 0; i < gpuOptions.length; ++i) {
            labels.push(gpuOptions[i]["label"])
            if (savedId && gpuOptions[i]["id"] === savedId) selected = i
        }
        gpuCombo.model = labels
        gpuCombo.currentIndex = labels.length ? selected : -1
    }

    function encoderDisplayValue(value) {
        let normalized = String(value || "Auto").toLowerCase()
        if (normalized.indexOf("software") === 0) return "Software"
        if (normalized.indexOf("intel/amd va-api") === 0 || normalized === "vaapi") return "VA-API"
        if (normalized.indexOf("nvidia") === 0 || normalized === "nvenc") return "NVIDIA"
        return value || "Auto"
    }

    function codecDisplayValue(value) {
        let normalized = String(value || "").toLowerCase()
        if (normalized.indexOf("h.264") !== -1 || normalized === "h264" || normalized.indexOf("avc") !== -1) return "H.264"
        if (normalized.indexOf("h.265") !== -1 || normalized.indexOf("hevc") !== -1) return "HEVC"
        if (normalized.indexOf("av1") !== -1) return "AV1"
        return "Auto"
    }

    function captureValue() {
        let labels = {
            "KWin": "kwin", "Portal": "portal", "KMS": "kms", "WLR": "wlr",
            "X11": "x11", "NvFBC": "nvfbc", "PipeWire node": "pipewire_node"
        }
        return labels[captureMode.currentText] || "auto"
    }

    function captureDisplayValue(value) {
        let names = {
            "kwin": "KWin", "portal": "Portal", "kms": "KMS", "wlr": "WLR",
            "x11": "X11", "nvfbc": "NvFBC", "pipewire_node": "PipeWire node"
        }
        return names[value] || "Monitorize Auto"
    }

    function saveSettings() {
        if (loading) return
        let selectedEncoder = customized ? encoder.currentText : "Auto"
        let selectedCodec = customized ? codec.currentText : "Auto"
        backend.setSunshineEncoder(selectedEncoder, instance)
        backend.setSunshineCodec(selectedCodec, instance)
        backend.setSunshineNativePenTouch(nativeInput.checked, instance)
        backend.saveSunshineConfig({"stream_audio": audio.checked ? "enabled" : "disabled"}, instance)
        backend.saveSunshineDisplaySettings(instance, {
            "sunshine_encoder": selectedEncoder,
            "sunshine_gpu": customized ? selectedGpuId() : "",
            "sunshine_codec": selectedCodec,
            "sunshine_capture": captureValue(),
            "streaming_customized": customized,
            "sunshine_native_pen_touch": nativeInput.checked,
            "enable_audio": audio.checked
        })
    }

    function loadSettings() {
        loading = true
        let saved = instance === 1 ? backend.loadDisplaySettings() : backend.loadSecondDisplaySettings()
        streamingMode.selectValue(saved["streaming_customized"] === true
            ? "Customize ›" : "Automatic (Recommended)")
        encoder.selectValue(encoderDisplayValue(saved["sunshine_encoder"]))
        refreshGpuOptions(saved["sunshine_gpu"] || "")
        codec.selectValue(codecDisplayValue(saved["sunshine_codec"]))
        let capture = String(saved["sunshine_capture"] || "auto").toLowerCase()
        captureMode.selectValue(captureDisplayValue(capture), true)
        nativeInput.checked = saved["sunshine_native_pen_touch"] !== false
        audio.checked = saved["enable_audio"] === true
        loading = false
    }

    Component.onCompleted: loadSettings()
    onVisibleChanged: { if (visible && !loading) loadSettings() }

    Rectangle {
        visible: card.showHeading
        Layout.fillWidth: true
        implicitHeight: 1
        color: theme.border
    }
    RowLayout {
        visible: card.showHeading
        Layout.fillWidth: true
        spacing: 10
        LineIcon { symbol: "streaming"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
        Text {
            text: "Sunshine"
            color: "#b1d6ff"; font.pixelSize: 13; font.weight: Font.DemiBold
            Layout.fillWidth: true
        }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: 2; columnSpacing: 24; rowSpacing: 12
        Text { text: "Capture mode"; color: theme.textSecondary; Layout.preferredWidth: 145 }
        CustomComboBox {
            id: captureMode
            objectName: "captureMode" + card.instance
            Layout.fillWidth: true
            model: ["Monitorize Auto", "KWin", "Portal", "KMS", "WLR", "X11", "NvFBC", "PipeWire node"]
            onActivated: card.saveSettings()
        }
        Text {
            text: "Manual modes may require a different desktop, session, or Sunshine build."
            color: theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap
            Layout.columnSpan: 2; Layout.fillWidth: true
        }
    }
    CustomComboBox {
        id: streamingMode
        Layout.fillWidth: true
        model: ["Automatic (Recommended)", "Customize ›"]
        onActivated: card.saveSettings()
    }
    GridLayout {
        visible: card.customized; Layout.fillWidth: true
        columns: 2; columnSpacing: 24; rowSpacing: 12
        Text { text: "Encoder"; color: theme.textSecondary; Layout.preferredWidth: 145 }
        CustomComboBox {
            id: encoder; Layout.fillWidth: true
            model: ["Auto", "NVIDIA", "VA-API", "Vulkan", "Software"]
            onActivated: { card.refreshGpuOptions(""); card.saveSettings() }
        }
        Text { text: "Codec"; color: theme.textSecondary }
        CustomComboBox {
            id: codec; Layout.fillWidth: true
            model: ["Auto", "H.264", "HEVC", "AV1"]
            onActivated: card.saveSettings()
        }
        Text { text: "Encoding GPU"; color: theme.textSecondary; visible: card.gpuOptions.length > 0 }
        CustomComboBox {
            id: gpuCombo; Layout.fillWidth: true
            visible: card.gpuOptions.length > 0
            onActivated: card.saveSettings()
        }
    }
    CustomToggle {
        id: nativeInput; text: "Touch input"
        onCheckedChanged: card.saveSettings()
    }
    CustomToggle {
        id: audio; text: "Audio"
        onCheckedChanged: card.saveSettings()
    }
}
