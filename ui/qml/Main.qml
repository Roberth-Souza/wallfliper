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

    // The layer surface holds the keyboard grab from the moment it maps, but Qt
    // doesn't always flip the window to "active" until the first pointer/key
    // event — so key events aren't routed to the focus scope and the user has to
    // click first. Nudge activation on map, and (re)grab focus to the main scope
    // whenever the window becomes active.
    Component.onCompleted: win.requestActivate()
    onActiveChanged: if (active) mainScope.forceActiveFocus()

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
    // it's just a translucent dark panel. Opacity is a user setting (see
    // Settings.qml), persisted in config.json. Base is near-black (#070708);
    // only the alpha varies. Sharp corners throughout, per DESIGN.md.
    Rectangle {
        id: bg
        anchors.centerIn: parent
        width: Math.min(1340, win.width - 80)
        height: Math.min(510, win.height - 80)
        color: Qt.rgba(7 / 255, 7 / 255, 8 / 255, controller.backgroundOpacity)

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
                controller.setKindFilter("image")   // images only
            else if (event.text === "v")
                controller.setKindFilter("video")   // videos only
            else if (event.text === "e")
                controller.setKindFilter("all")     // everything
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

            // Centring is done by hand (see _center + settleTimer), not via a
            // highlight range. A highlight range re-derives contentX from
            // currentIndex on the view's own layout schedule: ApplyRange only snaps
            // to centre on a user-driven index change (so the applied card opened
            // off-centre until the first arrow key), and StrictlyEnforceRange snaps
            // every frame but rewrites currentIndex to whatever card is in the
            // centre slot mid-layout (the "opens on a random wallpaper" bug). With
            // NoHighlightRange currentIndex is never touched; the only cost is the
            // initial-layout contentX clobber that settleTimer absorbs (see _prime).
            highlightRangeMode: ListView.NoHighlightRange
            // Half-view spacers at both ends so the focused card sits dead-centre
            // for *every* index — including the first and last, which otherwise
            // can't centre (nothing to scroll them past the edge into). The pad is
            // exactly the gap between a centred card and the view edge, so card i
            // centres at contentX = i*step - edgePad with no clamping artefacts.
            readonly property real edgePad: Math.max(0, (width - portraitW) / 2)
            header: Item { width: carousel.edgePad; height: carousel.height }
            footer: Item { width: carousel.edgePad; height: carousel.height }
            // Smooth one-card slide on navigation, enabled only after the initial
            // jump to the applied card (see _prime) so launch snaps there instantly
            // instead of sweeping from the index-0 baseline.
            readonly property int navMoveDuration: 220
            property bool _animateScroll: false
            Behavior on contentX {
                enabled: carousel._animateScroll
                NumberAnimation { duration: carousel.navMoveDuration; easing.type: Easing.OutCubic }
            }

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

            // Card geometry. Portrait by default; the focused card first *pops*
            // (full height + a touch wider, instantly) then widens to the full
            // landscape `expandedW` after the settle delay. These ratios and the
            // delay are the only knobs.
            readonly property real cardH: height
            readonly property real portraitW: Math.round(cardH * 0.66)
            // Quick "pop" size the instant a card is focused — a touch wider than
            // portrait — before the slower landscape widen.
            readonly property real poppedW: Math.round(cardH * 0.80)
            readonly property real expandedW: Math.round(cardH * 1.35)
            // Idle cards sit slightly inset so the focused card visibly lifts off
            // them when it pops to full strip height.
            readonly property real idleH: Math.round(cardH * 0.92)
            readonly property int expandDelay: 650   // ms focused before widening
            // Decode the card thumbnail at 2x the card height (supersampled), so
            // it stays sharp on any display density without leaning on a possibly
            // under-reported devicePixelRatio. Bounded by the cache resolution.
            readonly property int decodeH: Math.round(cardH * 2)

            model: controller.model
            currentIndex: 0

            // Open on the wallpaper that's already applied (appliedRow() returns
            // its row, or 0 when there's no saved state / it left the folder).
            // The model is fully populated before this view is built (the
            // Controller scans in its constructor), so the only moving part is the
            // view's own layout. Prime once it has *measured* geometry, gated on
            // contentWidth > 0 (only non-zero after the first layout pass sized the
            // delegates). Latched so a later resize/model reset can't yank it back.
            //
            // Why not a highlight range: StrictlyEnforceRange centres reliably but
            // *rewrites currentIndex* to whatever card lands in the centre slot
            // during the startup layout transient (the applied row silently becomes
            // a neighbour — the long-standing "opens on a random wallpaper" bug).
            // NoHighlightRange leaves currentIndex alone, at the cost that the view
            // resets contentX to its content-start once or twice during initial
            // layout, clobbering the offset _center sets. settleTimer re-asserts the
            // centred offset across those passes until it holds — see below.
            property bool _primed: false
            function _prime(): void {
                if (_primed || count <= 0 || width <= 0 || contentWidth <= 0)
                    return
                _primed = true
                currentIndex = controller.appliedRow()
                _center()
                settleTimer.restart()
            }
            // Place the current card dead-centre. Every layout slot is exactly
            // portraitW (the focused card's pop/expand overflows its slot without
            // resizing it), so card i sits at content-x i*step and centres at
            // contentX = i*step - edgePad. The end spacers (header/footer) make the
            // valid contentX range [originX, originX + contentWidth - width] exactly
            // contain every card's centred offset, so the clamp only guards rounding.
            // _desiredX is remembered so settleTimer can detect a layout clobber.
            property real _desiredX: 0
            function _center(): void {
                if (count <= 0 || width <= 0)
                    return
                const step = portraitW + spacing
                const target = currentIndex * step - edgePad
                const maxX = originX + Math.max(0, contentWidth - width)
                _desiredX = Math.max(originX, Math.min(target, maxX))
                contentX = _desiredX
            }
            // Bounded startup settle, NOT an idle loop: it runs only right after
            // priming and stops the instant the centred offset holds (or after a
            // hard cap). The view clobbers contentX back to its content-start during
            // the first few layout passes; re-assert each frame until it sticks for
            // 3 consecutive frames, then enable the navigation slide and stop. There
            // is no clean QML signal for "initial layout settled", so this is the
            // event-driven alternative to racing a single fixed delay.
            Timer {
                id: settleTimer
                interval: 16
                repeat: true
                property int stable: 0
                property int ticks: 0
                onTriggered: {
                    ticks++
                    if (Math.abs(carousel.contentX - carousel._desiredX) < 0.5) {
                        if (++stable >= 3) { stop(); carousel._animateScroll = true }
                    } else {
                        stable = 0
                        carousel._center()
                    }
                    if (ticks > 40) { stop(); carousel._animateScroll = true }
                }
            }
            onCurrentIndexChanged: _center()
            onContentWidthChanged: _prime()
            onWidthChanged: { _prime(); _center() }
            onCountChanged: _prime()
            Component.onCompleted: _prime()

            delegate: Item {
                id: cell
                required property int index
                required property string name
                required property string kind
                required property string thumbnail
                required property string preview
                property bool selected: ListView.isCurrentItem
                property bool expanded: false   // full landscape widen (after the settle delay)

                // The layout slot stays portrait, so the strip spacing and the
                // centred-scroll maths (see _center / ApplyRange) never change.
                // The card *visual* grows beyond this box, centred — so it expands
                // symmetrically on both sides instead of only rightward — and the
                // focused cell is z-lifted to draw above the neighbours it overflows.
                height: carousel.cardH
                width: carousel.portraitW
                z: selected ? 1 : 0

                // Pop the instant it's focused (no waiting on expandDelay): the
                // card lifts to full strip height and a wider portrait, standing
                // off the idle cards. Only *then*, after the delay, does it widen
                // to a full landscape card. Collapse settles back when focus leaves.
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
                    id: cardVisual
                    anchors.centerIn: parent
                    // Centred so growth overflows symmetrically. Idle size by
                    // default; the two states drive the pop, then the widen.
                    width: carousel.portraitW
                    height: carousel.idleH
                    color: "#161616"
                    border.color: cell.selected ? "#ffffff" : "transparent"
                    border.width: 2
                    clip: true

                    states: [
                        State {
                            name: "popped"
                            when: cell.selected && !cell.expanded
                            PropertyChanges {
                                cardVisual.width: carousel.poppedW
                                cardVisual.height: carousel.cardH
                            }
                        },
                        State {
                            name: "expanded"
                            when: cell.selected && cell.expanded
                            PropertyChanges {
                                cardVisual.width: carousel.expandedW
                                cardVisual.height: carousel.cardH
                            }
                        }
                    ]
                    transitions: [
                        // Fast, smooth pop the moment focus lands.
                        Transition {
                            to: "popped"
                            NumberAnimation { properties: "width,height"; duration: 150; easing.type: Easing.OutCubic }
                        },
                        // Slower, deliberate landscape widen.
                        Transition {
                            to: "expanded"
                            NumberAnimation { properties: "width,height"; duration: 300; easing.type: Easing.OutCubic }
                        },
                        // Settle back to idle when focus leaves.
                        Transition {
                            to: ""
                            NumberAnimation { properties: "width,height"; duration: 180; easing.type: Easing.OutCubic }
                        }
                    ]

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
                    anchors.fill: cardVisual
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
