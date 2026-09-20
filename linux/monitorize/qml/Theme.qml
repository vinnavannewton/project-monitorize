import QtQuick

QtObject {
    // KDE Breeze-inspired dark utility palette.
    // Keep the base quiet and reserve blue for actions and active states.

    // Backgrounds
    readonly property color background: "#0d1929"
    readonly property color surface: "#152335"
    readonly property color surfaceAlt: "#1b2c40"
    readonly property color logBoxBackground: "#0b1523"

    // Accents
    readonly property color accent: "#69b5ff"
    readonly property color accentAlpha20: "#202f6f95"
    readonly property color accentAlpha40: "#402f6f95"

    // Borders
    readonly property color border: "#29435e"
    readonly property color borderHover: "#4378a8"

    // Buttons
    readonly property color buttonBackground: "#1676d2"
    readonly property color buttonBackgroundHover: "#3daee9"
    readonly property color buttonBackgroundPressed: "#24749f"
    readonly property color buttonText: "#ffffff"

    // Text
    readonly property color textPrimary: "#e1ecff"
    readonly property color textSecondary: "#afc3dd"
    readonly property color textMuted: "#839fbd"
    readonly property color textLight: "#eff0f1"

    // Text on cards
    readonly property color cardTextPrimary: "#eff0f1"
    readonly property color cardTextSecondary: "#c7d0d9"
    readonly property color cardTextMuted: "#8f9aa6"

    // Shape
    readonly property int controlRadius: 8
    readonly property int cardRadius: 10
    readonly property real hoverScale: 1.01
}
