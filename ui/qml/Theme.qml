pragma Singleton
import QtQuick

// Shared style tokens, matched to the system rofi theme (~/.config/rofi):
// near-opaque near-black panel, 2px white frame, JetBrainsMono. One place to
// change so Main/Settings/FolderInput can't drift apart.
QtObject {
    // JetBrainsMono when installed, else the fontconfig generic monospace —
    // never a hard font dependency (degrade gracefully, per CLAUDE.md).
    readonly property string fontFamily:
        Qt.fontFamilies().indexOf("JetBrainsMono Nerd Font") !== -1
            ? "JetBrainsMono Nerd Font" : "monospace"

    // Panel backdrop. Alpha < 1 lets a compositor blur rule show through.
    readonly property color bg: Qt.rgba(5 / 255, 5 / 255, 5 / 255, 0.95)
    // Opaque variant for cards stacked above the dim (settings, folder entry).
    readonly property color surface: "#050505"

    // The white frame: window border and selection language.
    readonly property color frame: "#ffffff"
    readonly property int frameWidth: 2

    readonly property color text: "#ffffff"
    readonly property color muted: "#8a8a8a"
    readonly property color faint: "#4a4a4a"
}
