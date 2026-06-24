import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

Window {
    id: win
    visible: true
    width: Screen.width
    height: Screen.height
    // Transparent surface: the real backdrop is `bg` below, painted with alpha
    // so a Hyprland `layerrule = blur, wallfliper` blurs *only* the background.
    // Thumbnails and text are opaque (drawn on top of bg) so they stay crisp.
    // Tune the alpha in `bg.color` for more/less see-through.
    color: "transparent"

    // wlr-layer-shell: full-screen overlay above everything (anchored to all
    // four edges). The surface fills the screen but is transparent except for
    // the centered `bg` card; the transparent margin is a click-catcher that
    // dismisses (see dismissArea below) — click-away-to-close, like a launcher.
    //
    // Exclusive keyboard: the overlay holds the keyboard the whole time it's
    // mapped, like a launcher (rofi/wofi). OnDemand delegates focus to the
    // compositor's normal policy, so a `focus_follows_mouse` compositor steals
    // the keyboard the moment the pointer leaves the surface — defocusing on
    // hover-out. Exclusive ignores pointer position entirely; you stay focused
    // while browsing and close with Esc / apply, not by drifting the mouse off.
    // The folder picker stays reachable because openFolderPicker() hides the
    // surface (visible = false), releasing the keyboard grab so the portal
    // chooser is topmost and focused.
    LayerShell.Window.scope: "wallfliper"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight

    // Click-away-to-close: a full-screen catcher behind the card. The surface is
    // full-screen but transparent here, so the desktop shows through (and
    // `layerrule = ignorezero` keeps blur off it); clicking anywhere outside the
    // card dismisses, like clicking away from a launcher. Clicks on the card
    // itself are swallowed (see the MouseArea inside bg) so they never reach it.
    MouseArea {
        id: dismissArea
        anchors.fill: parent
        onClicked: Qt.quit()
    }

    // Backdrop (the visible panel), centered as a card so there's always a
    // transparent margin to click away on. Capped to the design size, shrinking
    // to fit smaller screens. Alpha < 1 is what blur shows through; without blur
    // it's just a translucent dark panel. Opacity and corner style are user
    // settings (see Settings.qml), persisted in config.json. Rounded corners
    // need `layerrule = ignorezero, wallfliper` so blur skips the transparent
    // edges. Base is near-black (#070708); only the alpha varies.
    Rectangle {
        id: bg
        anchors.centerIn: parent
        width: Math.min(1381, win.width - 80)
        height: Math.min(743, win.height - 80)
        color: Qt.rgba(7 / 255, 7 / 255, 8 / 255, controller.backgroundOpacity)
        radius: controller.corners === "sharp" ? 0 : 14

        // Swallow clicks on the panel so they don't fall through to dismissArea;
        // only clicks on the transparent margin outside the card close the app.
        // While searching, a click on empty panel chrome cancels search.
        MouseArea {
            anchors.fill: parent
            onClicked: if (win.searching) win.exitSearchClear()
        }
    }

    // Toggles the lazy settings overlay (gear icon). Closed = panel unloaded.
    property bool settingsOpen: false

    // Search is modal: `/` enters search mode so the printable keys — including
    // the w/a/s/d + h/j/k/l navigation and space — stay free as commands in
    // normal mode. Once searching, every printable key filters live (any
    // filename is typable); arrows still move. Shown in the top bar as `/<query>`.
    //
    // Leaving search has two flavors: `Enter`, an arrow key, or clicking a
    // result *confirms* the filter (exitSearchKeep — drop to normal nav, query
    // kept, grid stays filtered); `Esc`, `/` again, or clicking empty app chrome
    // *cancels* it (exitSearchClear — wipe the query, full grid returns).
    property string searchText: ""
    property bool searching: false
    onSearchTextChanged: {
        controller.setFilter(searchText)
        grid.currentIndex = grid.count > 0 ? 0 : -1
    }

    // Space: apply but keep the overlay open, so you can audition wallpapers
    // live on the real desktop and keep browsing.
    function applyCurrent() {
        if (grid.currentIndex >= 0)
            controller.apply(grid.currentIndex)
    }

    function applyAndExit() {
        if (grid.currentIndex < 0)
            return
        controller.apply(grid.currentIndex)
        Qt.quit()
    }

    // Leave search input mode but keep the query and filtered grid (Enter or
    // clicking a result). Selection is preserved; press `/` to resume editing.
    function exitSearchKeep() {
        win.searching = false
    }

    // Leave search mode and clear the query, restoring the full grid (Esc, `/`
    // again, or clicking empty app chrome).
    function exitSearchClear() {
        win.searching = false
        win.searchText = ""
    }

    // Lazy manual-entry fallback: shown only when no portal chooser answers.
    property bool folderEntryOpen: false

    function openFolderPicker() {
        // With a portal, hide the overlay so the chooser toplevel is topmost and
        // focused. Without one, never unmap — go straight to manual entry so the
        // window can't be stranded hidden waiting on a chooser that won't appear.
        if (controller.folderPortalAvailable()) {
            win.visible = false
            controller.pickFolder()
        } else {
            win.showFolderEntry()
        }
    }

    // Re-map the overlay after the picker closes (chosen, cancelled, or failed).
    function closeFolderPicker() {
        win.visible = true
        if (settingsLoader.item)
            settingsLoader.item.forceActiveFocus()
    }

    // Open manual path entry, ensuring the overlay is mapped (the portal route
    // may have hidden it before failing).
    function showFolderEntry() {
        win.visible = true
        win.folderEntryOpen = true
    }

    function closeFolderEntry() {
        win.folderEntryOpen = false
        if (settingsLoader.item)
            settingsLoader.item.forceActiveFocus()
        else
            mainScope.forceActiveFocus()
    }

    Connections {
        target: controller
        function onFolderPickerClosed() { win.closeFolderPicker() }
        // Portal missing or the request failed: fall back to manual entry.
        function onFolderManualRequested() { win.showFolderEntry() }
    }

    // A filter toggle (image / video). White line icon at full opacity +
    // white outline when active; dimmed to grey when off — the same
    // white-selection language as the grid. Corners follow the global corner
    // setting. The icon is a monochrome SVG from assets/icons (white stroke),
    // greyed for the off state by lowering opacity over the dark backdrop.
    component FilterButton: Rectangle {
        id: fbtn
        property url icon
        property bool active: false
        signal toggled()

        width: 28
        height: 22
        radius: controller.corners === "sharp" ? 0 : 6
        color: "transparent"
        border.color: fbtn.active ? "#ffffff" : "#4a4a4a"
        border.width: fbtn.active ? 2 : 1

        Image {
            anchors.centerIn: parent
            source: fbtn.icon
            // Rasterize the SVG at 2x the display size for a crisp edge on HiDPI.
            sourceSize.width: 28
            sourceSize.height: 28
            width: 14
            height: 14
            smooth: true
            opacity: fbtn.active ? 1.0 : 0.5
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: fbtn.toggled()
        }
    }

    FocusScope {
        id: mainScope
        anchors.fill: bg
        anchors.margins: 16
        focus: true

        Keys.onPressed: (event) => {
            // Esc always exits immediately, in either mode.
            if (event.key === Qt.Key_Escape) {
                if (win.searching)
                    win.exitSearchClear()
                else
                    Qt.quit()
                event.accepted = true
                return
            }

            if (win.searching) {
                // Search mode: every printable key (incl. w/a/s/d, h/j/k/l and
                // space) is part of the query so any filename is typable. An
                // arrow key navigates results, not the query, so it confirms the
                // filter (keeps the query, drops to normal nav) and moves; Enter
                // confirms without moving; `/` again cancels it.
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                    win.exitSearchKeep()
                else if (event.key === Qt.Key_Up) {
                    win.exitSearchKeep()
                    grid.moveCurrentIndexUp()
                } else if (event.key === Qt.Key_Down) {
                    win.exitSearchKeep()
                    grid.moveCurrentIndexDown()
                } else if (event.key === Qt.Key_Left) {
                    win.exitSearchKeep()
                    grid.moveCurrentIndexLeft()
                } else if (event.key === Qt.Key_Right) {
                    win.exitSearchKeep()
                    grid.moveCurrentIndexRight()
                } else if (event.text === "/")
                    win.exitSearchClear()  // press `/` again to leave and clear
                else if (event.key === Qt.Key_Backspace) {
                    // Backspace on an empty query leaves search mode; otherwise
                    // it edits the query.
                    if (win.searchText === "")
                        win.exitSearchKeep()
                    else
                        win.searchText = win.searchText.slice(0, -1)
                } else if (event.text.length === 1 && event.text >= " ")
                    win.searchText += event.text
                else
                    return  // let unhandled keys propagate
                event.accepted = true
                return
            }

            // Normal mode: the keys are commands.
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                win.applyAndExit()
            else if (event.key === Qt.Key_Space)
                win.applyCurrent()  // audition: apply but stay
            else if (event.text === "/")
                win.searching = true
            else if (event.text === "i")
                controller.toggleImageFilter()
            else if (event.text === "v")
                controller.toggleVideoFilter()
            else if (event.key === Qt.Key_Up || event.key === Qt.Key_W || event.key === Qt.Key_K)
                grid.moveCurrentIndexUp()
            else if (event.key === Qt.Key_Down || event.key === Qt.Key_S || event.key === Qt.Key_J)
                grid.moveCurrentIndexDown()
            else if (event.key === Qt.Key_Left || event.key === Qt.Key_A || event.key === Qt.Key_H)
                grid.moveCurrentIndexLeft()
            else if (event.key === Qt.Key_Right || event.key === Qt.Key_D || event.key === Qt.Key_L)
                grid.moveCurrentIndexRight()
            else
                return
            event.accepted = true
        }

        // ---- Top bar ----
        Item {
            id: topBar
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 40

            Row {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                spacing: 22

                Text {
                    text: "⚙"   // gear (only icon allowed — no word is shorter)
                    color: "#808080"
                    font.pixelSize: 18
                    anchors.verticalCenter: parent.verticalCenter
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: win.settingsOpen = true
                    }
                }

                // Source label. Only the local library is implemented today;
                // Wallhaven/Lively tabs return here once those backends exist
                // (re-add as a Repeater over a sources model — see Roadmap).
                Text {
                    text: "Local"
                    color: "#ffffff"
                    font.pixelSize: 14
                    font.underline: true
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            // Search affordance: a faint `/  search` hint in normal mode that
            // becomes the live `/<query>` once you press `/`. No box, no icon.
            Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: (win.searching || win.searchText !== "") ? "/" + win.searchText : "/  search"
                // White while editing; grey when a filter persists after leaving
                // search (so the active filter is never hidden); faint when idle.
                color: win.searching ? "#ffffff" : (win.searchText !== "" ? "#909090" : "#4a4a4a")
                font.pixelSize: 14
            }
        }

        // ---- Filter toggles: image / video, below the search hint ----
        Row {
            id: filterBar
            anchors.top: topBar.bottom
            anchors.topMargin: 8
            anchors.right: parent.right
            spacing: 8

            FilterButton {
                icon: "../../assets/icons/image.svg"
                active: controller.imageFilter
                onToggled: controller.toggleImageFilter()
            }
            FilterButton {
                icon: "../../assets/icons/video.svg"
                active: controller.videoFilter
                onToggled: controller.toggleVideoFilter()
            }
        }

        // ---- Grid ----
        GridView {
            id: grid
            anchors.top: filterBar.bottom
            anchors.topMargin: 12
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: statusBar.top
            anchors.bottomMargin: 10
            clip: true
            // Responsive columns: pick as many ~232px cells as fit, then divide
            // the full width evenly among them so no empty space is left on the
            // right. Cell height keeps the original 232:176 proportion.
            readonly property int minCell: 232
            readonly property int columns: Math.max(1, Math.floor(width / minCell))
            cellWidth: width / columns
            cellHeight: cellWidth * (176 / 232)
            cacheBuffer: 600
            model: controller.model
            currentIndex: 0

            delegate: Item {
                id: cell
                required property int index
                required property string name
                required property string kind
                required property string thumbnail
                required property string preview
                width: grid.cellWidth
                height: grid.cellHeight
                property bool selected: GridView.isCurrentItem

                // Thumb fills the cell minus padding; caption sits below.
                readonly property real thumbW: width - 20
                readonly property real thumbH: thumbW * (120 / 212)

                // A video preview plays only on the selected cell. Ask the
                // controller to generate it lazily the moment we're selected
                // (cached after the first time); at most one cell previews.
                readonly property bool previewing: selected && kind === "video" && preview !== ""
                onSelectedChanged: if (selected && kind === "video") controller.ensurePreview(index)
                Component.onCompleted: if (selected && kind === "video") controller.ensurePreview(index)

                Column {
                    anchors.centerIn: parent
                    spacing: 6

                    Rectangle {
                        width: cell.thumbW
                        height: cell.thumbH
                        radius: controller.corners === "sharp" ? 0 : 8
                        color: "#161616"
                        border.color: cell.selected ? "#ffffff" : "transparent"
                        border.width: 2
                        clip: true

                        Image {
                            anchors.fill: parent
                            anchors.margins: 2
                            source: cell.thumbnail
                            visible: cell.thumbnail !== "" && !cell.previewing
                            asynchronous: true
                            cache: true
                            fillMode: Image.PreserveAspectCrop
                            sourceSize.width: 424
                        }
                        AnimatedImage {
                            anchors.fill: parent
                            anchors.margins: 2
                            // Only load the clip while selected so unselected
                            // cells hold no decoder/memory.
                            source: cell.previewing ? cell.preview : ""
                            visible: cell.previewing
                            playing: cell.previewing
                            cache: false
                            asynchronous: true
                            fillMode: Image.PreserveAspectCrop
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: cell.thumbnail === "" && !cell.previewing
                            text: cell.kind === "video" ? "▶" : "…"
                            color: "#3a3a3a"
                            font.pixelSize: 22
                        }
                    }

                    Text {
                        width: cell.thumbW
                        text: cell.name
                        color: cell.selected ? "#ffffff" : "#909090"
                        elide: Text.ElideMiddle
                        horizontalAlignment: Text.AlignHCenter
                        font.pixelSize: 12
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    // Clicking a result selects it and leaves search mode, but
                    // keeps the query/filter so the selection stays valid.
                    onClicked: {
                        grid.currentIndex = cell.index
                        if (win.searching)
                            win.exitSearchKeep()
                    }
                    onDoubleClicked: { grid.currentIndex = cell.index; win.applyAndExit() }
                }
            }
        }

        // ---- Status ----
        Text {
            id: statusBar
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            text: controller.status
            color: "#777777"
            font.pixelSize: 12
            elide: Text.ElideRight
        }
    }

    // Lazy settings overlay: only instantiated while open (active binding), so a
    // closed panel holds no objects/memory. Covers the full window (above the
    // 16px-margin FocusScope) and takes keyboard focus while shown.
    Loader {
        id: settingsLoader
        anchors.fill: parent
        z: 100
        active: win.settingsOpen
        source: "Settings.qml"
        onLoaded: item.forceActiveFocus()
    }

    Connections {
        target: settingsLoader.item
        function onClosed() {
            win.settingsOpen = false
            mainScope.forceActiveFocus()  // return key handling to the grid/search
        }
        function onFolderRequested() { win.openFolderPicker() }
    }

    // Manual folder entry, stacked above settings (z:100). Only instantiated
    // while open, so it costs nothing otherwise. It self-focuses its input.
    Loader {
        id: folderEntryLoader
        anchors.fill: parent
        z: 200
        active: win.folderEntryOpen
        source: "FolderInput.qml"
    }

    Connections {
        target: folderEntryLoader.item
        function onAccepted() { win.closeFolderEntry() }
        function onCancelled() { win.closeFolderEntry() }
    }
}
