import QtQuick

// In-app settings overlay (no second wlr-layer-shell surface — see Main.qml).
// Loaded lazily by a Loader in Main.qml only while open, so it costs nothing
// when closed. Keyboard-first: j/k move rows, h/l or ←/→ change a value,
// Enter/Space activate, Esc closes. Monochrome/flat per DESIGN.md.
FocusScope {
    id: panel
    anchors.fill: parent
    focus: true

    // Emitted to Main.qml: close the panel / open the folder picker (the picker
    // must hide the whole overlay, which only Main.qml can do).
    signal closed()
    signal folderRequested()

    // Discrete backdrop-opacity steps (alpha of the near-black panel).
    readonly property var opacitySteps: [0.70, 0.80, 0.90, 1.0]
    property int sel: 0                  // 0 background · 1 folder
    readonly property int rowCount: 2

    function nearestOpacityIndex() {
        var best = 0
        var bestD = Infinity
        for (var i = 0; i < opacitySteps.length; i++) {
            var d = Math.abs(opacitySteps[i] - controller.backgroundOpacity)
            if (d < bestD) { bestD = d; best = i }
        }
        return best
    }

    function stepOpacity(dir) {
        var i = Math.max(0, Math.min(opacitySteps.length - 1, nearestOpacityIndex() + dir))
        controller.setBackgroundOpacity(opacitySteps[i])
    }

    Keys.onPressed: (event) => {
        switch (event.key) {
        case Qt.Key_Escape:
            panel.closed(); break
        case Qt.Key_J: case Qt.Key_Down:
            panel.sel = (panel.sel + 1) % panel.rowCount; break
        case Qt.Key_K: case Qt.Key_Up:
            panel.sel = (panel.sel + panel.rowCount - 1) % panel.rowCount; break
        case Qt.Key_H: case Qt.Key_Left:
            if (panel.sel === 0) panel.stepOpacity(-1)
            break
        case Qt.Key_L: case Qt.Key_Right:
            if (panel.sel === 0) panel.stepOpacity(1)
            break
        case Qt.Key_Return: case Qt.Key_Enter: case Qt.Key_Space:
            if (panel.sel === 0) panel.stepOpacity(1)
            else if (panel.sel === 1) panel.folderRequested()
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
        color: "#141417"
        border.color: "#2a2a2e"
        border.width: 1
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
                text: "settings"
                color: "#ffffff"
                font.pixelSize: 15
            }

            // ---- background darkness (filled-bar level meter) ----
            Row {
                spacing: 10
                Text { text: panel.sel === 0 ? "›" : " "; color: "#ffffff"; width: 10; font.pixelSize: 13 }
                Text { text: "background"; color: panel.sel === 0 ? "#ffffff" : "#8a8a8a"; width: 90; font.pixelSize: 13 }
                Row {
                    spacing: 6
                    Repeater {
                        model: panel.opacitySteps
                        delegate: Rectangle {
                            required property int index
                            width: 28
                            height: 10
                            color: index <= panel.nearestOpacityIndex() ? "#ffffff" : "#3a3a3a"
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { panel.sel = 0; controller.setBackgroundOpacity(panel.opacitySteps[parent.index]) }
                            }
                        }
                    }
                }
            }

            // ---- wallpaper folder ----
            Row {
                spacing: 10
                Text { text: panel.sel === 1 ? "›" : " "; color: "#ffffff"; width: 10; font.pixelSize: 13 }
                Text { text: "folder"; color: panel.sel === 1 ? "#ffffff" : "#8a8a8a"; width: 90; font.pixelSize: 13 }
                Text {
                    text: controller.wallpaperDir === "" ? "(none) — enter to set" : controller.wallpaperDir
                    color: "#8a8a8a"
                    font.pixelSize: 13
                    elide: Text.ElideMiddle
                    width: card.width - 40 - 10 - 90 - 30
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { panel.sel = 1; panel.folderRequested() }
                    }
                }
            }

            Text {
                text: "j/k move    ←/→ change    enter select    esc close"
                color: "#4a4a4a"
                font.pixelSize: 11
            }
        }
    }
}
