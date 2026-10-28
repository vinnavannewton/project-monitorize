import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    objectName: "incomingPairingPopup"
    required property var coordinator
    property string requestKey: ""
    readonly property var request: coordinator.request
    modal: true
    anchors.centerIn: parent
    width: Math.min(440, parent.width - 32)
    padding: 22
    closePolicy: Popup.NoAutoClose
    background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }

    function synchronize() {
        let key = request.id ? request.instance + ":" + request.id : ""
        if (key !== requestKey) {
            pin.text = ""
            requestKey = key
        }
        if (key && backend.uiVisible) open()
        else close()
        if (coordinator.phase === "done") doneTimer.restart()
        else doneTimer.stop()
    }
    Connections {
        target: popup.coordinator
        function onChanged() { popup.synchronize() }
    }
    Connections {
        target: backend
        function onUiVisibleChanged() { popup.synchronize() }
    }
    Timer { id: doneTimer; interval: 1800; onTriggered: popup.coordinator.dismiss() }
    Component.onCompleted: synchronize()
    ColumnLayout {
        width: parent.width
        spacing: 12
        Text {
            text: "Moonlight pairing request"
            textFormat: Text.PlainText
            color: theme.textPrimary; font.pixelSize: 18; font.weight: Font.Bold
            Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
        Text {
            objectName: "incomingPairingClient"
            text: (popup.request.name || "Unknown client") + " wants to pair."
            textFormat: Text.PlainText
            color: theme.textPrimary
            Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
        }
        Text {
            objectName: "incomingPairingAddress"
            text: (popup.request.address || "Unknown address") + " · Display " + (popup.request.instance || 1)
            textFormat: Text.PlainText
            color: theme.textSecondary
            Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
        }
        Text {
            text: "Enter the four-digit PIN shown in this client's Moonlight app."
            visible: popup.coordinator.phase === "pin"
            color: theme.textSecondary
            Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
        CustomTextField {
            id: pin
            objectName: "incomingPairingPin"
            visible: popup.coordinator.phase === "pin"
            enabled: !popup.coordinator.busy
            Layout.fillWidth: true
            maximumLength: 4
            echoMode: TextInput.Password
            inputMethodHints: Qt.ImhDigitsOnly | Qt.ImhSensitiveData
            validator: RegularExpressionValidator { regularExpression: /[0-9]{0,4}/ }
            onAccepted: { if (text.length === 4) popup.coordinator.submit(text) }
        }
        Text {
            objectName: "incomingPairingResult"
            text: popup.coordinator.message
            textFormat: Text.PlainText
            visible: text.length > 0
            color: popup.coordinator.phase === "done" ? theme.successText : theme.errorText
            Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10
            CustomButton {
                objectName: "rejectIncomingPairing"
                text: "Reject"
                primary: false
                visible: popup.coordinator.phase !== "done"
                enabled: !popup.coordinator.busy
                onClicked: { pin.text = ""; popup.coordinator.reject() }
            }
            CustomButton {
                text: "Close"
                primary: false
                visible: popup.coordinator.phase === "pin" || popup.coordinator.phase === "done"
                enabled: !popup.coordinator.busy
                onClicked: { pin.text = ""; popup.coordinator.dismiss() }
            }
            CustomButton {
                objectName: "acceptIncomingPairing"
                text: popup.coordinator.busy ? "Working…" : popup.coordinator.phase === "request" ? "Accept" : "Pair"
                visible: popup.coordinator.phase !== "done"
                enabled: !popup.coordinator.busy && (popup.coordinator.phase === "request" || pin.text.length === 4)
                onClicked: {
                    if (popup.coordinator.phase === "request") {
                        popup.coordinator.accept()
                        pin.forceActiveFocus()
                    } else {
                        popup.coordinator.submit(pin.text)
                        pin.text = ""
                    }
                }
            }
        }
    }
}
