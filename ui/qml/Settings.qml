import QtQuick

// In-app settings overlay (no second wlr-layer-shell surface — see Main.qml).
// Opened by typing `/config` in search. Loaded lazily by a Loader in Main.qml
// only while open, so it costs nothing when closed. Keyboard-first: j/k move
// rows, Enter/Space activate, Esc closes. Monochrome/flat per DESIGN.md.
FocusScope {
    id: panel
    anchors.fill: parent
    focus: true

    // Emitted to Main.qml: close the panel / open the folder picker (the picker
    // must hide the whole overlay, which only Main.qml can do).
    signal closed()
    signal folderRequested()

    property int sel: 0                  // 0 folder
    readonly property int rowCount: 1

    Keys.onPressed: (event) => {
        switch (event.key) {
        case Qt.Key_Escape:
            panel.closed(); break
        case Qt.Key_J: case Qt.Key_Down:
            panel.sel = (panel.sel + 1) % panel.rowCount; break
        case Qt.Key_K: case Qt.Key_Up:
            panel.sel = (panel.sel + panel.rowCount - 1) % panel.rowCount; break
        case Qt.Key_Return: case Qt.Key_Enter: case Qt.Key_Space:
            if (panel.sel === 0) panel.folderRequested()
            break
        default:
            return
        }
        event.accepted = true
    }

    // Dim the grid; click-away closes.
    Rectangle {
        anchors.fill: parent
        color: "#99000000"
        MouseArea { anchors.fill: parent; onClicked: panel.closed() }
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: 440
        height: col.implicitHeight + 40
        color: Theme.surface
        border.color: Theme.frame
        border.width: Theme.frameWidth
        // Swallow clicks so click-away on the dim doesn't fire through the card.
        MouseArea { anchors.fill: parent }

        Column {
            id: col
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 20
            spacing: 16

            Text {
                text: "config"
                color: Theme.text
                font.family: Theme.fontFamily
                font.bold: true
                font.pixelSize: 15
            }

            // ---- wallpaper folder ----
            Row {
                spacing: 10
                Text { text: panel.sel === 0 ? "›" : " "; color: Theme.text; width: 10; font.family: Theme.fontFamily; font.pixelSize: 13 }
                Text { text: "folder"; color: panel.sel === 0 ? Theme.text : Theme.muted; width: 90; font.family: Theme.fontFamily; font.pixelSize: 13 }
                Text {
                    text: controller.wallpaperDir === "" ? "(none) — enter to set" : controller.wallpaperDir
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    elide: Text.ElideMiddle
                    width: card.width - 40 - 10 - 90 - 30
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { panel.sel = 0; panel.folderRequested() }
                    }
                }
            }

            Text {
                text: "j/k move    enter select    esc close"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: 11
            }
        }
    }
}
