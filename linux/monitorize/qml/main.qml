import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    PairingRequestDialog { coordinator: backend.incomingPairing }
    width: 860
    height: 580
    property bool settingsLoading: true
    property bool settingsMinimizeToTray: false
    property bool settingsAutostartEnabled: false
    property string settingsError: ""
    property bool stagnantCleanupSucceeded: false
    property string stagnantCleanupMessage: ""
    property bool restoreTokenClearSucceeded: false
    property string restoreTokenClearMessage: ""
    property string startFailureMessage: "Failed to start stream"
    readonly property bool showGlobalBack: stack.depth > 1 && !backend.isStreaming
        && stack.currentItem !== null && stack.currentItem.objectName !== "bootWifiSetupPage"
    property string selectedPage: "DisplaySetupPage.qml"
    property string highlightedPage: "DisplaySetupPage.qml"
    property bool navHighlightFading: false
    property string pendingNavigation: ""
    property string pendingConfigurationPage: ""
    property string configurationNavigationError: ""

    function navigationGroup(page) {
        return (page === "DisplaySetupPage.qml" || page === "StreamingPage.qml")
            ? "top" : "bottom"
    }

    function navigationButtonY(page) {
        if (page === "DisplaySetupPage.qml") return navigationColumn.y + configureButton.y
        if (page === "StreamingPage.qml") return navigationColumn.y + sessionButton.y
        if (page === "PresetsPage.qml") return navigationColumn.y + presetsButton.y
        return navigationColumn.y + settingsButton.y
    }

    function navigationButtonHeight(page) {
        if (page === "DisplaySetupPage.qml") return configureButton.height
        if (page === "StreamingPage.qml") return sessionButton.height
        if (page === "PresetsPage.qml") return presetsButton.height
        return settingsButton.height
    }

    onSelectedPageChanged: {
        if (highlightedPage === selectedPage) return
        if (navigationGroup(highlightedPage) === navigationGroup(selectedPage)) {
            navHighlightFade.stop()
            navHighlightFading = false
            navHighlight.opacity = 1
            highlightedPage = selectedPage
        } else {
            navHighlightFading = true
            navHighlightFade.restart()
        }
    }

    function navigate(page) {
        if (page === selectedPage && stack.depth === 1) {
            pendingNavigation = ""
            configureUnsavedDialog.close()
            return
        }
        if (stack.busy) {
            pendingNavigation = page
            return
        }
        if (configureUnsavedDialog.visible) {
            pendingConfigurationPage = page
            return
        }
        if (stack.currentItem && typeof stack.currentItem.commitAllPendingDisplaySettings === "function") {
            stack.currentItem.commitAllPendingDisplaySettings()
        }
        if (selectedPage === "DisplaySetupPage.qml" && page !== selectedPage && backend.configurationDirty) {
            pendingConfigurationPage = page
            configurationNavigationError = ""
            configureUnsavedDialog.open()
            return
        }
        finishNavigation(page)
    }

    function finishNavigation(page) {
        selectedPage = page
        stack.replace(null, page)
    }

    function resolveConfigurationNavigation(save) {
        if (backend.applyingConfiguration || !pendingConfigurationPage) return
        configurationNavigationError = ""
        if (save) {
            if (!backend.saveConfigurationForLater()) return
        } else {
            // Old controls must not publish stale edits as they lose focus or close.
            stack.currentItem.loading = true
            if (!backend.discardConfigurationChanges()) {
                stack.currentItem.loading = false
                return
            }
        }
        let destination = pendingConfigurationPage
        stack.currentItem.loading = true
        configureUnsavedDialog.close()
        finishNavigation(destination)
    }

    function showConfigurationDraft() {
        if (stack.busy) {
            draftNavigationTimer.restart()
            return
        }
        pendingNavigation = ""
        selectedPage = "DisplaySetupPage.qml"
        // The backend has replaced the draft; never flush stale controls over it.
        stack.replace(null, "DisplaySetupPage.qml")
    }

    Timer {
        id: draftNavigationTimer
        interval: 80
        onTriggered: showConfigurationDraft()
    }

    function removeStagnantVirtualDisplays() {
        backend.removeStagnantVirtualDisplays()
    }

    function clearRestoreTokens() {
        let result = backend.clearRestoreTokens()
        restoreTokenClearSucceeded = result["success"] === true
        restoreTokenClearMessage = result["message"] || "No restore tokens were found"
        restoreTokenClearToast.open()
        restoreTokenClearToastTimer.restart()
    }

    Connections {
        target: backend
        function onConfigureDisplayRequestedForDraft() { showConfigurationDraft() }
        function onConfigurationApplyFinished(success, message) {
            if (configureUnsavedDialog.visible && !success) {
                root.configurationNavigationError = message
                return
            }
            root.stagnantCleanupSucceeded = success
            root.stagnantCleanupMessage = message
            stagnantCleanupToast.open()
            stagnantCleanupToastTimer.restart()
        }
    }

    Dialog {
        id: configureUnsavedDialog
        objectName: "configureUnsavedDialog"
        title: "Save configuration changes?"
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(560, root.width - 40)
        padding: 20
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.cardRadius }
        header: Label {
            text: configureUnsavedDialog.title
            color: theme.headingText
            font.pixelSize: 18
            font.weight: Font.DemiBold
            padding: 20
            bottomPadding: 8
            wrapMode: Text.WordWrap
        }
        contentItem: ColumnLayout {
            Text {
                text: "Saving keeps your current stream running. The changes will take effect when you next start or restart streaming."
                color: theme.textPrimary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                text: root.configurationNavigationError || backend.configurationValidationMessage
                visible: text.length > 0
                color: theme.errorText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        footer: DialogButtonBox {
            padding: 20
            topPadding: 8
            background: Item {}
            CustomButton {
                objectName: "saveConfigurationChangesButton"
                text: "Save changes"
                enabled: backend.configurationValid && !backend.applyingConfiguration
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: root.resolveConfigurationNavigation(true)
            }
            CustomButton {
                objectName: "discardConfigurationChangesButton"
                text: "Discard changes"; primary: false
                enabled: !backend.applyingConfiguration
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: root.resolveConfigurationNavigation(false)
            }
            CustomButton {
                id: keepConfigurationEditingButton
                objectName: "keepConfigurationEditingButton"
                text: "Keep editing"; primary: false
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: configureUnsavedDialog.reject()
            }
        }
        onOpened: keepConfigurationEditingButton.forceActiveFocus()
        onClosed: {
            root.pendingConfigurationPage = ""
            root.configurationNavigationError = ""
        }
    }

    Theme {
        id: theme
        mode: backend ? backend.themeMode : "system"
        systemDarkFallback: backend ? backend.systemThemeDark : false
        useSystemAccent: backend ? backend.useSystemAccent : false
        systemAccentColors: backend ? backend.systemAccentColors : ({})
    }

    color: theme.background

    // Session updates must not override the page selected by the user.
    Connections {
        target: backend
        function onVirtualDisplayCleanupFinished(success, message) {
            root.stagnantCleanupSucceeded = success
            root.stagnantCleanupMessage = message
            stagnantCleanupToast.open()
            stagnantCleanupToastTimer.restart()
        }
        function onStreamingStartFailed() {
            if (vkmsReinstallPopup.visible) return
            root.startFailureMessage = "Failed to start stream"
            startFailedToast.open()
            startFailedToastTimer.restart()
        }
        function onVkmsReinstallRequired() {
            startFailedToast.close()
            vkmsReinstallPopup.open()
        }
        function onRemoteLoginSetupBlocked(message) {
            root.startFailureMessage = message
            startFailedToast.open()
            startFailedToastTimer.restart()
        }
        function onStreamingCodecMismatch(message) {
            root.startFailureMessage = message
            startFailedToast.open()
            startFailedToastTimer.restart()
        }
    }

    Component.onCompleted: {
        if (backend.systemSetupPending) firstRunSetupPopup.open()
    }

    // --- Main StackView for page navigation ---
    StackView {
        id: stack
        objectName: "mainStack"
        clip: true
        anchors.fill: parent
        anchors.leftMargin: 134
        anchors.rightMargin: 28
        anchors.topMargin: 28
        anchors.bottomMargin: 20
        initialItem: "DisplaySetupPage.qml"
        onBusyChanged: {
            if (!busy && root.pendingNavigation) {
                let destination = root.pendingNavigation
                root.pendingNavigation = ""
                Qt.callLater(function() { root.navigate(destination) })
            }
        }

        replaceEnter: Transition {
            PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
        }
        replaceExit: Transition {
            PropertyAnimation { property: "opacity"; to: 0; duration: 1 }
        }
        pushEnter: Transition {
            PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
        }
        pushExit: Transition {
            PropertyAnimation { property: "opacity"; to: 0; duration: 1 }
        }
        popEnter: Transition {
            PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
        }
        popExit: Transition {
            PropertyAnimation { property: "opacity"; to: 0; duration: 1 }
        }
    }

    Rectangle {
        id: sidebar
        width: 106
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        color: theme.logBoxBackground; border.color: theme.border

        Rectangle {
            id: navHighlight
            x: navigationColumn.x
            width: navigationColumn.width
            y: root.navigationButtonY(root.highlightedPage)
            height: root.navigationButtonHeight(root.highlightedPage)
            radius: theme.controlRadius
            color: theme.buttonBackground
            z: 0

            Behavior on y {
                enabled: !root.navHighlightFading
                NumberAnimation { duration: 260; easing.type: Easing.InOutCubic }
            }
        }

        ColumnLayout {
            id: navigationColumn
            anchors { fill: parent; margins: 8; topMargin: 18; bottomMargin: 14 }
            spacing: 10
            z: 1
            NavigationButton {
                id: configureButton
                label: "Configure"; symbol: "display"; Layout.fillWidth: true
                selected: root.selectedPage === "DisplaySetupPage.qml"
                onClicked: root.navigate("DisplaySetupPage.qml")
            }
            NavigationButton {
                id: sessionButton
                label: "Session"; symbol: "session"; Layout.fillWidth: true
                selected: root.selectedPage === "StreamingPage.qml"
                onClicked: root.navigate("StreamingPage.qml")
            }
            Item { Layout.fillHeight: true }
            NavigationButton {
                id: presetsButton
                label: "Presets"; symbol: "logs"; Layout.fillWidth: true
                selected: root.selectedPage === "PresetsPage.qml"
                onClicked: root.navigate("PresetsPage.qml")
            }
            NavigationButton {
                id: settingsButton
                label: "Settings"; symbol: "settings"; Layout.fillWidth: true
                selected: root.selectedPage === "SettingsPage.qml"
                onClicked: root.navigate("SettingsPage.qml")
            }
        }
    }

    SequentialAnimation {
        id: navHighlightFade
        NumberAnimation { target: navHighlight; property: "opacity"; to: 0; duration: 110 }
        ScriptAction {
            script: {
                root.highlightedPage = root.selectedPage
                root.navHighlightFading = false
            }
        }
        NumberAnimation { target: navHighlight; property: "opacity"; to: 1; duration: 150 }
    }

    Popup {
        id: startFailedToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        width: Math.min(520, parent.width - 32)
        height: Math.max(48, contentItem.implicitHeight + topPadding + bottomPadding)
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: "#b91c1c"
            radius: theme.controlRadius
        }
        contentItem: Text {
            text: root.startFailureMessage
            color: "white"
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: startFailedToastTimer.restart()
        Timer {
            id: startFailedToastTimer
            interval: 5000
            onTriggered: startFailedToast.close()
        }
    }

    Popup {
        id: vkmsReinstallPopup
        objectName: "vkmsReinstallPopup"
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(440, root.width - 40)
        height: vkmsReinstallContent.implicitHeight + 44
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#99000000" }

        ColumnLayout {
            id: vkmsReinstallContent
            anchors.fill: parent
            spacing: 14

            Text {
                text: "Reinstall monitorize-vkms"
                color: theme.textPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
                Layout.fillWidth: true
            }
            Text {
                text: "monitorize-vkms is installed, but its helper or kernel module is not ready. Run sudo ./install.sh from the monitorize-vkms directory, follow any reboot instructions, then try again."
                color: theme.textSecondary
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                CustomButton {
                    text: "Installation instructions"
                    primary: false
                    onClicked: backend.openMonitorizeVkmsInstallPage()
                }
                CustomButton {
                    text: "Close"
                    onClicked: vkmsReinstallPopup.close()
                }
            }
        }
    }

    Popup {
        id: stagnantCleanupToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        z: 1000
        width: Math.min(520, parent.width - 32)
        height: Math.max(48, contentItem.implicitHeight + topPadding + bottomPadding)
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: root.stagnantCleanupSucceeded ? "#15803d" : "#b91c1c"
            radius: theme.controlRadius
        }
        contentItem: Text {
            text: root.stagnantCleanupMessage
            color: "white"
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: stagnantCleanupToastTimer.restart()
        Timer {
            id: stagnantCleanupToastTimer
            interval: 5000
            onTriggered: stagnantCleanupToast.close()
        }
    }

    Popup {
        id: restoreTokenClearToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        z: 1000
        width: Math.min(520, parent.width - 32)
        height: Math.max(48, contentItem.implicitHeight + topPadding + bottomPadding)
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: root.restoreTokenClearSucceeded ? "#15803d" : "#b91c1c"
            radius: theme.controlRadius
        }
        contentItem: Text {
            text: root.restoreTokenClearMessage
            color: "white"
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: restoreTokenClearToastTimer.restart()
        Timer {
            id: restoreTokenClearToastTimer
            interval: 5000
            onTriggered: restoreTokenClearToast.close()
        }
    }

    Popup {
        id: firstRunSetupPopup
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        anchors.centerIn: parent
        width: Math.min(560, root.width - 40)
        height: Math.min(500, root.height - 40)
        padding: 0
        background: Rectangle {
            color: theme.background
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#80000000" }

        Loader {
            id: firstRunSetupLoader
            anchors.fill: parent
            source: "SystemSetupPage.qml"
            active: firstRunSetupPopup.visible
            onLoaded: item.firstRun = true
        }

        Connections {
            target: firstRunSetupLoader.item
            ignoreUnknownSignals: true
            function onSetupCompleted() { firstRunSetupPopup.close() }
            function onCancellationRequested() { firstRunCancelPopup.open() }
        }
    }

    Popup {
        id: firstRunCancelPopup
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        anchors.centerIn: parent
        width: 380
        height: firstRunCancelContent.implicitHeight + 44
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#99000000" }

        ColumnLayout {
            id: firstRunCancelContent
            anchors.fill: parent
            spacing: 14

            Text {
                text: "Skip system setup?"
                color: theme.textPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
                Layout.fillWidth: true
            }

            Text {
                text: "Touch input or Moonlight connections may not work until you complete setup."
                color: theme.textSecondary
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10

                CustomButton {
                    text: "Finish setup"
                    primary: false
                    onClicked: firstRunCancelPopup.close()
                }

                CustomButton {
                    text: "I know what I’m doing"
                    onClicked: {
                        backend.markSystemSetupDecided()
                        firstRunCancelPopup.close()
                        firstRunSetupPopup.close()
                    }
                }
            }
        }
    }

    Button {
        id: backButton
        objectName: "globalBackButton"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: 14
        anchors.leftMargin: 134
        z: 2
        visible: root.showGlobalBack
        text: "‹ Back"
        onClicked: stack.pop()
        background: Rectangle {
            implicitWidth: 82
            implicitHeight: 34
            color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
            border.color: theme.border
            radius: theme.controlRadius
            Behavior on color { ColorAnimation { duration: 150 } }
        }
        contentItem: Text {
            text: parent.text
            color: theme.cardTextPrimary
            font.pixelSize: 12
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

}
