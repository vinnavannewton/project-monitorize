import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool hasStartedScan: false
    property bool isScanSuccessful: backend.usbStatusText === "Device ready!" || backend.usbStatusText.indexOf("Warning:") === 0

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
            color: theme.textMuted
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: !hasStartedScan ? "Is the tablet connected via USB?" : (isScanSuccessful ? "Connection Established!" : "Scanning for Connected Android Device...")
            font.pixelSize: 18
            font.weight: Font.Bold
            color: theme.textPrimary
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: !hasStartedScan
                ? "Please connect your Android tablet to this computer using a USB cable, and ensure that USB Debugging is enabled in Settings."
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
