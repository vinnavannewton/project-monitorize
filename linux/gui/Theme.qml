import QtQuick

QtObject {
    // ── Palette Reference ────────────────────────────────────
    // Prussian Blue  #001d3c  — darkest (buttons, primary text)
    // Baltic Blue    #005e83  — accents, secondary text
    // Bondi Blue     #0092bc  — interactive highlights
    // Sky Surge      #45bed7  — decorative accents
    // Electric Aqua  #8ae9f2  — page background (lightest)

    // Backgrounds
    readonly property color background: '#5b8ae9f2'
    readonly property color surface: '#6793ae'
    readonly property color surfaceAlt: "#315469"
    readonly property color logBoxBackground: '#3e5f73'

    // Accents
    readonly property color accent: "#0092bc"
    readonly property color accentAlpha20: "#200092bc"
    readonly property color accentAlpha40: "#400092bc"

    // Borders
    readonly property color border: "#45bed7"
    readonly property color borderHover: "#0092bc"

    // Buttons
    readonly property color buttonBackground: "#005e83"
    readonly property color buttonBackgroundHover: "#001d3c"
    readonly property color buttonBackgroundPressed: "#0092bc"
    readonly property color buttonText: "#ffffff"

    // Text on main background (Electric Aqua)
    readonly property color textPrimary: "#001d3c"
    readonly property color textSecondary: "#005e83"
    readonly property color textMuted: "#1a6f8a"
    readonly property color textLight: "#001d3c"

    // Text on cards (slate blue surface)
    readonly property color cardTextPrimary: "#ffffff"
    readonly property color cardTextSecondary: "#e0dcf0"
    readonly property color cardTextMuted: "#b8aedd"
}
