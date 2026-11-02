import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "systemSetupPage"
    property bool setupAvailable: false
    property bool applying: false
    property bool helperBusy: false
    property bool helperInterrupted: false
    property bool statusSucceeded: false
    property bool statusFailed: false
    property string statusMessage: ""
    property bool firstRun: false
    property bool inputReady: false
    property string inputStateMessage: ""
    property bool completionSent: false
    signal configurationSaved()
    property bool logoutRequired: false
    property var completedActions: ({})
    property var firewallScope: ({checking: true, zones: []})
    property var selectedZones: []
    property bool scopeLoaded: false
    property bool waitingForScope: false
    signal setupCompleted()
    signal cancellationRequested()

    function refreshStatus() {
        let status = backend.getSystemSetupStatus()
        setupAvailable = status["available"] === true
        page.helperBusy = status["busy"] === true
        page.helperInterrupted = status["interrupted"] === true
        page.statusSucceeded = false
        page.statusFailed = !setupAvailable || page.helperInterrupted || (status["request_succeeded"] === false && !!status["result_message"])
        page.completedActions = status["completed_actions"] || ({})
        page.logoutRequired = status["logout_required"] === true
        page.inputReady = status["input_ready"] === true
        page.inputStateMessage = status["input_message"] || "Input has not been configured."
        inputCheck.checked = !page.completedActions["input"] || (!status["input_ready"] && !page.logoutRequired)
        firewallCheck.checked = !page.completedActions["firewall"] || status["firewall_scope_current"] === false
        if (!setupAvailable) page.statusMessage = status["message"] || "System setup prerequisites need repair."
        else if (page.helperBusy) page.statusMessage = "System setup is still running. Waiting for its verified result…"
        else if (status["interrupted"]) page.statusMessage = status["message"] || "Previous setup was interrupted. Completed steps were recovered; retry verifies any unfinished steps."
        else if (page.logoutRequired) page.statusMessage = status["input_message"]
        else if (status["result_message"]) page.statusMessage = status["result_message"]
        let selected = status["selected_actions"] || []
        let inputSelected = selected.indexOf("input") !== -1
        let firstRunSelection = inputSelected || selected.indexOf("firewall") !== -1
        if (setupAvailable && !page.helperBusy && !status["interrupted"] &&
                status["request_succeeded"] && firstRunSelection &&
                (!inputSelected || status["input_ready"] || page.logoutRequired)) {
            if (selected.indexOf("firewall") !== -1 && typeof backend.refreshFirewallScope === "function") {
                page.waitingForScope = true
                backend.refreshFirewallScope()
            } else page.statusSucceeded = true
        }
        if (!page.helperBusy && !page.helperInterrupted && status["firewall_scope_current"] === false && status["firewall_scope_message"])
            page.statusMessage = status["firewall_scope_message"]
    }

    function receiveScope(scope) {
        let available = (scope["zones"] || []).map(function(zone) { return zone.name })
        let choices = selectedZones.filter(function(name) { return available.indexOf(name) !== -1 })
        if (!scopeLoaded && available.length === 1) choices = available.slice()
        selectedZones = choices
        scopeLoaded = true
        firewallScope = scope
        if (waitingForScope) {
            waitingForScope = false
            statusSucceeded = scope["scope_current"] === true
            if (!statusSucceeded) {
                firewallCheck.checked = true
                statusMessage = scope["message"] || "Firewall access could not be verified. Review network selection and retry."
            }
        } else if (!page.helperBusy && !page.helperInterrupted && scope["scope_current"] === false && completedActions["firewall"]) {
            statusSucceeded = false
            firewallCheck.checked = true
            statusMessage = scope["message"] || "Review firewall network access."
        }
    }
    Component.onCompleted: {
        refreshStatus()
        if (typeof backend.refreshFirewallScope === "function") backend.refreshFirewallScope()
    }
    Timer {
        interval: 1000
        repeat: true
        running: page.helperBusy && !page.applying
        onTriggered: page.refreshStatus()
    }
    Connections {
        target: backend
        ignoreUnknownSignals: true
        function onFirewallScopeReady(scope) { page.receiveScope(scope) }
        function onSystemSetupFinished(result) {
            if (!page.applying) return
            page.applying = false
            page.helperInterrupted = false
            page.statusSucceeded = result["success"] === true
            page.statusFailed = !page.statusSucceeded
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
        page.inputReady = status["input_ready"] === true
        page.inputStateMessage = status["input_message"] || "Input has not been configured."
            if (actions["firewall"] && actions["firewall"]["success"] === true) {
                if (status["firewall_scope_checking"]) {
                    page.waitingForScope = page.statusSucceeded
                    page.statusSucceeded = false
                } else if (status["firewall_scope_current"] === false) {
                    page.statusSucceeded = false
                    firewallCheck.checked = true
                }
            }
            if (actions["input"] && actions["input"]["success"] === true && status["input_ready"] !== true && !page.logoutRequired) {
                page.statusSucceeded = false
                page.waitingForScope = false
                inputCheck.checked = true
            }
            page.statusMessage = result["message"] || "System setup failed."
            if (Object.keys(actions).length) {
                let outcomes = []
                for (let name in actions)
                    outcomes.push(name === "input" && actions[name]["success"] ? page.inputStateMessage : actions[name]["message"])
                page.statusMessage = outcomes.join("\n")
            }
            if (completed["input"]) completed["input"] = page.inputStateMessage
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

    function completeSetup() {
        if (!statusSucceeded || completionSent) return
        completionSent = true
        backend.markSystemSetupDecided()
        configurationSaved()
        setupCompleted()
    }
    Timer {
        interval: 300
        running: page.firstRun && page.statusSucceeded && !page.completionSent
        onTriggered: page.completeSetup()
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
            enabled: !page.applying && !page.helperBusy && !page.statusSucceeded
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
                    text: page.inputReady ? "Recheck input permissions" : "Enable touch and input"
                    Layout.fillWidth: true
                }


                Text {
                    text: page.inputStateMessage
                    font.bold: page.inputReady || page.logoutRequired
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
                    text: "Choose the firewalld networks that may receive streaming and discovery traffic. UFW permissions apply to all networks. Network changes are checked automatically."
                    color: theme.cardTextMuted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    leftPadding: 24
                }
                Text {
                    visible: firewallCheck.checked && typeof backend.refreshFirewallScope === "function" && !page.scopeLoaded
                    text: "Checking firewall networks…"
                    color: theme.cardTextMuted
                    Layout.fillWidth: true
                }
                Repeater {
                    model: firewallCheck.checked && page.firewallScope["running"] ? page.firewallScope["zones"] : []
                    delegate: CustomCheckBox {
                        required property var modelData
                        objectName: "firewallZone_" + modelData.name
                        Layout.fillWidth: true
                        text: modelData.name + (modelData.interfaces.length ? " · " + modelData.interfaces.join(", ") : "") +
                              (modelData.sources.length ? " · " + modelData.sources.join(", ") : "") +
                              (modelData.default ? " (default network)" : "")
                        checked: page.selectedZones.indexOf(modelData.name) !== -1
                        onToggled: {
                            let choices = page.selectedZones.filter(function(name) { return name !== modelData.name })
                            if (checked) choices.push(modelData.name)
                            page.selectedZones = choices
                        }
                    }
                }
                Text {
                    visible: firewallCheck.checked && page.scopeLoaded && page.firewallScope["available"] === false
                    text: page.firewallScope["message"] || "Could not inspect firewall networks."
                    color: theme.errorText
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                CustomButton {
                    visible: firewallCheck.checked && page.scopeLoaded
                    text: "Refresh networks"
                    primary: false
                    onClicked: {
                        page.scopeLoaded = false
                        backend.refreshFirewallScope()
                    }
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
            visible: statusMessage.length > 0 && !(page.firstRun && page.statusSucceeded)
            color: page.statusFailed ? theme.errorText : statusSucceeded ? theme.successText : theme.textSecondary
            font.pixelSize: 12
            wrapMode: TextEdit.Wrap
            Layout.fillWidth: true
        }

        CustomButton {
            text: "Log out…"
            visible: page.logoutRequired && !page.firstRun
            enabled: !page.applying && !page.helperBusy && !backend.remoteLoginSetupBusy
            onClicked: {
                if (page.statusSucceeded && page.firstRun) backend.markSystemSetupDecided()
                backend.requestSessionLogout()
            }
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
                text: applying ? "Applying setup…" : "Authorize and finish setup"
                enabled: setupAvailable && !helperBusy && !applying && !page.waitingForScope &&
                         (inputCheck.checked || firewallCheck.checked) &&
                         (!firewallCheck.checked || typeof backend.refreshFirewallScope !== "function" ||
                          (page.scopeLoaded && page.firewallScope["available"] === true &&
                           (!page.firewallScope["running"] || page.selectedZones.length > 0)))
                onClicked: {
                    applying = true
                    if (typeof backend.startScopedSystemSetup === "function")
                        backend.startScopedSystemSetup(inputCheck.checked, firewallCheck.checked, page.selectedZones)
                    else backend.startSystemSetup(inputCheck.checked, firewallCheck.checked)
                }
            }
            CustomButton {
                objectName: "systemSetupDoneButton"
                text: "Done"
                visible: page.statusSucceeded && !page.firstRun
                onClicked: {
                    if (page.statusSucceeded && page.firstRun) {
                        page.completeSetup()
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
