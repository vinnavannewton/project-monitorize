import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    width: 860
    height: 580
    color: "#0c0d14"

    gradient: Gradient {
        GradientStop { position: 0.0; color: "#0c0d14" }
        GradientStop { position: 1.0; color: "#06070a" }
    }

    // --- State Variables ---
    property string detectedDe: backend ? backend.detectedDe : "unknown"
    property string localIp: backend ? backend.localIp : "0.0.0.0"
    property string usbStatusText: backend ? backend.usbStatusText : ""
    property bool usbBusy: backend ? backend.usbBusy : false
    property bool isStreaming: backend ? backend.isStreaming : false
    property int countdown: backend ? backend.countdown : 0
    property string streamingStatus: backend ? backend.streamingStatus : ""

    // --- Connections to Python Backend Signals ---
    Connections {
        target: backend
        function onDetectedDeChanged(de) { root.detectedDe = de }
        function onLocalIpChanged(ip) { root.localIp = ip }
        function onUsbStatusTextChanged(txt) { root.usbStatusText = txt }
        function onUsbBusyChanged(busy) { root.usbBusy = busy }
        function onIsStreamingChanged(streaming) {
            root.isStreaming = streaming
            if (streaming) {
                stack.replace(pageStreaming)
            } else {
                stack.replace(pageMainMenu, StackView.PopTransition)
            }
        }
        function onCountdownChanged(val) { root.countdown = val }
        function onLogAppended(type, msg) {
            if (pageStreamingInstance) {
                pageStreamingInstance.appendLog(type, msg)
            }
        }
    }

    Transition {
        id: fastRebound
        SpringAnimation {
            properties: "y"
            spring: 12.0
            damping: 0.8
        }
    }

    // Keep reference to streaming page instance to forward logs
    property var pageStreamingInstance: null

    // --- Main StackView for Pages ---
    StackView {
        id: stack
        anchors.fill: parent
        anchors.margins: 20
        initialItem: pageMainMenu

        pushEnter: Transition {
            PropertyAnimation { property: "x"; from: stack.width; to: 0; duration: 300; easing.type: Easing.OutCubic }
            PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 250 }
        }
        pushExit: Transition {
            PropertyAnimation { property: "x"; to: -stack.width; duration: 300; easing.type: Easing.OutCubic }
            PropertyAnimation { property: "opacity"; to: 0; duration: 250 }
        }
        popEnter: Transition {
            PropertyAnimation { property: "x"; from: -stack.width; to: 0; duration: 300; easing.type: Easing.OutCubic }
            PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 250 }
        }
        popExit: Transition {
            PropertyAnimation { property: "x"; to: stack.width; duration: 300; easing.type: Easing.OutCubic }
            PropertyAnimation { property: "opacity"; to: 0; duration: 250 }
        }
    }

    // =========================================================================
    // PAGE: MAIN MENU
    // =========================================================================
    Component {
        id: pageMainMenu
        Item {
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 20
                width: Math.min(parent.width - 40, 600)

                Text {
                    text: "Monitorize"
                    font.pixelSize: 32
                    font.weight: Font.ExtraBold
                    color: "#e0e2ff"
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: "Linux → Android Display Bridge"
                    font.pixelSize: 14
                    color: "#6a6c96"
                    Layout.alignment: Qt.AlignHCenter
                }

                // Desktop Badge
                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    implicitWidth: 180
                    implicitHeight: 32
                    radius: 16
                    color: "#161726"
                    border.color: "#2a2d55"
                    border.width: 1

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: 8

                        Image {
                            source: {
                                if (detectedDe === "kde") return "../assets/svg/kde-logo.svg"
                                if (detectedDe === "gnome") return "../assets/svg/gnome-logo.svg"
                                if (detectedDe === "hyprland") return "../assets/svg/hyprland-logo.svg"
                                return ""
                            }
                            sourceSize.width: 14
                            sourceSize.height: 14
                            Layout.alignment: Qt.AlignVCenter
                            visible: source !== ""
                        }

                        Text {
                            text: "Desktop: " + (detectedDe === "kde" ? "KDE Plasma" : (detectedDe === "gnome" ? "GNOME" : (detectedDe === "hyprland" ? "Hyprland" : detectedDe.toUpperCase())))
                            color: "#6a6cbb"
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            Layout.alignment: Qt.AlignVCenter
                        }
                    }
                }

                Item { Layout.preferredHeight: 20 }

                RowLayout {
                    spacing: 30
                    Layout.alignment: Qt.AlignHCenter

                    // USB Mode Card
                    Rectangle {
                        id: usbCard
                        implicitWidth: 220
                        implicitHeight: 140
                        radius: 16
                        color: usbMouseArea.containsMouse ? "#1c1e3a" : "#12142a"
                        border.color: usbMouseArea.containsMouse ? "#4c4fd0" : "#2a2d55"
                        border.width: 1
                        scale: usbMouseArea.containsMouse ? 1.03 : 1.0
                        Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }

                        ColumnLayout {
                            anchors.centerIn: parent
                            spacing: 12
                            Image {
                                source: "../assets/svg/usb-logo.svg"
                                sourceSize.width: 48
                                sourceSize.height: 48
                                Layout.alignment: Qt.AlignHCenter
                            }
                            Text {
                                text: "USB Mode"
                                font.pixelSize: 16
                                font.weight: Font.Bold
                                color: "#c0c2ee"
                                Layout.alignment: Qt.AlignHCenter
                            }
                        }

                        MouseArea {
                            id: usbMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                stack.push(pageUsbStep1)
                            }
                        }
                    }

                    // Wi-Fi Mode Card
                    Rectangle {
                        id: wifiCard
                        implicitWidth: 220
                        implicitHeight: 140
                        radius: 16
                        color: wifiMouseArea.containsMouse ? "#1c1e3a" : "#12142a"
                        border.color: wifiMouseArea.containsMouse ? "#4c4fd0" : "#2a2d55"
                        border.width: 1
                        scale: wifiMouseArea.containsMouse ? 1.03 : 1.0
                        Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }

                        ColumnLayout {
                            anchors.centerIn: parent
                            spacing: 12
                            Image {
                                source: "../assets/svg/wifi-logo.svg"
                                sourceSize.width: 48
                                sourceSize.height: 48
                                Layout.alignment: Qt.AlignHCenter
                            }
                            Text {
                                text: "Wi-Fi Mode"
                                font.pixelSize: 16
                                font.weight: Font.Bold
                                color: "#c0c2ee"
                                Layout.alignment: Qt.AlignHCenter
                            }
                        }

                        MouseArea {
                            id: wifiMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                stack.push(pageWifi)
                            }
                        }
                    }
                }

                Item { Layout.preferredHeight: 30 }

                Text {
                    text: "Select a connection mode to begin"
                    font.pixelSize: 12
                    color: "#5a5c82"
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }
    }

    // =========================================================================
    // PAGE: USB STEP 1 (SCAN DEVICE)
    // =========================================================================
    Component {
        id: pageUsbStep1
        Item {
            id: usbStep1Page
            property bool hasStartedScan: false
            property bool isScanSuccessful: usbStatusText === "Device ready!" || usbStatusText.indexOf("Warning:") === 0

            Component.onCompleted: {
                hasStartedScan = false;
                backend.resetUsbStatus();
            }

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 24
                width: Math.min(parent.width - 40, 500)

                Text {
                    text: "USB Mode  ·  Step 1 of 2"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    color: "#5a5c82"
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: !hasStartedScan ? "Is the tablet connected via USB?" : (isScanSuccessful ? "Connection Established!" : "Scanning for Connected Android Device...")
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: "#d4d6f0"
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: !hasStartedScan 
                        ? "Please connect your Android tablet to this computer using a USB cable, and ensure that USB Debugging is enabled in Settings."
                        : usbStatusText
                    font.pixelSize: 13
                    color: !hasStartedScan ? "#6a6c96" : (isScanSuccessful ? "#4cd68d" : "#8a8cc0")
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                // Custom Spinner
                Rectangle {
                    id: spinnerContainer
                    Layout.alignment: Qt.AlignHCenter
                    width: 60
                    height: 60
                    color: "transparent"
                    visible: hasStartedScan && usbBusy

                    Rectangle {
                        anchors.fill: parent
                        radius: 30
                        color: "transparent"
                        border.color: "#1a1c30"
                        border.width: 4
                    }

                    Rectangle {
                        id: activeArc
                        width: 60
                        height: 60
                        radius: 30
                        color: "transparent"
                        border.color: "#4c4fd0"
                        border.width: 4
                        clip: true

                        Rectangle {
                            width: 60
                            height: 30
                            color: "#0c0d14"
                            anchors.bottom: parent.bottom
                            visible: !usbBusy
                        }
                    }

                    RotationAnimator {
                        target: activeArc
                        from: 0
                        to: 360
                        duration: 1000
                        running: usbBusy
                        loops: Animation.Infinite
                    }
                }

                RowLayout {
                    spacing: 20
                    Layout.alignment: Qt.AlignHCenter

                    Button {
                        text: !hasStartedScan ? "Cancel" : "Back"
                        visible: !isScanSuccessful
                        onClicked: {
                            stack.pop()
                        }
                        background: Rectangle {
                            implicitWidth: 100
                            implicitHeight: 38
                            color: "transparent"
                            border.color: "#2a2d55"
                            radius: 8
                        }
                        contentItem: Text {
                            text: parent.text
                            color: "#6a6c90"
                            font.pixelSize: 13
                            font.weight: Font.Bold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    CustomButton {
                        text: "Yes, Start Scan"
                        visible: !hasStartedScan
                        implicitWidth: 150
                        onClicked: {
                            usbStep1Page.hasStartedScan = true
                            backend.startUsbScan()
                        }
                    }

                    CustomButton {
                        text: "Retry Scan"
                        visible: hasStartedScan && !usbBusy && !isScanSuccessful
                        implicitWidth: 120
                        onClicked: {
                            backend.startUsbScan()
                        }
                    }
                }

                Timer {
                    interval: 600
                    running: usbStep1Page.isScanSuccessful
                    repeat: false
                    onTriggered: {
                        stack.replace(pageUsbStep2)
                    }
                }
            }
        }
    }

    // =========================================================================
    // PAGE: USB STEP 2 (CONFIG)
    // =========================================================================
    Component {
        id: pageUsbStep2
        Item {
            Component.onCompleted: {
                let saved = backend.loadUsbSettings();
                resCombo.currentIndex = resCombo.find(saved["resolution"] || "2560x1600");
                if (saved["resolution"] === "Custom...") {
                    customW.text = saved["custom_w"] || "";
                    customH.text = saved["custom_h"] || "";
                }
                fpsCombo.currentIndex = fpsCombo.find(saved["fps"] || "60");
                if (saved["fps"] === "Custom...") {
                    customFps.text = saved["custom_fps"] || "";
                }
                bitrateField.text = saved["bitrate"] || "8000";
                if (saved["display_type"] && displayTypeCombo) {
                    displayTypeCombo.currentIndex = displayTypeCombo.find(saved["display_type"]);
                }
                encoderCombo.currentIndex = encoderCombo.find(saved["encoder"] || "Auto-detect (Recommended)");

                let gen = backend.loadGeneralSettings();
                trayCheck.checked = gen["minimize_to_tray"] || false;
                touchCheck.checked = gen["enable_touch"] || false;

                usbScroll.contentItem.rebound = fastRebound;
            }

            ScrollView {
                id: usbScroll
                anchors.fill: parent
                contentWidth: parent.width
                contentHeight: columnLayout.implicitHeight + 40

                ColumnLayout {
                    id: columnLayout
                    width: parent.width - 40
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 16

                    Text {
                        text: "USB Mode  ·  Step 2 of 2"
                        font.pixelSize: 12
                        font.weight: Font.Bold
                        color: "#5a5c82"
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: "#1a1c30"
                    }

                    Text {
                        text: "Please open the Monitorize app on your tablet."
                        font.pixelSize: 15
                        color: "#b0b2d0"
                        Layout.alignment: Qt.AlignHCenter
                    }

                    // Fields Grid
                    GridLayout {
                        columns: 2
                        columnSpacing: 20
                        rowSpacing: 12
                        Layout.alignment: Qt.AlignHCenter

                        Text { text: "Resolution:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: resCombo
                            model: ["1280x720", "1280x800", "1920x1080", "1920x1200", "2560x1440", "2560x1600", "3840x2160", "Custom..."]
                        }

                        // Custom Res fields row
                        Text { text: ""; visible: resCombo.currentText === "Custom..."; font.pixelSize: 14 }
                        RowLayout {
                            spacing: 8
                            visible: resCombo.currentText === "Custom..."

                            CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4 }
                            Text { text: "×"; color: "#6a6c96"; font.pixelSize: 18; font.weight: Font.Bold }
                            CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4 }
                            Text { text: "(500 - 4000)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                        }

                        Text { text: "FPS:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: fpsCombo
                            model: ["30", "60", "90", "120", "Custom..."]
                        }

                        // Custom FPS field row
                        Text { text: ""; visible: fpsCombo.currentText === "Custom..."; font.pixelSize: 14 }
                        RowLayout {
                            spacing: 8
                            visible: fpsCombo.currentText === "Custom..."

                            CustomTextField { id: customFps; placeholderText: "FPS"; maximumLength: 3 }
                            Text { text: "(24 - 240)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                        }

                        Text { text: "Video Bitrate (kbps):"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomTextField {
                            id: bitrateField
                            text: "8000"
                            maximumLength: 5
                        }

                        // Display Type (only on KDE/GNOME/Hyprland)
                        Text {
                            text: "Display Type:"
                            color: "#b0b2d0"
                            font.pixelSize: 14
                            visible: root.detectedDe === "kde" || root.detectedDe === "gnome" || root.detectedDe === "hyprland"
                        }
                        CustomComboBox {
                            id: displayTypeCombo
                            visible: root.detectedDe === "kde" || root.detectedDe === "gnome" || root.detectedDe === "hyprland"
                            model: ["Extend Right", "Mirror"]
                        }

                        Text { text: "Encoder:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: encoderCombo
                            model: [
                                "Auto-detect (Recommended)",
                                "NVIDIA NVENC (nvh264enc)",
                                "Intel/AMD VA-API (vah264enc)",
                                "Software (CPU / x264enc)"
                            ]
                        }
                    }

                    // Checkbox Settings row
                    RowLayout {
                        spacing: 24
                        Layout.alignment: Qt.AlignHCenter

                        CustomCheckBox {
                            id: trayCheck
                            text: "Minimize to tray on close"
                        }

                        CustomCheckBox {
                            id: touchCheck
                            text: "Enable Touch Input"
                        }
                    }

                    // Warning card
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: warningText.implicitHeight + 20
                        radius: 8
                        color: "#161109"
                        border.color: "#3d2a0a"
                        border.width: 1

                        Text {
                            id: warningText
                            anchors.fill: parent
                            anchors.margins: 10
                            text: "WARNING: The Resolution and FPS set here MUST EXACTLY MATCH the settings in the Android tablet app, or the stream will corrupt!"
                            color: "#e8a840"
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            wrapMode: Text.Wrap
                        }
                    }

                    // Spacing
                    Item { Layout.preferredHeight: 10 }

                    // Navigation buttons
                    RowLayout {
                        spacing: 20
                        Layout.alignment: Qt.AlignHCenter

                        Button {
                            text: "← Back"
                            onClicked: {
                                stack.pop()
                            }
                            background: Rectangle {
                                implicitWidth: 100
                                implicitHeight: 38
                                color: "transparent"
                                border.color: "#2a2d55"
                                radius: 8
                            }
                            contentItem: Text {
                                text: parent.text
                                color: "#6a6c90"
                                font.pixelSize: 13
                                font.weight: Font.Bold
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        CustomButton {
                            text: "▶  Start Streaming"
                            implicitWidth: 200
                            implicitHeight: 44
                            onClicked: {
                                // Save settings
                                backend.saveGeneralSettings(trayCheck.checked, touchCheck.checked);
                                backend.saveUsbSettings(
                                    resCombo.currentText,
                                    resCombo.currentText === "Custom..." ? customW.text : "",
                                    resCombo.currentText === "Custom..." ? customH.text : "",
                                    fpsCombo.currentText,
                                    fpsCombo.currentText === "Custom..." ? customFps.text : "",
                                    bitrateField.text,
                                    displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend Right",
                                    encoderCombo.currentText
                                );
                                // Start stream
                                backend.startStreaming(
                                    resCombo.currentText === "Custom..." ? customW.text + "x" + customH.text : resCombo.currentText,
                                    fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText,
                                    bitrateField.text,
                                    displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend Right",
                                    encoderCombo.currentText,
                                    false // isWifi = false
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    // =========================================================================
    // PAGE: WI-FI CONFIG
    // =========================================================================
    Component {
        id: pageWifi
        Item {
            Component.onCompleted: {
                let saved = backend.loadWifiSettings();
                resCombo.currentIndex = resCombo.find(saved["resolution"] || "2560x1600");
                if (saved["resolution"] === "Custom...") {
                    customW.text = saved["custom_w"] || "";
                    customH.text = saved["custom_h"] || "";
                }
                fpsCombo.currentIndex = fpsCombo.find(saved["fps"] || "60");
                if (saved["fps"] === "Custom...") {
                    customFps.text = saved["custom_fps"] || "";
                }
                bitrateField.text = saved["bitrate"] || "8000";
                if (saved["display_type"] && displayTypeCombo) {
                    displayTypeCombo.currentIndex = displayTypeCombo.find(saved["display_type"]);
                }
                encoderCombo.currentIndex = encoderCombo.find(saved["encoder"] || "Auto-detect (Recommended)");

                let gen = backend.loadGeneralSettings();
                trayCheck.checked = gen["minimize_to_tray"] || false;
                touchCheck.checked = gen["enable_touch"] || false;

                wifiScroll.contentItem.rebound = fastRebound;
            }

            ScrollView {
                id: wifiScroll
                anchors.fill: parent
                contentWidth: parent.width
                contentHeight: wifiColumn.implicitHeight + 40

                ColumnLayout {
                    id: wifiColumn
                    width: parent.width - 40
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 16

                    Text {
                        text: "Wi-Fi Mode Settings"
                        font.pixelSize: 12
                        font.weight: Font.Bold
                        color: "#5a5c82"
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: "#1a1c30"
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 10
                        Text { text: "📶"; font.pixelSize: 22 }
                        Text {
                            text: "Your Local IP Address is: " + localIp
                            font.pixelSize: 16
                            font.weight: Font.Bold
                            color: "#4cd68d"
                        }
                    }

                    Text {
                        text: "Enter this IP in the Monitorize Android app and tap Receive."
                        font.pixelSize: 14
                        color: "#8a8cc0"
                        Layout.alignment: Qt.AlignHCenter
                    }

                    // Fields Grid
                    GridLayout {
                        columns: 2
                        columnSpacing: 20
                        rowSpacing: 12
                        Layout.alignment: Qt.AlignHCenter

                        Text { text: "Resolution:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: resCombo
                            model: ["1280x720", "1280x800", "1920x1080", "1920x1200", "2560x1440", "2560x1600", "3840x2160", "Custom..."]
                        }

                        // Custom Res fields row
                        Text { text: ""; visible: resCombo.currentText === "Custom..."; font.pixelSize: 14 }
                        RowLayout {
                            spacing: 8
                            visible: resCombo.currentText === "Custom..."

                            CustomTextField { id: customW; placeholderText: "Width"; maximumLength: 4 }
                            Text { text: "×"; color: "#6a6c96"; font.pixelSize: 18; font.weight: Font.Bold }
                            CustomTextField { id: customH; placeholderText: "Height"; maximumLength: 4 }
                            Text { text: "(500 - 4000)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                        }

                        Text { text: "FPS:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: fpsCombo
                            model: ["30", "60", "90", "120", "Custom..."]
                        }

                        // Custom FPS field row
                        Text { text: ""; visible: fpsCombo.currentText === "Custom..."; font.pixelSize: 14 }
                        RowLayout {
                            spacing: 8
                            visible: fpsCombo.currentText === "Custom..."

                            CustomTextField { id: customFps; placeholderText: "FPS"; maximumLength: 3 }
                            Text { text: "(24 - 240)"; color: "#4a4c70"; font.pixelSize: 11; font.italic: true }
                        }

                        Text { text: "Video Bitrate (kbps):"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomTextField {
                            id: bitrateField
                            text: "8000"
                            maximumLength: 5
                        }

                        // Display Type (only on KDE/GNOME/Hyprland)
                        Text {
                            text: "Display Type:"
                            color: "#b0b2d0"
                            font.pixelSize: 14
                            visible: root.detectedDe === "kde" || root.detectedDe === "gnome" || root.detectedDe === "hyprland"
                        }
                        CustomComboBox {
                            id: displayTypeCombo
                            visible: root.detectedDe === "kde" || root.detectedDe === "gnome" || root.detectedDe === "hyprland"
                            model: ["Extend Right", "Mirror"]
                        }

                        Text { text: "Encoder:"; color: "#b0b2d0"; font.pixelSize: 14 }
                        CustomComboBox {
                            id: encoderCombo
                            model: [
                                "Auto-detect (Recommended)",
                                "NVIDIA NVENC (nvh264enc)",
                                "Intel/AMD VA-API (vah264enc)",
                                "Software (CPU / x264enc)"
                            ]
                        }
                    }

                    // Checkbox Settings row
                    RowLayout {
                        spacing: 24
                        Layout.alignment: Qt.AlignHCenter

                        CustomCheckBox {
                            id: trayCheck
                            text: "Minimize to tray on close"
                        }

                        CustomCheckBox {
                            id: touchCheck
                            text: "Enable Touch Input"
                        }
                    }

                    // Warning card
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: warningText.implicitHeight + 20
                        radius: 8
                        color: "#161109"
                        border.color: "#3d2a0a"
                        border.width: 1

                        Text {
                            id: warningText
                            anchors.fill: parent
                            anchors.margins: 10
                            text: "WARNING: The Resolution and FPS set here MUST EXACTLY MATCH the settings in the Android tablet app, or the stream will corrupt!"
                            color: "#e8a840"
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            wrapMode: Text.Wrap
                        }
                    }

                    // Spacing
                    Item { Layout.preferredHeight: 10 }

                    // Navigation buttons
                    RowLayout {
                        spacing: 20
                        Layout.alignment: Qt.AlignHCenter

                        Button {
                            text: "← Back"
                            onClicked: {
                                stack.pop()
                            }
                            background: Rectangle {
                                implicitWidth: 100
                                implicitHeight: 38
                                color: "transparent"
                                border.color: "#2a2d55"
                                radius: 8
                            }
                            contentItem: Text {
                                text: parent.text
                                color: "#6a6c90"
                                font.pixelSize: 13
                                font.weight: Font.Bold
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        CustomButton {
                            text: "▶  Start Streaming"
                            implicitWidth: 200
                            implicitHeight: 44
                            onClicked: {
                                // Save settings
                                backend.saveGeneralSettings(trayCheck.checked, touchCheck.checked);
                                backend.saveWifiSettings(
                                    resCombo.currentText,
                                    resCombo.currentText === "Custom..." ? customW.text : "",
                                    resCombo.currentText === "Custom..." ? customH.text : "",
                                    fpsCombo.currentText,
                                    fpsCombo.currentText === "Custom..." ? customFps.text : "",
                                    bitrateField.text,
                                    displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend Right",
                                    encoderCombo.currentText
                                );
                                // Start stream
                                backend.startStreaming(
                                    resCombo.currentText === "Custom..." ? customW.text + "x" + customH.text : resCombo.currentText,
                                    fpsCombo.currentText === "Custom..." ? customFps.text : fpsCombo.currentText,
                                    bitrateField.text,
                                    displayTypeCombo.visible ? displayTypeCombo.currentText : "Extend Right",
                                    encoderCombo.currentText,
                                    true // isWifi = true
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    // =========================================================================
    // PAGE: STREAMING ACTIVE
    // =========================================================================
    Component {
        id: pageStreaming
        Item {
            id: pageStreamingRoot

            Component.onCompleted: {
                root.pageStreamingInstance = pageStreamingRoot
            }

            // Tab selection for logs filter
            property string logFilter: "ALL"

            // Local arrays to keep logs separated by category
            property var allLogs: []

            function appendLog(type, msg) {
                allLogs.push({ type: type, message: msg })
                updateLogDisplay()
            }

            function updateLogDisplay() {
                let text = ""
                for (let i = 0; i < allLogs.length; i++) {
                    let log = allLogs[i]
                    if (logFilter === "ALL" || log.type === logFilter) {
                        let categoryColor = "#7cc87c"
                        if (log.type === "STREAMER") categoryColor = "#5c9eff"
                        else if (log.type === "INPUT") categoryColor = "#e8a840"

                        let msgColor = "#b8bad8"
                        let lowerMsg = log.message.toLowerCase()
                        if (lowerMsg.includes("warning") || lowerMsg.includes("warn")) {
                            msgColor = "#e8a840"
                        } else if (lowerMsg.includes("error") || lowerMsg.includes("exception") || lowerMsg.includes("failed") || lowerMsg.includes("denied") || lowerMsg.includes("crashed")) {
                            msgColor = "#ff6b6b"
                        } else if (lowerMsg.includes("success") || lowerMsg.includes("ready") || lowerMsg.includes("listening") || lowerMsg.includes("connected") || lowerMsg.includes("active")) {
                            msgColor = "#4cd68d"
                        }

                        let tag = "[" + log.type + "]"
                        text += "<b><font color='" + categoryColor + "'>" + tag + "</font></b> &nbsp;<font color='" + msgColor + "'>" + log.message + "</font><br>"
                    }
                }
                logArea.text = text
                // Scroll to bottom
                logScrollView.contentItem.contentY = Math.max(0, logArea.implicitHeight - logScrollView.height)
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 14

                // Top Status Card
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 60
                    radius: 12
                    color: "#12142a"
                    border.color: "#2a2d55"
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 20
                        anchors.rightMargin: 20
                        spacing: 20

                        // Pulsing Active Indicator
                        Rectangle {
                            width: 12
                            height: 12
                            radius: 6
                            color: "#4cd68d"

                            OpacityAnimator {
                                target: parent.children[0]
                                from: 0.3
                                to: 1.0
                                duration: 800
                                running: root.isStreaming
                                loops: Animation.Infinite
                            }
                        }

                        Text {
                            text: countdown > 0 ? ("Streaming starting in " + countdown + "...") : "Streaming Active"
                            font.pixelSize: 18
                            font.weight: Font.Bold
                            color: "#4cd68d"
                        }

                        Text {
                            text: root.streamingStatus
                            font.pixelSize: 13
                            color: "#8a8cc0"
                            Layout.fillWidth: true
                        }
                    }
                }

                // Log Tab Filters
                RowLayout {
                    spacing: 10
                    Layout.fillWidth: true

                    Repeater {
                        model: ["ALL", "STREAMER", "INPUT"]
                        Button {
                            text: modelData
                            property bool isSelected: pageStreamingRoot.logFilter === modelData
                            onClicked: {
                                pageStreamingRoot.logFilter = modelData
                                pageStreamingRoot.updateLogDisplay()
                            }
                            background: Rectangle {
                                implicitWidth: 100
                                implicitHeight: 32
                                color: isSelected ? "#3538b0" : "#12142a"
                                border.color: isSelected ? "#4c4fd0" : "#2a2d55"
                                radius: 6
                            }
                            contentItem: Text {
                                text: parent.text
                                color: isSelected ? "#ffffff" : "#6a6c96"
                                font.pixelSize: 11
                                font.weight: Font.Bold
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                // Log Box Container
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#080910"
                    border.color: "#1a1c30"
                    border.width: 1
                    radius: 8

                    ScrollView {
                        id: logScrollView
                        anchors.fill: parent
                        anchors.margins: 10
                        clip: true

                        TextArea {
                            id: logArea
                            textFormat: Text.RichText
                            font.family: "Fira Code, JetBrains Mono, DejaVu Sans Mono, Consolas, monospace"
                            font.pixelSize: 12
                            color: "#b8bad8"
                            readOnly: true
                            selectByMouse: true
                            wrapMode: Text.WrapAnywhere
                            background: null
                            leftPadding: 8
                            rightPadding: 8
                            topPadding: 8
                            bottomPadding: 8

                            onImplicitHeightChanged: {
                                logScrollView.contentItem.contentY = Math.max(0, implicitHeight - logScrollView.height)
                            }
                        }
                    }
                }

                // Bottom control buttons (below left)
                RowLayout {
                    spacing: 12
                    Layout.alignment: Qt.AlignLeft
                    Layout.bottomMargin: 10

                    Button {
                        text: "⚙ Display Config"
                        visible: backend.detectedDe === "hyprland"
                        onClicked: {
                            backend.configureDisplay()
                        }
                        background: Rectangle {
                            implicitWidth: 140
                            implicitHeight: 38
                            color: parent.down ? "#16182a" : (parent.hovered ? "#222540" : "#1a1c30")
                            border.color: "#2a2d55"
                            radius: 8
                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                        contentItem: Text {
                            text: parent.text
                            color: "#b8bad8"
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    Button {
                        text: "⏹ Stop Streaming"
                        onClicked: {
                            allLogs = []
                            backend.stopStreaming()
                        }
                        background: Rectangle {
                            implicitWidth: 150
                            implicitHeight: 38
                            color: parent.down ? "#5a1010" : (parent.hovered ? "#c42830" : "#a82028")
                            radius: 8
                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                        contentItem: Text {
                            text: parent.text
                            color: "#ffffff"
                            font.pixelSize: 13
                            font.weight: Font.Bold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }
        }
    }
}
