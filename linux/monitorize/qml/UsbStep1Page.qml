import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool hasStartedScan: false
    property bool autoScanStarted: false
    property bool isScanSuccessful: backend.usbStatusText === "Device ready!" || backend.usbStatusText.indexOf("Warning:") === 0

    function startAutomaticScanIfReady() {
        if (hasStartedScan || autoScanStarted || backend.usbBusy)
            return
        let onlineSerials = []
        for (let index = 0; index < backend.recentUsbDevices.length; index++) {
            let device = backend.recentUsbDevices[index]
            if (device.online)
                onlineSerials.push(device.serial)
        }
        if (onlineSerials.length !== 1)
            return
        autoScanStarted = true
        hasStartedScan = true
        backend.startUsbScan(onlineSerials[0])
    }

    Component.onCompleted: {
        hasStartedScan = false;
        autoScanStarted = false;
        backend.resetUsbStatus();
        startAutomaticScanIfReady();
    }

    Connections {
        target: backend
        function onRecentUsbDevicesChanged() {
            page.startAutomaticScanIfReady()
        }
    }

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 24
        width: Math.min(parent.width - 40, 500)

        Text {
            text: "USB Mode  ·  Step 1 of 2"
            font.pixelSize: 12
            font.weight: Font.Bold
            color: theme.textMuted
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: !hasStartedScan ? "Is the Android Device connected via USB?" : (isScanSuccessful ? "Connection Established!" : "Scanning for Connected Android Device...")
            font.pixelSize: 18
            font.weight: Font.Bold
            color: theme.textPrimary
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: !hasStartedScan
                ? "Please connect your Android device to this computer using a USB cable, and ensure that USB Debugging is enabled in Settings."
                : backend.usbStatusText
            font.pixelSize: 13
            color: !hasStartedScan ? theme.textSecondary : (isScanSuccessful ? "#2e7d32" : "#c62828")
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
            visible: hasStartedScan && backend.usbBusy

            Rectangle {
                anchors.fill: parent
                radius: 30
                color: "transparent"
                border.color: theme.border
                border.width: 4
            }

            Rectangle {
                id: activeArc
                width: 60
                height: 60
                radius: 30
                color: "transparent"
                border.color: theme.accent
                border.width: 4
                clip: true

                Rectangle {
                    width: 60
                    height: 30
                    color: theme.background
                    anchors.bottom: parent.bottom
                    visible: !backend.usbBusy
                }
            }

            RotationAnimator {
                target: activeArc
                from: 0
                to: 360
                duration: 1000
                running: backend.usbBusy
                loops: Animation.Infinite
            }
        }

        RowLayout {
            spacing: 20
            Layout.alignment: Qt.AlignHCenter

            CustomButton {
                text: "Yes, Start Scan"
                visible: !hasStartedScan
                implicitWidth: 150
                onClicked: {
                    page.hasStartedScan = true
                    backend.startUsbScan()
                }
            }

            CustomButton {
                text: "Retry Scan"
                visible: hasStartedScan && !backend.usbBusy && !isScanSuccessful
                implicitWidth: 120
                onClicked: {
                    backend.startUsbScan()
                }
            }
        }

        // Recent USB Connections
        ColumnLayout {
            visible: !hasStartedScan && backend.recentUsbDevices.length > 0
            Layout.fillWidth: true
            Layout.topMargin: 16
            spacing: 8

            Text {
                text: "Recent Connections"
                font.pixelSize: 12
                font.weight: Font.Bold
                color: theme.textMuted
                Layout.alignment: Qt.AlignLeft
            }

            Repeater {
                model: backend.recentUsbDevices

                Rectangle {
                    Layout.fillWidth: true
                    height: 52
                    radius: theme.controlRadius
                    color: theme.surface
                    border.color: theme.border
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 12

                        Rectangle {
                            width: 8
                            height: 8
                            radius: 4
                            color: modelData.online ? "#4caf50" : theme.textMuted
                            Layout.alignment: Qt.AlignVCenter
                        }

                        ColumnLayout {
                            spacing: 1
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter

                            Text {
                                text: modelData.name
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                color: theme.textPrimary
                            }

                            Text {
                                text: "Serial: " + modelData.serial
                                font.pixelSize: 11
                                color: theme.textSecondary
                            }
                        }

                        Text {
                            text: modelData.online ? "Online" : "Offline"
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            color: modelData.online ? theme.accent : theme.textMuted
                            Layout.alignment: Qt.AlignVCenter
                        }
                    }
                }
            }
        }

        Timer {
            interval: 600
            running: page.isScanSuccessful
            repeat: false
            onTriggered: {
                page.StackView.view.replace("UsbStep2Page.qml")
            }
        }
    }
}
