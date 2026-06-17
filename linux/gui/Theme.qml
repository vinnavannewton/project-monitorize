import QtQuick

QtObject {
    // KDE Breeze-inspired dark utility palette.
    // Keep the base quiet and reserve blue for actions and active states.

    // Backgrounds
    readonly property color background: "#1b1e24"
    readonly property color surface: "#232831"
    readonly property color surfaceAlt: "#2b313b"
    readonly property color logBoxBackground: "#171a20"

    // Accents
    readonly property color accent: "#3daee9"
    readonly property color accentAlpha20: "#203daee9"
    readonly property color accentAlpha40: "#403daee9"

    // Borders
    readonly property color border: "#343b46"
    readonly property color borderHover: "#4a5565"

    // Buttons
    readonly property color buttonBackground: "#2f6f95"
    readonly property color buttonBackgroundHover: "#3daee9"
    readonly property color buttonBackgroundPressed: "#24749f"
    readonly property color buttonText: "#ffffff"

    // Text
    readonly property color textPrimary: "#eff0f1"
    readonly property color textSecondary: "#c7d0d9"
    readonly property color textMuted: "#8f9aa6"
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
