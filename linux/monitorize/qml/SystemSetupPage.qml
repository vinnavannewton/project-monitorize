import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property bool setupAvailable: false
    property bool applying: false
    property bool statusSucceeded: false
    property string statusMessage: ""
    property bool firstRun: false
    property bool logoutRequired: false
    property var completedActions: ({})
    signal setupCompleted()
    signal cancellationRequested()

    function refreshStatus() {
        let status = backend.getSystemSetupStatus()
        setupAvailable = status["available"] === true
    }

    Component.onCompleted: refreshStatus()
    Connections {
        target: backend
        function onSystemSetupFinished(result) {
            if (!page.applying) return
            page.applying = false
            page.statusSucceeded = result["success"] === true
            let completed = Object.assign({}, page.completedActions)
            let actions = result["actions"] || ({})
            for (let name in actions) {
                if (actions[name]["success"] === true) completed[name] = actions[name]["message"] || "Completed"
            }
            page.completedActions = completed
            if (actions["input"] && actions["input"]["success"] === true) inputCheck.checked = false
            if (actions["firewall"] && actions["firewall"]["success"] === true) firewallCheck.checked = false
            let status = backend.getSystemSetupStatus()
            page.logoutRequired = status["logout_required"] === true
            page.statusMessage = result["message"] || "System setup failed."
            let summaries = []
            for (let name in completed) {
                if (page.statusMessage.indexOf(completed[name]) === -1) summaries.push(completed[name])
            }
            if (summaries.length) page.statusMessage = summaries.join("\n") + "\n" + page.statusMessage
            if (page.logoutRequired && page.statusMessage.indexOf("Log out") === -1)
                page.statusMessage += "\nLog out and back in to enable input in this session."
            if (actions["input"] && actions["input"]["success"] === true && status["input_ready"] !== true && !page.logoutRequired)
                page.statusMessage += "\n" + (status["input_message"] || "Input is not ready in this session.")
        }
    }

    ScrollView {
        id: setupScroll
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
    ColumnLayout {
        width: Math.min(setupScroll.availableWidth - 32, 500)
        x: (setupScroll.availableWidth - width) / 2
        spacing: 16

        Text {
            text: "Finish system setup"
            color: theme.textPrimary
            font.pixelSize: 26
            font.weight: Font.ExtraBold
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: "Select the system changes to authorize. Administrator authorization may be requested. Wi-Fi setup uses NetworkManager authorization separately."
            color: theme.textSecondary
            font.pixelSize: 13
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: options.implicitHeight + 32
            enabled: !page.applying && !page.statusSucceeded
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
                    text: "Allows streaming and discovery through active firewalld network zones or UFW."
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
            text: "System setup needs Polkit and trusted Monitorize setup assets."
            color: theme.errorText
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        TextArea {
            readOnly: true
            selectByMouse: true
            background: null
            text: statusMessage
            visible: statusMessage.length > 0
            color: statusSucceeded ? theme.successText : theme.errorText
            font.pixelSize: 12
            wrapMode: TextEdit.Wrap
            Layout.fillWidth: true
        }

        CustomButton {
            text: "Log out…"
            visible: page.logoutRequired
            enabled: !page.applying && !backend.remoteLoginSetupBusy
            onClicked: backend.requestSessionLogout()
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10

            CustomButton {
                visible: !page.statusSucceeded
                text: page.firstRun ? "Cancel" : "Back"
                primary: false
                enabled: !page.applying
                onClicked: {
                    if (page.firstRun) page.cancellationRequested()
                    else page.StackView.view.pop()
                }
            }

            CustomButton {
                objectName: "systemSetupAuthorizeButton"
                visible: !page.statusSucceeded
                text: applying ? "Authorizing…" : "Authorize and finish setup"
                enabled: setupAvailable && !applying && (inputCheck.checked || firewallCheck.checked)
                onClicked: {
                    applying = true
                    backend.startSystemSetup(inputCheck.checked, firewallCheck.checked)
                }
            }
            CustomButton {
                objectName: "systemSetupDoneButton"
                text: "Done"
                visible: page.statusSucceeded
                onClicked: {
                    if (page.statusSucceeded && page.firstRun) {
                        backend.markSystemSetupDecided()
                        page.setupCompleted()
                    } else page.StackView.view.pop()
                }
            }
        }
        CustomButton {
            text: "Configure Wi-Fi before login…"
            visible: !page.firstRun
            primary: false
            enabled: !page.applying
            onClicked: page.StackView.view.push("BootWifiSetupPage.qml")
        }
    }
    }
}
