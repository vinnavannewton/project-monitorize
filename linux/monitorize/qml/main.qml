import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
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
    property string selectedPage: "DisplaySetupPage.qml"
    function navigate(page) {
        selectedPage = page
        stack.replace(page)
    }

    function removeStagnantVirtualDisplays() {
        let result = backend.removeStagnantVirtualDisplays()
        stagnantCleanupSucceeded = result["success"] === true
        stagnantCleanupMessage = result["message"] || "No stagnant displays were found"
        stagnantCleanupToast.open()
    }

    function clearRestoreTokens() {
        let result = backend.clearRestoreTokens()
        restoreTokenClearSucceeded = result["success"] === true
        restoreTokenClearMessage = result["message"] || "No restore tokens were found"
        restoreTokenClearToast.open()
    }

    Theme {
        id: theme
    }

    color: theme.background

    gradient: Gradient {
        GradientStop { position: 0.0; color: theme.background }
        GradientStop { position: 1.0; color: theme.background }
    }

    // --- Navigate between pages when streaming state changes ---
    Connections {
        target: backend
        function onIsStreamingChanged(streaming) {
            if (streaming) {
                if (root.selectedPage !== "StreamingPage.qml") root.navigate("StreamingPage.qml")
            }
        }
        function onStreamingStartFailed() {
            root.startFailureMessage = "Failed to start stream"
            startFailedToast.open()
        }
        function onStreamingCodecMismatch(message) {
            root.startFailureMessage = message
            startFailedToast.open()
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
        property string lastStreamingSetupPage: "MainMenuPage.qml"
        anchors.fill: parent
        anchors.leftMargin: 134
        anchors.rightMargin: 28
        anchors.topMargin: 28
        anchors.bottomMargin: 20
        initialItem: "DisplaySetupPage.qml"

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

    Rectangle {
        width: 106
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        color: "#101e30"; border.color: theme.border
        ColumnLayout {
            anchors { fill: parent; margins: 8; topMargin: 18; bottomMargin: 14 }
            spacing: 10
            NavigationButton {
                label: "Configure"; symbol: "display"; Layout.fillWidth: true
                selected: root.selectedPage === "DisplaySetupPage.qml"
                onClicked: root.navigate("DisplaySetupPage.qml")
            }
            NavigationButton {
                label: "Session"; symbol: "session"; Layout.fillWidth: true
                selected: root.selectedPage === "StreamingPage.qml"
                onClicked: root.navigate("StreamingPage.qml")
            }
            Item { Layout.fillHeight: true }
            NavigationButton {
                label: "Presets"; symbol: "logs"; Layout.fillWidth: true
                selected: root.selectedPage === "PresetsPage.qml"
                onClicked: root.navigate("PresetsPage.qml")
            }
            NavigationButton {
                label: "Settings"; symbol: "settings"; Layout.fillWidth: true
                selected: root.selectedPage === "SettingsPage.qml"
                onClicked: root.navigate("SettingsPage.qml")
            }
        }
    }

    Popup {
        id: startFailedToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        width: 340
        height: 48
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: "#b91c1c"
            radius: 8
        }
        contentItem: Text {
            text: root.startFailureMessage
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: startFailedToastTimer.restart()
        Timer {
            id: startFailedToastTimer
            interval: 2800
            onTriggered: startFailedToast.close()
        }
    }

    Popup {
        id: stagnantCleanupToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        z: 1000
        width: 330
        height: 48
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: root.stagnantCleanupSucceeded ? "#15803d" : "#b91c1c"
            radius: 8
        }
        contentItem: Text {
            text: root.stagnantCleanupMessage
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: stagnantCleanupToastTimer.restart()
        Timer {
            id: stagnantCleanupToastTimer
            interval: 2800
            onTriggered: stagnantCleanupToast.close()
        }
    }

    Popup {
        id: restoreTokenClearToast
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        z: 1000
        width: 380
        height: 48
        modal: false
        focus: false
        padding: 12
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            color: root.restoreTokenClearSucceeded ? "#15803d" : "#b91c1c"
            radius: 8
        }
        contentItem: Text {
            text: root.restoreTokenClearMessage
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        onOpened: restoreTokenClearToastTimer.restart()
        Timer {
            id: restoreTokenClearToastTimer
            interval: 3200
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
            radius: 8
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
