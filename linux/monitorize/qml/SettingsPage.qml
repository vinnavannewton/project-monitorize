import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "startupSettingsPage"
    property bool bootOptionsExpanded: false
    property bool settingsLoading: true
    property bool settingsMinimizeToTray: false
    property bool settingsAutostartEnabled: false
    property string settingsError: ""
    property string startupNotice: ""
    property bool savingTimeout: false
    property string timeoutNotice: ""
    property bool timeoutFailed: false
    property int savedTimeout: 20
    property bool startupCleanupPending: false
    property int pendingBootAction: -1
    property int loginAfterBootDisable: -1
    property string sunshineResetMessage: ""
    property bool sunshineResetSucceeded: false
    property var vkmsAuthorization: ({success: false})
    property string vkmsAuthorizationMessage: ""
    property bool vkmsChecking: false
    property var bootStatus: ({available: false, enabled: false, supported: false})
    function startupResultMessage(result) {
        if (!result.success) return ""
        if (result.primary_display_setting)
            return "Login-screen display choice saved for the next boot. Desktop streaming is unchanged."
        if (result.activated)
            return "Remote login enabled. Available now and after future boots."
        return result.message || "Startup settings saved."
    }
    function refreshAuthorization() {
        vkmsChecking = true
        backend.refreshVkmsAuthorization()
    }
    function refreshStartup() {
        bootStatus = backend.getRemoteLoginSetupStatus()
        settingsLoading = true
        bootCheck.checked = bootStatus.enabled === true
        savedTimeout = backend.getDisplayIdleTimeout()
        displayTimeout.value = savedTimeout
        loginPrimaryCheck.checked = bootStatus.create_login_virtual_display === true
        autostartCheck.checked = bootStatus.enabled === true || backend.isAutostartEnabled()
        settingsLoading = false
    }
    StackView.onActivated: backend.refreshVkmsManagement()
    Component.onCompleted: loadAppSettings()
    function loadAppSettings() {
        settingsLoading = true
        let gen = backend.loadGeneralSettings()
        settingsMinimizeToTray = gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false
        settingsAutostartEnabled = backend.isAutostartEnabled()
        minimizeTrayCheck.checked = settingsMinimizeToTray
        autostartCheck.checked = settingsAutostartEnabled
        settingsError = ""
        settingsLoading = false
        refreshStartup()
        refreshAuthorization()
    }

    Connections {
        target: backend
        function onVkmsAuthorizationReady(result) {
            page.vkmsChecking = false
            page.vkmsAuthorization = result
        }
        function onRemoteLoginSetupFinished(result) {
            if (result.repair) {
                page.settingsError = result.success ? "" : (result.message || "Remote-login repair failed.")
                page.startupNotice = result.success ? result.message : ""
                page.refreshStartup()
                if (result.success && !result.request_completed && page.pendingBootAction >= 0) {
                    let requested = page.pendingBootAction === 1
                    Qt.callLater(function() { backend.configureRemoteLogin(requested) })
                }
                if (result.cancelled) {
                    page.pendingBootAction = -1
                    page.loginAfterBootDisable = -1
                }
                if (result.success && result.request_completed) {
                    if (!page.bootStatus.enabled && page.loginAfterBootDisable >= 0) {
                        let error = backend.setAutostartEnabled(page.loginAfterBootDisable === 1)
                        if (error.length > 0) page.settingsError = error
                        page.refreshStartup()
                    }
                    page.pendingBootAction = -1
                    page.loginAfterBootDisable = -1
                }
                return
            }
            if (result.timeout_setting) {
                page.savingTimeout = false
                page.timeoutFailed = !result.success
                page.timeoutNotice = result.message || (result.success ? "Disconnect timer saved." : "Could not save the disconnect timer.")
                page.refreshStartup()
                return
            }
            page.startupCleanupPending = result.cleanup_pending === true
            page.settingsError = result.success ? "" : (result.message || "Startup setup failed.")
            page.startupNotice = page.startupResultMessage(result)
            page.refreshStartup()
            if (result.success && !page.bootStatus.enabled && page.loginAfterBootDisable >= 0) {
                let error = backend.setAutostartEnabled(page.loginAfterBootDisable === 1)
                if (error.length > 0) page.settingsError = error
                page.refreshStartup()
            }
            if (result.success || result.cancelled) {
                page.pendingBootAction = -1
                page.loginAfterBootDisable = -1
            }
        }
        function onRecoveryOperationFinished(result) {
            if (result.operation !== "host_recovery") return
            page.vkmsAuthorizationMessage = result.message || "Host recovery finished."
            page.refreshAuthorization()
        }
        function onSystemSetupFinished(result) {
            page.vkmsAuthorizationMessage = result.success ? "" : (result.message || "Authorization failed.")
            page.refreshAuthorization()
        }
    }

    function saveAppSettings() {
        if (settingsLoading) return
        settingsMinimizeToTray = minimizeTrayCheck.checked
        backend.saveGeneralSettings(settingsMinimizeToTray)
    }

    function saveAutostartSettings() {
        if (settingsLoading) return
        if (!autostartCheck.checked && bootStatus.enabled) {
            // Keep the dependency visible until administrator disable succeeds.
            autostartCheck.checked = true
            loginAfterBootDisable = 0
            page.pendingBootAction = 0
            backend.configureRemoteLogin(false)
            return
        }
        settingsAutostartEnabled = autostartCheck.checked
        settingsError = backend.setAutostartEnabled(settingsAutostartEnabled)
        if (settingsError.length > 0) {
            settingsLoading = true
            settingsAutostartEnabled = backend.isAutostartEnabled()
            autostartCheck.checked = settingsAutostartEnabled
            settingsLoading = false
        }
    }


    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 18
            Text { text: "Settings"; color: theme.textPrimary; font.pixelSize: 28; font.weight: Font.Bold }
            SectionCard {
                title: "GENERAL"; symbol: "settings"; Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "Theme"; color: theme.textPrimary; font.pixelSize: 13 }
                    Item { Layout.fillWidth: true }
                    CustomComboBox {
                        objectName: "themeModeCombo"
                        Layout.preferredWidth: 180
                        model: ["System", "Light", "Dark"]
                        currentIndex: backend ? ["system", "light", "dark"].indexOf(backend.themeMode) : 0
                        onActivated: backend.setThemeMode(["system", "light", "dark"][currentIndex])
                    }
                }
                CustomCheckBox {
                    objectName: "systemAccentCheck"
                    text: "Use system accent color"
                    checked: backend ? backend.useSystemAccent : false
                    keepCheckedBlue: true
                    onClicked: backend.setUseSystemAccent(checked)
                }
                CustomCheckBox {
                    keepCheckedBlue: true
                    id: minimizeTrayCheck; text: "Minimize to tray on close"
                    Layout.fillWidth: true; onCheckedChanged: page.saveAppSettings()
                }
                RowLayout {
                    spacing: 8
                    Layout.fillWidth: true
                    CustomCheckBox {
                        keepCheckedBlue: true
                        id: autostartCheck; objectName: "loginStartupCheck"; text: "Start Monitorize after login"
                        expandableLabel: true
                        onLabelClicked: page.bootOptionsExpanded = !page.bootOptionsExpanded
                        enabled: !backend.remoteLoginSetupBusy
                        Layout.fillWidth: false; onClicked: page.saveAutostartSettings()
                    }
                    StartupHelp { helpText: "Starts Monitorize after you sign in and reach your desktop." }
                    Item { Layout.fillWidth: true }
                    ToolButton {
                        objectName: "bootStartupExpander"
                        text: page.bootOptionsExpanded ? "⌃" : "⌄"
                        Accessible.name: "Show boot startup options"
                        onClicked: page.bootOptionsExpanded = !page.bootOptionsExpanded
                    }
                }
                RowLayout {
                    spacing: 8
                    id: bootOptions
                    objectName: "bootStartupOptions"
                    visible: page.bootOptionsExpanded
                    Layout.fillWidth: true
                    Layout.leftMargin: 24
                    CustomCheckBox {
                        keepCheckedBlue: true
                        id: bootCheck; objectName: "bootStartupCheck"; text: "Also start at boot (login screen)"
                        Layout.fillWidth: false
                        enabled: !backend.remoteLoginSetupBusy && (page.bootStatus.enabled || (autostartCheck.checked && page.bootStatus.available && page.bootStatus.supported && page.bootStatus.live_available === true))
                        onClicked: {
                            page.loginAfterBootDisable = checked ? -1 : 1
                            page.pendingBootAction = checked ? 1 : 0
                            backend.configureRemoteLogin(checked)
                        }
                    }
                    StartupHelp { helpText: "Enables remote login now and after future boots, without restarting your PC.\nAfter signing in, reconnect to the same Moonlight PC. Desktop startup is included." }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    visible: page.bootOptionsExpanded
                    spacing: 8
                    Layout.fillWidth: true
                    Layout.leftMargin: 48
                    CustomCheckBox {
                        id: loginPrimaryCheck
                        objectName: "loginPrimaryDisplayCheck"
                        text: "Create virtual display at login"
                        keepCheckedBlue: true
                        Layout.fillWidth: true
                        enabled: page.bootStatus.enabled === true && page.bootStatus.login_display_policy_available === true && !backend.remoteLoginSetupBusy
                        opacity: enabled ? 1.0 : 0.45
                        onClicked: backend.configureLoginVirtualDisplay(checked)
                    }
                }

                SelectableMessage {
                    objectName: "bootStartupStatusMessage"
                    visible: page.bootOptionsExpanded && (!page.bootStatus.available || (!page.bootStatus.enabled && (!autostartCheck.checked || !page.bootStatus.supported || page.bootStatus.live_available !== true)))
                    text: !page.bootStatus.available || (!page.bootStatus.supported && !page.bootStatus.enabled)
                          ? (page.bootStatus.message || "") : (!autostartCheck.checked ? "Enable Start Monitorize after login first." : (page.bootStatus.live_message || "Install matching live-enrollment service updates before enabling boot startup."))
                    color: theme.textSecondary; Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                SelectableMessage {
                    objectName: "remoteLoginRepairExplanation"
                    text: page.bootStatus.repair_message || ""
                    visible: repairRemoteLoginButton.visible
                    Layout.fillWidth: true
                }
                CustomButton {
                    id: repairRemoteLoginButton
                    objectName: "repairRemoteLoginButton"
                    text: "Repair remote login"
                    visible: page.bootOptionsExpanded && page.bootStatus.repair_kind === "source" && (!page.bootStatus.available || page.bootStatus.repair_needed || page.settingsError.length > 0)
                    enabled: !backend.remoteLoginSetupBusy && !backend.sessionBusy
                    onClicked: backend.repairRemoteLogin()
                }
                CustomButton {
                    objectName: "resumeRemoteLoginMaintenance"
                    text: "Resume remote-login recovery"
                    visible: page.bootOptionsExpanded && page.bootStatus.maintenance_pending === true && page.bootStatus.recovery_available === true
                    enabled: !backend.remoteLoginSetupBusy && !backend.sessionBusy
                    onClicked: backend.recoverRemoteLogin(page.pendingBootAction === 0)
                }
                CustomButton {
                    objectName: "refreshBootStartupStatus"
                    text: "Refresh startup status"
                    visible: page.bootOptionsExpanded
                    enabled: !backend.remoteLoginSetupBusy
                    onClicked: page.refreshStartup()
                }
                SelectableMessage {
                    objectName: "startupErrorMessage"
                    text: page.settingsError; visible: text.length > 0
                    color: theme.errorText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                SelectableMessage {
                    objectName: "startupNoticeText"
                    text: backend.remoteLoginSetupBusy && !page.savingTimeout ? (backend.remoteLoginSetupProgress || "Applying startup settings…") : page.startupNotice
                    visible: text.length > 0
                    color: theme.textSecondary; Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                CustomButton {
                    text: "Retry display release"
                    visible: page.startupCleanupPending
                    enabled: !backend.remoteLoginSetupBusy
                    onClicked: backend.configureRemoteLogin(false)
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border; Layout.topMargin: 8; Layout.bottomMargin: 8 }
                RowLayout {
                    objectName: "displayIdleTimeoutRow"
                    Layout.fillWidth: true
                    Text { text: "Remove virtual display after disconnect"; color: theme.textPrimary; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    SpinBox {
                        id: displayTimeout
                        objectName: "displayIdleTimeoutSeconds"
                        from: 1; to: 600; value: 20; editable: true
                        enabled: !backend.remoteLoginSetupBusy
                        implicitWidth: 124
                        implicitHeight: 42
                        leftPadding: 32
                        rightPadding: 32
                        opacity: enabled ? 1.0 : 0.45
                        contentItem: TextInput {
                            text: displayTimeout.textFromValue(displayTimeout.value, displayTimeout.locale)
                            color: theme.cardTextPrimary
                            selectionColor: theme.accent
                            selectedTextColor: theme.buttonText
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            horizontalAlignment: TextInput.AlignHCenter
                            verticalAlignment: TextInput.AlignVCenter
                            readOnly: !displayTimeout.editable
                            validator: displayTimeout.validator
                            inputMethodHints: Qt.ImhDigitsOnly
                            selectByMouse: true
                        }
                        background: Rectangle {
                            color: theme.surface
                            border.color: (displayTimeout.activeFocus || displayTimeout.contentItem.activeFocus) ? theme.accent : (displayTimeout.hovered ? theme.borderHover : theme.border)
                            radius: theme.controlRadius
                        }
                        down.indicator: Rectangle {
                            x: 1; y: 1
                            width: 30; height: displayTimeout.height - 2
                            radius: theme.controlRadius
                            color: displayTimeout.down.pressed ? theme.surfaceAlt : (displayTimeout.down.hovered ? theme.borderHover : "transparent")
                            Text {
                                anchors.centerIn: parent
                                text: "−"; font.pixelSize: 18
                                color: displayTimeout.value > displayTimeout.from ? theme.cardTextPrimary : theme.textMuted
                            }
                        }
                        up.indicator: Rectangle {
                            x: displayTimeout.width - width - 1; y: 1
                            width: 30; height: displayTimeout.height - 2
                            radius: theme.controlRadius
                            color: displayTimeout.up.pressed ? theme.surfaceAlt : (displayTimeout.up.hovered ? theme.borderHover : "transparent")
                            Text {
                                anchors.centerIn: parent
                                text: "+"; font.pixelSize: 18
                                color: displayTimeout.value < displayTimeout.to ? theme.cardTextPrimary : theme.textMuted
                            }
                        }
                    }
                    Text { text: "seconds"; color: theme.textSecondary }
                    CustomButton {
                        id: saveDisplayTimeout
                        objectName: "saveDisplayIdleTimeout"
                        implicitWidth: 80
                        text: page.savingTimeout ? "Saving…" : "Save"
                        enabled: !backend.remoteLoginSetupBusy && displayTimeout.value !== page.savedTimeout
                        onClicked: { page.savingTimeout = true; backend.configureDisplayIdleTimeout(displayTimeout.value) }
                    }
                    StartupHelp { helpText: "Remove the virtual display after the last client disconnects. Reconnecting cancels removal.\nApplies to manually started sessions and remote login. Saved changes apply to future disconnects." }
                }
                SelectableMessage {
                    objectName: "timeoutResultMessage"
                    text: page.savingTimeout ? "Saving disconnect timer…" : page.timeoutNotice
                    visible: text.length > 0
                    color: page.timeoutFailed ? theme.errorText : theme.textSecondary
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                }

            }
            SectionCard {
                title: "MISCELLANEOUS"; symbol: "extras"; Layout.fillWidth: true
                SettingsActionRow {
                    title: "Wi-Fi before login"
                    description: "Configure a NetworkManager Wi-Fi profile to connect before desktop login."
                    symbol: "wifi-settings"; actionText: "Configure"
                    Layout.fillWidth: true
                    onClicked: page.StackView.view.push("BootWifiSetupPage.qml")
                }
                SettingsActionRow {
                    title: "Passwordless display creation (VKMS)"
                    description: page.vkmsChecking ? "Checking authorization…" : page.vkmsAuthorizationMessage.length > 0 ? page.vkmsAuthorizationMessage : page.vkmsAuthorization.success
                        ? (page.vkmsAuthorization.repair_required ? page.vkmsAuthorization.message : page.vkmsAuthorization.unattended_enabled ? "Enabled for your user. Display creation does not request a password." : "Authorize your user once for virtual display creation without recurring password prompts.")
                        : (page.vkmsAuthorization.message || "Install or upgrade monitorize-vkms first.")
                    symbol: "key-slash"
                    actionText: page.vkmsChecking ? "Checking…" : !page.vkmsAuthorization.success ? "Recover host" : page.vkmsAuthorization.repair_required ? "Reauthorize" : page.vkmsAuthorization.unattended_enabled ? "Disable" : "Enable"
                    actionEnabled: !page.vkmsChecking && !backend.isStreaming && !backend.sessionBusy && !backend.systemSetupRunning
                    Layout.fillWidth: true
                    onClicked: {
                        page.vkmsAuthorizationMessage = ""
                        if (!page.vkmsAuthorization.success) backend.recoverVkmsHost()
                        else backend.setVkmsUnattended(page.vkmsAuthorization.repair_required || !page.vkmsAuthorization.unattended_enabled)
                    }
                }
                SettingsActionRow {
                    title: "Reset Sunshine settings to defaults"
                    description: "Restore Sunshine settings to their default values."
                    symbol: "reset"; actionText: "Reset to defaults"
                    Layout.fillWidth: true
                    actionEnabled: !backend.applyingConfiguration
                    onClicked: sunshineResetDialog.open()
                }
                SelectableMessage {
                    objectName: "sunshineResetResultMessage"
                    text: page.sunshineResetMessage
                    visible: text.length > 0
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: page.sunshineResetSucceeded ? theme.textSecondary : theme.errorText
                }
                SettingsActionRow {
                    title: "Remove virtual display"
                    description: "Remove Monitorize’s leftover virtual displays."
                    symbol: "display-minus"
                    actionText: backend.virtualDisplayCleanupRunning ? "Removing…" : "Remove display"
                    Layout.fillWidth: true
                    actionEnabled: !backend.isStreaming && !backend.virtualDisplayCleanupRunning
                    onClicked: root.removeStagnantVirtualDisplays()
                }
                SettingsActionRow {
                    visible: backend.sunshineAvailable
                    title: "Clear restore tokens"
                    description: "Delete saved screen-sharing restore tokens."
                    symbol: "document-x"; actionText: "Clear tokens"
                    actionEnabled: !backend.isStreaming
                    Layout.fillWidth: true
                    onClicked: root.clearRestoreTokens()
                }
                CustomButton {
                    visible: backend.systemSetupAvailable
                    text: "Run system setup again"; primary: false; Layout.fillWidth: true
                    onClicked: page.StackView.view.push("SystemSetupPage.qml")
                }
            }
            VkmsManagementCard { Layout.fillWidth: true }
        }
    }

    Dialog {
        id: sunshineResetDialog
        anchors.centerIn: parent
        title: "Reset Sunshine settings?"
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: Math.min(420, page.width - 40)
        contentItem: Text {
            text: "Load Monitorize's default streaming choices for both displays? Use Apply in Configure to restart with these changes. Pairing and saved display layouts will be kept."
            color: theme.textPrimary
            wrapMode: Text.WordWrap
        }
        onAccepted: {
            let result = backend.resetSunshineSettings()
            page.sunshineResetSucceeded = result["success"] === true
            page.sunshineResetMessage = result["message"] || "Could not reset Sunshine settings"
        }
    }
}
