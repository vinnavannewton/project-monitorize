import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Item {
    id: page
    objectName: "startupSettingsPage"
    readonly property bool compact: width < 640
    property bool startupDetailsExpanded: false
    readonly property bool canStartAtBoot: bootStatus.enabled === true
        || (bootStatus.available && bootStatus.supported && bootStatus.live_available === true)
    readonly property int startupMode: bootStatus.enabled === true ? 2 : (settingsAutostartEnabled ? 1 : 0)
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
            return result.pairing_required ? result.message : "Remote login enabled. Available now and after future boots."
        return result.message || "Startup settings saved."
    }
    function refreshAuthorization() {
        vkmsChecking = true
        backend.refreshVkmsAuthorization()
    }
    function refreshStartup() {
        bootStatus = backend.getRemoteLoginSetupStatus()
        settingsLoading = true
        settingsAutostartEnabled = backend.isAutostartEnabled()
        savedTimeout = backend.getDisplayIdleTimeout()
        displayTimeout.value = savedTimeout
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
        settingsError = ""
        settingsLoading = false
        refreshStartup()
        refreshAuthorization()
    }

    Connections {
        target: backend
        function onRemoteLoginRepairOptionsReady(result) {
            page.bootStatus = Object.assign({}, page.bootStatus, result)
        }
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

    function selectStartupMode(mode) {
        if (settingsLoading || backend.remoteLoginSetupBusy || startupMode === mode) return
        settingsError = ""
        startupNotice = ""
        if (mode === 2 || bootStatus.enabled === true) {
            loginAfterBootDisable = mode === 2 ? -1 : mode
            pendingBootAction = mode === 2 ? 1 : 0
            backend.queueBootStartupChange(mode === 2)
        } else {
            settingsError = backend.setAutostartEnabled(mode === 1)
            refreshStartup()
        }
    }

    component StartupChoice: Basic.RadioButton {
        id: choice
        required property int mode
        required property string detail
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Accessible.description: choice.detail
        autoExclusive: false
        checkable: false
        checked: page.startupMode === mode
        enabled: !backend.remoteLoginSetupBusy && (mode !== 2 || page.canStartAtBoot)
        contentItem: Column {
            leftPadding: choice.indicator.width + choice.spacing
            spacing: 4
            Text {
                width: parent.width - parent.leftPadding
                text: choice.text
                color: choice.enabled ? theme.textPrimary : theme.textMuted
                font.pixelSize: 14
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width - parent.leftPadding
                text: choice.detail
                color: theme.textSecondary
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
        Accessible.checked: checked
        onClicked: page.selectStartupMode(mode)
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
                Text {
                    text: "Startup"
                    color: theme.textPrimary
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                }
                StartupChoice {
                    objectName: "manualStartupRadio"
                    mode: 0
                    text: "Start manually"
                    detail: "Open Monitorize when you need it."
                }
                StartupChoice {
                    objectName: "loginStartupRadio"
                    mode: 1
                    text: "Start after sign-in"
                    detail: "Start with your desktop session."
                }
                StartupChoice {
                    objectName: "bootStartupRadio"
                    mode: 2
                    text: "Start at the login screen"
                    detail: "Connect before signing in. Desktop startup is included."
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.startupMode === 2
                    Label {
                        text: "Display at the login screen"
                        color: theme.textPrimary
                    }
                    CustomComboBox {
                        id: loginDisplay
                        objectName: "loginScreenDisplayCombo"
                        Accessible.name: "Display at the login screen"
                        Layout.fillWidth: true
                        model: ["Physical display", "Virtual display"]
                        disabledIndex: page.bootStatus.existing_login_display_only ? 1 : -1
                        currentIndex: page.bootStatus.create_login_virtual_display === true ? 1 : 0
                        enabled: page.bootStatus.login_display_policy_available === true && !backend.remoteLoginSetupBusy
                            && (!page.bootStatus.existing_login_display_only || page.bootStatus.create_login_virtual_display === true)
                        onActivated: function(index) {
                            currentIndex = Qt.binding(() => page.bootStatus.create_login_virtual_display === true ? 1 : 0)
                            if (index !== currentIndex && (index === 0 || !page.bootStatus.existing_login_display_only))
                                backend.configureLoginVirtualDisplay(index === 1)
                        }
                    }
                    SelectableMessage {
                        visible: page.bootStatus.existing_login_display_only === true
                        text: "This login screen streams an existing physical display."
                        color: theme.textSecondary
                        Layout.fillWidth: true
                    }
                }
                SelectableMessage {
                    objectName: "bootStartupStatusMessage"
                    visible: !page.canStartAtBoot
                    text: !page.bootStatus.available || !page.bootStatus.supported
                          ? (page.bootStatus.message || "") : (page.bootStatus.live_message || "Install matching service updates to enable login-screen startup.")
                    color: theme.textSecondary
                    Layout.fillWidth: true
                }
                ToolButton {
                    text: page.startupDetailsExpanded ? "Hide startup details" : "Startup details"
                    onClicked: page.startupDetailsExpanded = !page.startupDetailsExpanded
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
                    visible: page.startupDetailsExpanded && page.bootStatus.repair_kind === "source" && (!page.bootStatus.available || page.bootStatus.repair_needed || page.settingsError.length > 0)
                    enabled: !backend.remoteLoginSetupBusy && !backend.sessionBusy
                    onClicked: backend.repairRemoteLogin()
                }
                CustomButton {
                    objectName: "resumeRemoteLoginMaintenance"
                    text: "Resume remote-login recovery"
                    visible: page.startupDetailsExpanded && page.bootStatus.maintenance_pending === true && page.bootStatus.recovery_available === true
                    enabled: !backend.remoteLoginSetupBusy && !backend.sessionBusy
                    onClicked: backend.recoverRemoteLogin(page.pendingBootAction === 0)
                }
                CustomButton {
                    objectName: "rerunRemoteLoginSetup"
                    text: "Re-run remote-login setup"
                    visible: page.startupDetailsExpanded && page.bootStatus.repair_kind !== "source"
                    enabled: !backend.remoteLoginSetupBusy && !backend.sessionBusy && page.startupMode !== 0
                        && page.bootStatus.available && page.bootStatus.supported && page.bootStatus.live_available === true
                    onClicked: {
                        page.pendingBootAction = 1
                        backend.configureRemoteLogin(true)
                    }
                }
                CustomButton {
                    objectName: "refreshBootStartupStatus"
                    text: "Refresh startup status"
                    visible: page.startupDetailsExpanded
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
                GridLayout {
                    objectName: "displayIdleTimeoutRow"
                    columns: page.compact ? 3 : 5
                    Layout.fillWidth: true
                    Text { text: "Remove virtual display after disconnect"; color: theme.textPrimary; Layout.fillWidth: true; wrapMode: Text.WordWrap; Layout.row: 0; Layout.column: 0; Layout.columnSpan: page.compact ? 3 : 1 }
                    SpinBox {
                        id: displayTimeout
                        objectName: "displayIdleTimeoutSeconds"
                        Layout.row: page.compact ? 1 : 0
                        Layout.column: page.compact ? 0 : 1
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
                    Text { text: "seconds"; color: theme.textSecondary; Layout.row: page.compact ? 1 : 0; Layout.column: page.compact ? 1 : 2 }
                    CustomButton {
                        id: saveDisplayTimeout
                        objectName: "saveDisplayIdleTimeout"
                        Layout.row: page.compact ? 2 : 0
                        Layout.column: page.compact ? 0 : 3
                        Layout.columnSpan: page.compact ? 3 : 1
                        Layout.fillWidth: page.compact
                        implicitWidth: 80
                        text: page.savingTimeout ? "Saving…" : "Save"
                        enabled: !backend.remoteLoginSetupBusy && displayTimeout.value !== page.savedTimeout
                        onClicked: { page.savingTimeout = true; backend.configureDisplayIdleTimeout(displayTimeout.value) }
                    }
                    StartupHelp { Layout.row: page.compact ? 1 : 0; Layout.column: page.compact ? 2 : 4; helpText: "Remove the virtual display after the last client disconnects. Reconnecting cancels removal.\nApplies to manually started sessions and remote login. Saved changes apply to future disconnects." }
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
                    title: "Passwordless Display Creation"
                    description: page.vkmsChecking ? "Checking authorization…" : page.vkmsAuthorizationMessage.length > 0 ? page.vkmsAuthorizationMessage : page.vkmsAuthorization.success
                        ? (page.vkmsAuthorization.repair_required ? page.vkmsAuthorization.message : page.vkmsAuthorization.unattended_enabled ? "Passwordless Display Creation is enabled for your user. Display creation does not request a password." : "Passwordless Display Creation is disabled. Enable it to create virtual displays without recurring password prompts.")
                        : (page.vkmsAuthorization.message || "Install or upgrade monitorize-vkms first.")
                    symbol: "key-slash"
                    actionText: page.vkmsChecking ? "Checking…" : !page.vkmsAuthorization.success ? "Recover host" : page.vkmsAuthorization.repair_required ? "Reauthorize" : page.vkmsAuthorization.unattended_enabled ? "Disable" : "Enable"
                    actionEnabled: !page.vkmsChecking && (!backend.isStreaming || (page.vkmsAuthorization.success && !page.vkmsAuthorization.unattended_enabled)) && !backend.sessionBusy && !backend.systemSetupRunning && !backend.applyingConfiguration
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
                    actionEnabled: !backend.sessionBusy && !backend.applyingConfiguration && !backend.systemSetupRunning && !backend.virtualDisplayCleanupRunning
                    onClicked: root.removeStagnantVirtualDisplays()
                }
                SettingsActionRow {
                    visible: backend.sunshineAvailable
                    title: "Clear restore tokens"
                    description: "Delete saved screen-sharing restore tokens."
                    symbol: "document-x"; actionText: "Clear tokens"
                    actionEnabled: !backend.sessionBusy && !backend.applyingConfiguration && !backend.systemSetupRunning
                    Layout.fillWidth: true
                    onClicked: root.clearRestoreTokens()
                }
                CustomButton {
                    text: "Run system setup again"; primary: false; Layout.fillWidth: true
                    onClicked: page.StackView.view.push("SystemSetupPage.qml")
                }
            }
            VkmsManagementCard { Layout.fillWidth: true }
        }
    }

    Dialog {
        id: sunshineResetDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        title: "Reset Sunshine settings?"
        modal: true
        width: Math.min(420, parent.width - 32)
        padding: 20
        header: null
        footer: null
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        contentItem: ColumnLayout {
            spacing: 14
            Text {
                text: sunshineResetDialog.title
                color: theme.headingText
                font.pixelSize: 18
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                text: "Load Monitorize's default streaming choices for both displays? Use Apply in Configure to restart with these changes. Pairing and saved display layouts will be kept."
                color: theme.textPrimary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CustomButton { text: "Cancel"; primary: false; onClicked: sunshineResetDialog.reject() }
                CustomButton { text: "Reset"; onClicked: sunshineResetDialog.accept() }
            }
        }
        onAccepted: {
            let result = backend.resetSunshineSettings()
            page.sunshineResetSucceeded = result["success"] === true
            page.sunshineResetMessage = result["message"] || "Could not reset Sunshine settings"
        }
    }

}
