import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: card
    required property int instance
    property bool showHeading: true
    objectName: "sunshineDisplay" + instance
    property bool loading: true
    property string saveError: ""
    property var gpuOptions: []
    property int gpuRequest: 0
    property string pendingGpuId: ""
    property string encoderChoice: "Auto"
    property string codecChoice: "Auto"
    property string gpuId: ""
    readonly property bool customized: streamingMode.currentIndex === 1

    spacing: 12

    function refreshGpuOptions(savedId) {
        pendingGpuId = savedId
        gpuOptions = []
        gpuRequest = customized && (encoderChoice === "NVIDIA" || encoderChoice === "VA-API")
            ? backend.requestEncodingGpuOptions(encoderChoice) : 0
    }

    function applyGpuOptions(options) {
        gpuOptions = options
        gpuId = options.some(function(option) { return option.id === pendingGpuId })
            ? pendingGpuId : ""
        if (editorLoader.item) editorLoader.item.applyGpuLabels()
    }

    Connections {
        target: backend
        function onEncodingGpuOptionsReady(request, requestedEncoder, options) {
            if (request === card.gpuRequest && requestedEncoder === card.encoderChoice)
                card.applyGpuOptions(options)
        }
        function onSunshineChoicesFinished(savedInstance, success, message) {
            if (savedInstance === card.instance)
                card.saveError = success ? "" : message
        }
        function onSunshineSettingsRevisionChanged() {
            if (!card.loading && card.visible) card.loadSettings()
        }
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
        let selectedEncoder = customized ? encoderChoice : "Auto"
        let selectedCodec = customized ? codecChoice : "Auto"
        let result = backend.requestSaveSunshineChoices(instance, {
            "sunshine_encoder": selectedEncoder,
            "sunshine_gpu": customized ? gpuId : "",
            "sunshine_codec": selectedCodec,
            "sunshine_capture": captureValue(),
            "streaming_customized": customized,
            "sunshine_native_pen_touch": nativeInput.checked,
            "enable_audio": audio.checked
        })
        saveError = result["accepted"] ? "" : (result["message"] || "Could not save Sunshine settings.")
    }

    function loadSettings() {
        loading = true
        let saved = instance === 1 ? backend.loadDisplaySettings() : backend.loadSecondDisplaySettings()
        encoderChoice = encoderDisplayValue(saved["sunshine_encoder"])
        codecChoice = codecDisplayValue(saved["sunshine_codec"])
        gpuId = saved["sunshine_gpu"] || ""
        streamingMode.selectValue(saved["streaming_customized"] === true
            ? "Customize ›" : "Automatic (Recommended)")
        refreshGpuOptions(gpuId)
        if (editorLoader.item) editorLoader.item.loadChoices()
        let capture = String(saved["sunshine_capture"] || "auto").toLowerCase()
        captureMode.selectValue(captureDisplayValue(capture), true)
        nativeInput.checked = saved["sunshine_native_pen_touch"] !== false
        audio.checked = saved["enable_audio"] === true
        loading = false
    }

    Component.onCompleted: loadSettings()

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
            color: card.enabled ? "#b1d6ff" : theme.textMuted
            font.pixelSize: 13; font.weight: Font.DemiBold
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
        onActivated: {
            card.refreshGpuOptions(card.gpuId)
            card.saveSettings()
        }
    }
    Loader {
        id: editorLoader
        active: card.customized
        Layout.fillWidth: true
        Layout.preferredHeight: item ? item.implicitHeight : 0
        onLoaded: item.loadChoices()
        sourceComponent: Component {
            GridLayout {
                columns: 2; columnSpacing: 24; rowSpacing: 12
                function loadChoices() {
                    encoder.selectValue(card.encoderChoice, true)
                    codec.selectValue(card.codecChoice, true)
                    applyGpuLabels()
                }
                function applyGpuLabels() {
                    let labels = card.gpuOptions.map(function(option) { return option.label })
                    gpuCombo.model = labels
                    let selected = card.gpuOptions.findIndex(function(option) {
                        return option.id === card.gpuId
                    })
                    gpuCombo.currentIndex = labels.length ? Math.max(0, selected) : -1
                }
                Text { text: "Encoder"; color: theme.textSecondary; Layout.preferredWidth: 145 }
                CustomComboBox {
                    id: encoder; Layout.fillWidth: true
                    model: ["Auto", "NVIDIA", "VA-API", "Vulkan", "Software"]
                    onActivated: {
                        card.encoderChoice = currentText
                        card.gpuId = ""
                        card.refreshGpuOptions("")
                        card.saveSettings()
                    }
                }
                Text { text: "Codec"; color: theme.textSecondary }
                CustomComboBox {
                    id: codec; Layout.fillWidth: true
                    model: ["Auto", "H.264", "HEVC", "AV1"]
                    onActivated: {
                        card.codecChoice = currentText
                        card.saveSettings()
                    }
                }
                Text { text: "Encoding GPU"; color: theme.textSecondary; visible: card.gpuOptions.length > 0 }
                CustomComboBox {
                    id: gpuCombo; Layout.fillWidth: true
                    visible: card.gpuOptions.length > 0
                    onActivated: {
                        card.gpuId = card.gpuOptions[currentIndex]["id"] || ""
                        card.saveSettings()
                    }
                }
            }
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
    Text {
        visible: card.saveError.length > 0
        text: card.saveError
        color: "#fca5a5"
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
