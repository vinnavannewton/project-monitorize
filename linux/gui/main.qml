import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    width: 860
    height: 580
    color: "#0c0d14"

    gradient: Gradient {
        GradientStop { position: 0.0; color: "#0c0d14" }
        GradientStop { position: 1.0; color: "#06070a" }
    }

    // --- Navigate between pages when streaming state changes ---
    Connections {
        target: backend
        function onIsStreamingChanged(streaming) {
            if (streaming) {
                stack.replace("StreamingPage.qml")
            } else {
                stack.replace("MainMenuPage.qml", StackView.PopTransition)
            }
        }
    }

    // --- Main StackView for page navigation ---
    StackView {
        id: stack
        anchors.fill: parent
        anchors.margins: 20
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
}
