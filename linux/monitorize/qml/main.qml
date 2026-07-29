import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    width: 860
    height: 580
    property bool settingsLoading: true
    property bool settingsMinimizeToTray: false
    property bool settingsEnableTouch: true
    property bool settingsEnableStylusFeatures: false
    property bool settingsAutostartEnabled: false
    property string settingsError: ""
    readonly property bool showGlobalBack: stack.depth > 1 && !backend.isStreaming && !backend.isReceiving

    function loadAppSettings() {
        settingsLoading = true
        let gen = backend.loadGeneralSettings()
        settingsMinimizeToTray = gen["minimize_to_tray"] !== undefined ? gen["minimize_to_tray"] : false
        settingsEnableTouch = gen["enable_touch"] !== undefined ? gen["enable_touch"] : true
        settingsEnableStylusFeatures = gen["enable_stylus_features"] !== undefined ? gen["enable_stylus_features"] : false
        settingsAutostartEnabled = backend.isAutostartEnabled()
        minimizeTrayCheck.checked = settingsMinimizeToTray
        autostartCheck.checked = settingsAutostartEnabled
        settingsError = ""
        settingsLoading = false
    }

    function saveAppSettings() {
        if (settingsLoading) return
        settingsMinimizeToTray = minimizeTrayCheck.checked
        backend.saveGeneralSettings(
            settingsMinimizeToTray,
            settingsEnableTouch,
            settingsEnableStylusFeatures
        )
    }

    function saveAutostartSettings() {
        if (settingsLoading) return
        settingsAutostartEnabled = autostartCheck.checked
        settingsError = backend.setAutostartEnabled(settingsAutostartEnabled)
        if (settingsError.length > 0) {
            settingsLoading = true
            settingsAutostartEnabled = backend.isAutostartEnabled()
            autostartCheck.checked = settingsAutostartEnabled
            settingsLoading = false
        }
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
                let returnPage = stack.currentItem
                    && stack.currentItem.returnPageSource !== undefined
                    ? stack.currentItem.returnPageSource
                    : "MainMenuPage.qml"
                stack.lastStreamingSetupPage = returnPage.length > 0
                    ? returnPage
                    : "MainMenuPage.qml"
                stack.replace("StreamingPage.qml")
            } else {
                stack.replace(stack.lastStreamingSetupPage, StackView.PopTransition)
            }
        }
    }

    // --- Navigate between pages when receiver state changes ---
    Connections {
        target: backend
        function onIsReceivingChanged(receiving) {
            if (receiving) {
                stack.lastReceiverSetupPage = "ReceiverSetupPage.qml"
                stack.replace("ReceiverStreamingPage.qml")
            } else {
                stack.replace(stack.lastReceiverSetupPage, StackView.PopTransition)
            }
        }
    }

    // --- Main StackView for page navigation ---
    StackView {
        id: stack
        objectName: "mainStack"
        property string lastStreamingSetupPage: "MainMenuPage.qml"
        property string lastReceiverSetupPage: "ReceiverSetupPage.qml"
        anchors.fill: parent
        anchors.leftMargin: backend.isReceiving ? 0 : 20
        anchors.rightMargin: backend.isReceiving ? 0 : 20
        anchors.topMargin: backend.isReceiving ? 0 : 56
        anchors.bottomMargin: backend.isReceiving ? 0 : 20
        initialItem: "MainMenuPage.qml"

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

    Button {
        id: backButton
        objectName: "globalBackButton"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: 14
        anchors.leftMargin: 20
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

    Button {
        id: settingsButton
        objectName: "settingsIconButton"
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 14
        anchors.rightMargin: 20
        z: 2
        width: 36
        height: 36
        visible: !backend.isReceiving
        onClicked: {
            root.loadAppSettings()
            settingsPopup.open()
        }
        background: Rectangle {
            implicitWidth: 36
            implicitHeight: 36
            visible: parent.hovered || parent.down
            color: parent.down ? theme.borderHover : theme.surfaceAlt
            radius: theme.controlRadius
            Behavior on color { ColorAnimation { duration: 150 } }
        }
        contentItem: Item {
            implicitWidth: 36
            implicitHeight: 36

            Image {
                anchors.centerIn: parent
                width: 17
                height: 17
                source: "../assets/svg/settings.svg"
                sourceSize.width: 17
                sourceSize.height: 17
                fillMode: Image.PreserveAspectFit
            }
        }
    }

    Popup {
        id: settingsPopup
        modal: true
        anchors.centerIn: parent
        width: 360
        height: settingsContent.implicitHeight + 44
        padding: 22
        background: Rectangle {
            color: theme.surface
            border.color: theme.border
            border.width: 1
            radius: theme.cardRadius
        }
        Overlay.modal: Rectangle { color: "#80000000" }

        ColumnLayout {
            id: settingsContent
            anchors.fill: parent
            spacing: 16

            Text {
                text: "Settings"
                color: theme.cardTextPrimary
                font.pixelSize: 18
                font.weight: Font.Bold
                Layout.fillWidth: true
            }

            CustomCheckBox {
                id: minimizeTrayCheck
                text: "Minimize to tray on close"
                Layout.fillWidth: true
                onCheckedChanged: root.saveAppSettings()
            }

            CustomCheckBox {
                id: autostartCheck
                text: "Start Monitorize after login"
                Layout.fillWidth: true
                onCheckedChanged: root.saveAutostartSettings()
            }

            Text {
                text: root.settingsError
                visible: root.settingsError.length > 0
                color: "#fca5a5"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    text: "Close"
                    Layout.alignment: Qt.AlignRight
                    onClicked: settingsPopup.close()
                    background: Rectangle {
                        implicitWidth: 92
                        implicitHeight: 36
                        color: parent.down ? theme.surfaceAlt : (parent.hovered ? theme.borderHover : theme.surface)
                        border.color: parent.hovered ? theme.borderHover : theme.border
                        border.width: 1
                        radius: theme.controlRadius
                        Behavior on color { ColorAnimation { duration: 150 } }
                        Behavior on border.color { ColorAnimation { duration: 150 } }
                    }
                    contentItem: Text {
                        text: parent.text
                        color: parent.hovered ? theme.textPrimary : theme.cardTextPrimary
                        font.pixelSize: 12
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }
}
