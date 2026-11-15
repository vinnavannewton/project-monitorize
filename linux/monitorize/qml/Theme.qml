import QtQuick

QtObject {
    objectName: "appearanceTheme"
    property bool useSystemAccent: false
    property var systemAccentColors: ({})
    property string mode: "system"
    property bool systemDarkFallback: false
    readonly property bool systemDark: typeof Qt.styleHints !== "undefined" && Qt.styleHints.colorScheme !== undefined
        ? Qt.styleHints.colorScheme === Qt.Dark : systemDarkFallback
    readonly property bool dark: mode === "dark" || (mode === "system" && systemDark)
    // Breeze-inspired neutral surfaces; blue action accents stay unchanged.
    // Keep the base quiet and reserve blue for actions and active states.

    // Backgrounds
    readonly property color background: dark ? "#202324" : "#eff0f1"
    readonly property color surface: dark ? "#272b2e" : "#ffffff"
    readonly property color surfaceAlt: dark ? "#303538" : "#e3e5e7"
    readonly property color logBoxBackground: dark ? "#1b1e20" : "#f5f6f7"

    // Accents
    readonly property color accent: useSystemAccent && systemAccentColors.accent ? systemAccentColors.accent : ("#69b5ff")
    readonly property color accentAlpha20: useSystemAccent && systemAccentColors.accentAlpha20 ? systemAccentColors.accentAlpha20 : ("#202f6f95")
    readonly property color accentAlpha40: useSystemAccent && systemAccentColors.accentAlpha40 ? systemAccentColors.accentAlpha40 : ("#402f6f95")

    // Borders
    readonly property color border: dark ? "#454b50" : "#b9bec3"
    readonly property color borderHover: useSystemAccent && systemAccentColors.borderHover ? systemAccentColors.borderHover : (dark ? "#4378a8" : "#4378a8")

    // Buttons
    readonly property color buttonBackground: useSystemAccent && systemAccentColors.buttonBackground ? systemAccentColors.buttonBackground : ("#1676d2")
    readonly property color buttonBackgroundHover: useSystemAccent && systemAccentColors.buttonBackgroundHover ? systemAccentColors.buttonBackgroundHover : ("#3daee9")
    readonly property color buttonBackgroundPressed: useSystemAccent && systemAccentColors.buttonBackgroundPressed ? systemAccentColors.buttonBackgroundPressed : ("#24749f")
    readonly property color buttonText: useSystemAccent && systemAccentColors.buttonText ? systemAccentColors.buttonText : ("#ffffff")

    // Text
    readonly property color textPrimary: dark ? "#eff0f1" : "#232629"
    readonly property color textSecondary: dark ? "#c7cbd0" : "#43494f"
    readonly property color textMuted: dark ? "#a4a9ae" : "#626970"
    readonly property color textLight: dark ? "#eff0f1" : "#232629"

    // Text on cards
    readonly property color cardTextPrimary: dark ? "#eff0f1" : "#232629"
    readonly property color cardTextSecondary: dark ? "#c7cbd0" : "#43494f"
    readonly property color cardTextMuted: dark ? "#a4a9ae" : "#626970"

    readonly property color headingText: useSystemAccent && systemAccentColors.headingText ? systemAccentColors.headingText : (dark ? "#b1d6ff" : "#1764a5")
    readonly property color errorText: dark ? "#fca5a5" : "#b42323"
    readonly property color warningText: dark ? "#fde68a" : "#795400"
    readonly property color successText: dark ? "#86efac" : "#187238"

    readonly property color iconTint: useSystemAccent && systemAccentColors.iconTint ? systemAccentColors.iconTint : (dark ? "#a6caff" : textSecondary)

    // Shape: KDE Breeze frame corners, in logical pixels.
    readonly property int controlRadius: 5
    readonly property int cardRadius: controlRadius
}
