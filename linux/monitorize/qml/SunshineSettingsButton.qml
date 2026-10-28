import QtQuick
import QtQuick.Controls

AbstractButton {
    id: actions
    required property int instance
    property bool canRemove: false
    signal removeRequested()
    objectName: "configureDisplayActions" + instance
    implicitWidth: 36
    implicitHeight: 36
    Accessible.name: "Display " + instance + " actions"
    background: Rectangle {
        radius: theme.controlRadius
        color: actions.hovered ? theme.surfaceAlt : "transparent"
        border.width: actions.visualFocus ? 1 : 0
        border.color: theme.borderHover
    }
    contentItem: Text {
        text: "⋮"; color: theme.textSecondary; font.pixelSize: 23
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    onClicked: displayMenu.open()
    Menu {
        id: displayMenu
        width: 220
        x: actions.width - width
        y: actions.height
        background: Rectangle { color: theme.surface; border.color: theme.border; radius: theme.controlRadius }
        Component.onCompleted: {
            if ("popupType" in displayMenu) displayMenu.popupType = Popup.Item
        }
        CardMenuItem {
            objectName: "configureSunshineSettings" + actions.instance
            text: "Sunshine settings"
            enabled: backend.sunshineAvailable && !backend.sunshineSettingsOpening
                && !backend.applyingConfiguration && !backend.sessionBusy
            onTriggered: backend.openSunshineWebUi(actions.instance)
        }
        CardMenuItem {
            objectName: "configureRemoveDisplay" + actions.instance
            text: "Remove"
            visible: actions.canRemove
            height: visible ? implicitHeight : 0
            enabled: actions.canRemove && !backend.applyingConfiguration
            onTriggered: actions.removeRequested()
        }
    }
}
