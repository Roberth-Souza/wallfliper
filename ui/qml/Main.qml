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
        width: Math.min(1340, win.width - 80)
        height: Math.min(510, win.height - 80)
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
        carousel.currentIndex = carousel.count > 0 ? 0 : -1
    }

    // Space: apply but keep the overlay open, so you can audition wallpapers
    // live on the real desktop and keep browsing.
    function applyCurrent() {
        if (carousel.currentIndex >= 0)
            controller.apply(carousel.currentIndex)
    }

    function applyAndExit() {
        if (carousel.currentIndex < 0)
            return
        controller.apply(carousel.currentIndex)
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

    FocusScope {
        id: mainScope
        anchors.fill: bg
        anchors.margins: 8
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
                else if (event.key === Qt.Key_Up || event.key === Qt.Key_Left) {
                    win.exitSearchKeep()
                    carousel.decrementCurrentIndex()
                } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Right) {
                    win.exitSearchKeep()
                    carousel.incrementCurrentIndex()
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
            else if (event.key === Qt.Key_Up || event.key === Qt.Key_W || event.key === Qt.Key_K
                     || event.key === Qt.Key_Left || event.key === Qt.Key_A || event.key === Qt.Key_H)
                carousel.decrementCurrentIndex()
            else if (event.key === Qt.Key_Down || event.key === Qt.Key_S || event.key === Qt.Key_J
                     || event.key === Qt.Key_Right || event.key === Qt.Key_D || event.key === Qt.Key_L)
                carousel.incrementCurrentIndex()
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
            height: 26

            // Settings gear — the only chrome up here, top-left.
            Text {
                text: "⚙"   // gear (only icon allowed — no word is shorter)
                color: "#808080"
                font.pixelSize: 18
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: win.settingsOpen = true
                }
            }
        }

        // ---- Carousel: a horizontal strip of portrait wallcards ----
        // The wallpapers are the content; chrome recedes. Cards are portrait so
        // a handful read at once; the focused card widens to a landscape card
        // (after a short settle delay) so its wallpaper — almost always 16:9 —
        // is legible. Centered in the free vertical band between the top bar and
        // the bottom search; height-capped so a few cards fit across, not a wall.
        ListView {
            id: carousel
            anchors.left: parent.left
            anchors.right: parent.right
            // Extra horizontal breathing room between the cards and the frame.
            anchors.leftMargin: 22
            anchors.rightMargin: 22
            anchors.verticalCenter: parent.verticalCenter
            height: Math.min(parent.height - 56, 420)
            clip: true
            orientation: ListView.Horizontal
            spacing: 18
            // No drag/flick: movement is keyboard + wheel only, so the wheel
            // can't fight the built-in flick and desync the centered selection.
            interactive: false

            // Keep the focused card near the centre; the strip slides under it.
            highlightRangeMode: ListView.ApplyRange
            preferredHighlightBegin: Math.round(width / 2 - portraitW / 2)
            preferredHighlightEnd: Math.round(width / 2 + portraitW / 2)
            highlightMoveDuration: 220

            // Mouse wheel over the strip steps through wallpapers (one per
            // notch), re-centering on the new focus. Hover alone never moves it.
            WheelHandler {
                onWheel: (event) => {
                    if (event.angleDelta.y < 0 || event.angleDelta.x < 0)
                        carousel.incrementCurrentIndex()
                    else if (event.angleDelta.y > 0 || event.angleDelta.x > 0)
                        carousel.decrementCurrentIndex()
                }
            }
            // Keep a small off-screen buffer so a few neighbours pre-decode
            // without holding the whole library's bitmaps in RAM.
            cacheBuffer: 700

            // Card geometry. Portrait by default; the focused card grows to
            // `expandedW`. These ratios and the settle delay are the only knobs.
            readonly property real cardH: height
            readonly property real portraitW: Math.round(cardH * 0.66)
            readonly property real expandedW: Math.round(cardH * 1.35)
            readonly property int expandDelay: 650   // ms focused before widening
            // Decode the card thumbnail at 2x the card height (supersampled), so
            // it stays sharp on any display density without leaning on a possibly
            // under-reported devicePixelRatio. Bounded by the cache resolution.
            readonly property int decodeH: Math.round(cardH * 2)

            model: controller.model
            currentIndex: 0

            delegate: Item {
                id: cell
                required property int index
                required property string name
                required property string kind
                required property string thumbnail
                required property string preview
                property bool selected: ListView.isCurrentItem
                property bool expanded: false

                height: carousel.cardH
                width: expanded ? carousel.expandedW : carousel.portraitW
                Behavior on width { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }

                // The outline lands instantly on focus (immediate feedback); the
                // widening waits out `expandDelay` so scrubbing fast through cards
                // doesn't thrash. Collapse is instant when focus leaves.
                Timer { id: expandTimer; interval: carousel.expandDelay; onTriggered: cell.expanded = true }
                onSelectedChanged: {
                    if (selected) {
                        if (kind === "video") controller.ensurePreview(index)
                        expandTimer.restart()
                    } else {
                        expandTimer.stop()
                        cell.expanded = false
                    }
                }
                Component.onCompleted: if (selected) {
                    if (kind === "video") controller.ensurePreview(index)
                    expandTimer.restart()
                }

                // A video preview plays only on the focused cell; generated
                // lazily (cached after the first time); at most one cell previews.
                readonly property bool previewing: selected && kind === "video" && preview !== ""

                Rectangle {
                    anchors.fill: parent
                    radius: controller.corners === "sharp" ? 0 : 10
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
                        // Portrait cards crop a landscape wallpaper to a centre
                        // slice; the expanded card reveals the full width. Decode
                        // by card height (the crop's constraining axis) so it's
                        // sharp without over-decoding the cropped-away width.
                        fillMode: Image.PreserveAspectCrop
                        sourceSize.height: carousel.decodeH
                    }
                    AnimatedImage {
                        anchors.fill: parent
                        anchors.margins: 2
                        // Only load the clip while focused so unfocused cells hold
                        // no decoder/memory.
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
                        font.pixelSize: 24
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    // A single click focuses the card (never hover); double-click
                    // applies and exits.
                    onClicked: {
                        carousel.currentIndex = cell.index
                        if (win.searching)
                            win.exitSearchKeep()
                    }
                    onDoubleClicked: { carousel.currentIndex = cell.index; win.applyAndExit() }
                }
            }
        }

        // ---- Bottom bar: the minimal search marker, bottom-left ----
        Item {
            id: bottomBar
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: 20

            // Just a `/` at the bottom-left (mirrors the gear at top-left); it
            // grows into `/<query>` as you type. No placeholder word, no box.
            Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "/" + win.searchText
                // White while editing; grey when a filter persists after leaving
                // search; faint when idle.
                color: win.searching ? "#ffffff" : (win.searchText !== "" ? "#909090" : "#4a4a4a")
                font.pixelSize: 14
            }
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
