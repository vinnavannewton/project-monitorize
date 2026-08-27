import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool setupAvailable: false
    property bool applying: false
    property bool statusSucceeded: false
    property string statusMessage: ""

    function refreshStatus() {
        let status = backend.getSystemSetupStatus()
        setupAvailable = status["available"] === true
    }

    Component.onCompleted: refreshStatus()

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 500)
        spacing: 16

        Text {
            text: "Finish system setup"
            color: theme.textPrimary
            font.pixelSize: 26
            font.weight: Font.ExtraBold
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: "Select the system changes to authorize. Monitorize will request your system password once."
            color: theme.textSecondary
            font.pixelSize: 13
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: options.implicitHeight + 32
            color: theme.surface
            border.color: theme.border
            radius: theme.cardRadius

            ColumnLayout {
                id: options
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12

                CustomCheckBox {
                    id: inputCheck
                    checked: true
                    text: "Enable touch and input"
                    Layout.fillWidth: true
                }

                Text {
                    text: "Adds your user to monitorize-input for /dev/uinput. Log out and back in afterward."
                    color: theme.cardTextMuted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    leftPadding: 24
                }

                CustomCheckBox {
                    id: firewallCheck
                    checked: true
                    text: "Allow Moonlight through the firewall"
                    Layout.fillWidth: true
                }

                Text {
                    text: "Opens only the Monitorize Sunshine streaming ports when firewalld or UFW is active."
                    color: theme.cardTextMuted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    leftPadding: 24
                }
            }
        }

        Text {
            visible: !setupAvailable
            text: "System setup is available from the Monitorize RPM or DEB package."
            color: "#fca5a5"
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Text {
            text: statusMessage
            visible: statusMessage.length > 0
            color: statusSucceeded ? "#86efac" : "#fca5a5"
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10

            CustomButton {
                text: "Back"
                primary: false
                onClicked: page.StackView.view.pop()
            }

            CustomButton {
                text: applying ? "Authorizing…" : "Authorize and finish setup"
                enabled: setupAvailable && !applying && (inputCheck.checked || firewallCheck.checked)
                onClicked: {
                    applying = true
                    let result = backend.applySystemSetup(inputCheck.checked, firewallCheck.checked)
                    applying = false
                    statusSucceeded = result["success"] === true
                    statusMessage = result["message"] || "System setup failed."
                }
            }
        }
    }
}
