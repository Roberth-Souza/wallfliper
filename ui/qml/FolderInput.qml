import QtQuick

// Fallback for setups with no xdg-desktop-portal FileChooser: type the
// wallpaper folder path by hand. In-app overlay (no second wlr-layer-shell
// surface — same reasoning as Settings.qml). TUI style per DESIGN.md: dark,
// flat, monochrome. Keyboard-first: type a path, Enter confirms, Esc cancels.
// Shown only when the portal is unavailable (see Main.qml openFolderPicker /
// the Controller.folderManualRequested fallback).
FocusScope {
    id: entry
    anchors.fill: parent
    focus: true

    // Tells Main.qml to unload this overlay. accepted = a valid folder was set
    // (the Controller already persisted it); cancelled = dismissed unchanged.
    signal accepted()
    signal cancelled()

    // Inline validation message from the last failed confirm ("" = none).
    property string error: ""

    // Greyscale palette, matching Settings.qml (white = focus/emphasis).
    readonly property color cWhite: "#ffffff"
    readonly property color cMuted: "#8a8a8a"
    readonly property color cFaint: "#5a5a5a"
    readonly property color cLine:  "#2a2a2e"

    function tryAccept() {
        var err = controller.setFolderFromText(input.text)
        if (err === "")
            entry.accepted()
        else
            entry.error = err
    }

    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Escape) {
            entry.cancelled()
            event.accepted = true
        }
    }

    // Dim the panels behind; click-away cancels.
    Rectangle {
        anchors.fill: parent
        color: "#99000000"
        MouseArea { anchors.fill: parent; onClicked: entry.cancelled() }
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: 560
        height: col.implicitHeight + 40
        radius: controller.corners === "sharp" ? 0 : 12
        color: "#141417"
        border.color: entry.cLine
        border.width: 1
        // Swallow clicks so click-away on the dim doesn't fire through the card.
        MouseArea { anchors.fill: parent }

        Column {
            id: col
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 20
            spacing: 12

            Text {
                text: "set wallpaper folder"
                color: entry.cWhite
                font.pixelSize: 14
                font.underline: true
            }
            Text {
                text: "no file chooser available — type a path"
                color: entry.cMuted
                font.pixelSize: 12
            }

            Rectangle { width: col.width; height: 1; color: entry.cLine }

            // ---- path input ----
            Rectangle {
                width: col.width
                height: 32
                radius: controller.corners === "sharp" ? 0 : 6
                color: "#0e0e10"
                border.color: input.activeFocus ? entry.cWhite : entry.cLine
                border.width: 1

                TextInput {
                    id: input
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    verticalAlignment: TextInput.AlignVCenter
                    color: entry.cWhite
                    font.pixelSize: 13
                    clip: true
                    selectByMouse: true
                    selectionColor: "#3a3a3a"
                    onAccepted: entry.tryAccept()
                    onTextChanged: entry.error = ""

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "~/Pictures/Wallpapers"
                        color: entry.cFaint
                        font.pixelSize: 13
                        visible: input.text === ""
                    }
                }
            }

            Rectangle { width: col.width; height: 1; color: entry.cLine }

            // Hint line, replaced by the validation error after a failed confirm.
            Text {
                width: col.width
                text: entry.error !== "" ? entry.error : "enter confirm    esc cancel"
                color: entry.error !== "" ? entry.cWhite : "#4a4a4a"
                font.pixelSize: 11
            }
        }
    }

    Component.onCompleted: {
        // Prefill the current folder so the user edits rather than retypes.
        input.text = controller.wallpaperDir
        input.forceActiveFocus()
        input.cursorPosition = input.text.length
    }
}
