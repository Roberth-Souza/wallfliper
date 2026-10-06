import QtQuick
import QtQuick.Effects
import QtQuick.Shapes
import QtQuick.Window
import org.kde.layershell as LayerShell

Window {
    id: win
    visible: true
    width: Screen.width
    height: Screen.height
    // Transparent surface: there is no backdrop panel — the desktop shows
    // through everywhere except the floating bar and the wallcards. The bar is
    // painted with alpha (Theme.bg) so a Hyprland `layerrule = blur,
    // wallfliper` blurs only it; cards and text are opaque and stay crisp.
    color: "transparent"

    // wlr-layer-shell: full-screen overlay above everything (anchored to all
    // four edges). The surface fills the screen but is transparent except for
    // the floating bar and the card strip; the transparent rest is a
    // click-catcher that dismisses (see dismissArea below) —
    // click-away-to-close, like a launcher.
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

    // Click-away-to-close: a full-screen catcher behind the bar and the cards.
    // There is no backdrop panel — the surface is transparent everywhere except
    // the floating bar and the wallcards, so the desktop shows through (and
    // `layerrule = ignorezero` keeps blur off it). Clicking empty space cancels
    // an active search first, then dismisses, like clicking away from a
    // launcher. Clicks on the bar/cards are swallowed by their own MouseAreas.
    MouseArea {
        id: dismissArea
        anchors.fill: parent
        onClicked: {
            if (win.searching)
                win.exitSearchClear()
            else if (win.colorMode)
                win.exitColorClear()
            else
                Qt.quit()
        }
    }

    // Toggles the lazy settings overlay. No gear icon: settings open by typing
    // the `/config` command in search (Enter). Closed = panel unloaded.
    property bool settingsOpen: false

    // Search is modal: `/` enters search mode so the printable keys — including
    // the w/a/s/d + h/j/k/l navigation and space — stay free as commands in
    // normal mode. Once searching, every printable key filters live (any
    // filename is typable); arrows still move. Shown in the top prompt as
    // `/<query>`, rofi-style, next to the app name.
    //
    // Leaving search has two flavors: `Enter`, an arrow key, or clicking a
    // result *confirms* the filter (exitSearchKeep — drop to normal nav, query
    // kept, grid stays filtered); `Esc`, `/` again, or clicking empty app chrome
    // *cancels* it (exitSearchClear — wipe the query, full grid returns).
    property string searchText: ""
    property bool searching: false
    onSearchTextChanged: {
        controller.setFilter(searchText)
        win.focusIndex(carousel.count > 0 ? 0 : -1)
    }

    // Honeycomb is its own view (a hex grid, not the PathView strip), so the
    // focused row lives in whichever view is active; the other one is synced
    // only when `t` switches between them.
    readonly property bool honeycomb: controller.cardLayout === "honeycomb"
    // Honeycomb only: gap between the screen edges and the bar / color strip.
    readonly property real edgeMargin: 24
    property real hiveAmount: honeycomb ? 1 : 0
    Behavior on hiveAmount { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    readonly property Honeycomb hive: hiveLoader.item as Honeycomb
    readonly property int currentIndex:
        honeycomb && hive ? hive.currentIndex : carousel.currentIndex
    onHoneycombChanged: if (!honeycomb && hive) carousel.snapIndex(hive.currentIndex)

    function focusIndex(i: int): void {
        if (honeycomb && win.hive)
            win.hive.focusIndex(i, false)
        else
            carousel.focusIndex(i)
    }

    // dx: a/d, h/l, Left/Right. dy: w/s, k/j, Up/Down. The strip has one
    // axis, so both step it; the grid moves by column or by row.
    function navigate(dx: int, dy: int, chained: bool): void {
        if (honeycomb && win.hive) {
            if (dx !== 0)
                win.hive.moveColumn(dx)
            else
                win.hive.moveRow(dy)
        } else
            carousel.scrollBy(dx + dy, chained)
    }

    // Space: apply but keep the overlay open, so you can audition wallpapers
    // live on the real desktop and keep browsing.
    function applyCurrent() {
        if (win.currentIndex >= 0)
            controller.apply(win.currentIndex)
    }

    function applyAndExit() {
        if (win.currentIndex < 0)
            return
        // A shader switch is painted by our own surface and only applies the
        // wallpaper once that surface covers the screen — quitting here would
        // kill it mid-flight and set nothing. Hide instead (which releases the
        // keyboard grab, so it feels like closing) and exit when it lands.
        if (controller.apply(win.currentIndex)) {
            win.quitAfterTransition = true
            win.visible = false
        } else {
            Qt.quit()
        }
    }

    // --- shader-painted wallpaper switch ------------------------------------
    // A separate layer-shell surface on the bottom layer, created per switch and
    // destroyed with it (see TransitionSurface.qml). Held in a plain property
    // rather than a Loader because it is a Window, not an Item.
    property var transitionSurface: null
    property bool quitAfterTransition: false

    Component {
        id: transitionComponent
        TransitionSurface {}
    }

    function startShaderTransition(oldUrl, newUrl, shaderUrl, ms) {
        win.dropTransitionSurface()
        const surface = transitionComponent.createObject(null, {
            oldSource: oldUrl,
            newSource: newUrl,
            shaderUrl: shaderUrl,
            durationMs: ms
        })
        if (surface === null) {          // shader or component broken
            controller.shaderTransitionFailed()
            win.finishTransition()
            return
        }
        surface.covered.connect(controller.shaderSurfaceReady)
        surface.finished.connect(function() {
            controller.shaderTransitionDone()
            win.finishTransition()
        })
        surface.failed.connect(function() {
            controller.shaderTransitionFailed()
            win.finishTransition()
        })
        win.transitionSurface = surface
    }

    function finishTransition() {
        win.dropTransitionSurface()
        if (win.quitAfterTransition)
            Qt.quit()
    }

    function dropTransitionSurface() {
        if (win.transitionSurface !== null) {
            win.transitionSurface.destroy()
            win.transitionSurface = null
        }
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

    // Color filter mode: `c` shows the swatch strip below the carousel and
    // routes the nav keys to it; moving the cursor applies the filter live
    // (audition-style, like Space for wallpapers). Enter confirms and keeps
    // the filter; Esc / `c` again cancels and clears it. The strip stays
    // visible while a filter is active — it doubles as the filter indicator.
    property bool colorMode: false
    // "all" clears, rendered as a dark swatch with a × — then the fixed
    // palette in Controller order. colorPalette is constant, evaluated once.
    readonly property var colorEntries:
        [{ name: "all", hex: "#161616" }].concat(controller.colorPalette)

    function enterColorMode() {
        controller.ensureColorIndex()
        win.colorMode = true
    }

    function exitColorKeep() {
        win.colorMode = false
    }

    function exitColorClear() {
        win.colorMode = false
        controller.setColorFilter("all")
    }

    // Step the swatch cursor; the cursor *is* the active filter (live apply),
    // so there is a single selection language: the white border.
    function moveColor(step: int): void {
        let i = colorEntries.findIndex(e => e.name === controller.colorFilter)
        if (i < 0)
            i = 0
        i = Math.min(Math.max(i + step, 0), colorEntries.length - 1)
        controller.setColorFilter(colorEntries[i].name)
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
        function onShaderTransitionRequested(oldUrl, newUrl, shaderUrl, ms) {
            win.startShaderTransition(oldUrl, newUrl, shaderUrl, ms)
        }
    }

    FocusScope {
        id: mainScope
        anchors.fill: parent
        focus: true

        Keys.onPressed: (event) => {
            if (win.hive)
                win.hive.pointerActive = false
            // Esc always exits immediately, in any mode.
            if (event.key === Qt.Key_Escape) {
                if (win.searching)
                    win.exitSearchClear()
                else if (win.colorMode)
                    win.exitColorClear()
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
                // confirms without moving; `/` again cancels it. `/config` is a
                // command, not a query: Enter on it opens settings instead.
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    if (win.searchText === "config") {
                        win.exitSearchClear()
                        win.settingsOpen = true
                    } else
                        win.exitSearchKeep()
                }
                else if (event.key === Qt.Key_Up || event.key === Qt.Key_Down
                         || event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
                    win.exitSearchKeep()
                    win.navigate(event.key === Qt.Key_Left ? -1 : event.key === Qt.Key_Right ? 1 : 0,
                                 event.key === Qt.Key_Up ? -1 : event.key === Qt.Key_Down ? 1 : 0,
                                 event.isAutoRepeat)
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

            if (win.colorMode) {
                // Color mode: the nav keys move the swatch cursor, applying
                // the filter live. Enter confirms (filter kept), `c` cancels.
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                    win.exitColorKeep()
                else if (event.text === "c")
                    win.exitColorClear()
                else if (event.key === Qt.Key_Up || event.key === Qt.Key_W || event.key === Qt.Key_K
                         || event.key === Qt.Key_Left || event.key === Qt.Key_A || event.key === Qt.Key_H)
                    win.moveColor(-1)
                else if (event.key === Qt.Key_Down || event.key === Qt.Key_S || event.key === Qt.Key_J
                         || event.key === Qt.Key_Right || event.key === Qt.Key_D || event.key === Qt.Key_L)
                    win.moveColor(1)
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
            else if (event.text === "c")
                win.enterColorMode()                // color filter strip
            else if (event.text === "r")
                win.honeycomb && win.hive
                    ? win.hive.selectRandom() : carousel.selectRandom()
            else if (event.text === "t")
                controller.setCardLayout(carousel.nextLayout[controller.cardLayout] ?? "push")
            else if (event.key === Qt.Key_D && (event.modifiers & Qt.ShiftModifier)) {
                // Shift+D: move the selected wallpaper file to the trash (no
                // confirmation). Must precede the nav branch below, which
                // claims plain `d`. The next card slides into the centre
                // (ListView keeps the numeric currentIndex, which now names
                // the following row; onCountChanged re-centres it).
                if (win.currentIndex >= 0)
                    controller.deleteWallpaper(win.currentIndex)
            }
            else if (event.key === Qt.Key_Up || event.key === Qt.Key_W || event.key === Qt.Key_K)
                win.navigate(0, -1, event.isAutoRepeat)
            else if (event.key === Qt.Key_Down || event.key === Qt.Key_S || event.key === Qt.Key_J)
                win.navigate(0, 1, event.isAutoRepeat)
            else if (event.key === Qt.Key_Left || event.key === Qt.Key_A || event.key === Qt.Key_H)
                win.navigate(-1, 0, event.isAutoRepeat)
            else if (event.key === Qt.Key_Right || event.key === Qt.Key_D || event.key === Qt.Key_L)
                win.navigate(1, 0, event.isAutoRepeat)
            else
                return
            event.accepted = true
        }

        // ---- Floating bar: `wallfliper  /<query>`, detached, centered above
        // the card strip. The only piece of chrome besides the cards; future
        // features (filters, source tabs) dock here. Framed with the same 2px
        // white border language as the selected card. Visuals only — keys stay
        // on mainScope.
        Rectangle {
            id: topBar
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: carousel.top
            // Flow cards outgrow the band; the bar rides up to clear them and
            // the crosshair above the focused card. Honeycomb fills the
            // screen, so the bar slides to its top edge.
            readonly property real bandMargin: 24 + (carousel.flowOverhang - 24) * carousel.flowAmount
            anchors.bottomMargin: bandMargin
                + (carousel.y - win.edgeMargin - height - bandMargin) * win.hiveAmount
            width: Math.max(320, promptRow.implicitWidth + 40)
            height: promptRow.implicitHeight + 22
            color: Theme.bg
            border.color: Theme.frame
            border.width: Theme.frameWidth

            // Swallow clicks so they don't fall through to dismissArea.
            MouseArea { anchors.fill: parent }

            Row {
                id: promptRow
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12

                Text {
                    id: promptTitle
                    text: "wallfliper"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.bold: true
                    font.pixelSize: 15
                }

                // The query grows next to the name as you type; a `|` cursor
                // marks live editing. White while editing, grey when a filter
                // persists after leaving search, hidden when idle.
                Text {
                    anchors.baseline: promptTitle.baseline
                    visible: win.searching || win.searchText !== ""
                    text: "/" + win.searchText + (win.searching ? "|" : "")
                    color: win.searching ? Theme.text : Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: 15
                }
            }
        }

        // ---- Flow decoration: orbit ellipse, side chevrons and crosshair.
        // The flow layout is the deliberately dramatic mode, exempt from the
        // flat/no-ornament rules (see DESIGN.md). Declared before the carousel
        // so the orbit passes behind the cards; unloaded in the other modes.
        Loader {
            anchors.fill: parent
            active: carousel.flowAmount > 0
            sourceComponent: Item {
                id: flowDecor
                opacity: carousel.flowAmount

                readonly property real cx: width / 2
                readonly property real cy: carousel.y + carousel.height / 2
                readonly property real cardTop: cy - carousel.flowH / 2
                readonly property real cardBottom: cy + carousel.flowH / 2
                readonly property color line: Qt.rgba(1, 1, 1, 0.45)
                readonly property real orbitX: carousel.orbitA
                readonly property real orbitY: carousel.orbitB
                // Front arc's low point sits orbitDrop below the focused card's centre.
                readonly property real orbitCy: cy + carousel.orbitDrop - carousel.orbitB
                readonly property real arrowX: Math.min(carousel.flowW * 2.06, width / 2 - 32)
                readonly property real arrowH: Math.round(carousel.flowH * 0.03)

                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: flowDecor.line
                        strokeWidth: 1
                        fillColor: "transparent"
                        PathAngleArc {
                            centerX: flowDecor.cx
                            centerY: flowDecor.orbitCy
                            radiusX: flowDecor.orbitX
                            radiusY: flowDecor.orbitY
                            startAngle: 0
                            sweepAngle: 360
                        }
                    }
                }

                // Crosshair: a hairline through the focused card's axis, from
                // the bar down to the card and on below it, ticked mid-way.
                Repeater {
                    model: [flowDecor.cardTop - carousel.flowReach, flowDecor.cardBottom]
                    delegate: Item {
                        required property real modelData
                        x: flowDecor.cx - 8
                        y: modelData
                        width: 16
                        height: carousel.flowReach
                        Rectangle {
                            x: 8
                            width: 1
                            height: parent.height
                            color: flowDecor.line
                        }
                        Rectangle {
                            y: Math.round(parent.height / 2)
                            width: parent.width + 1
                            height: 1
                            color: Theme.muted
                        }
                    }
                }

                Repeater {
                    model: [-1, 1]
                    delegate: Item {
                        id: chevron
                        required property int modelData
                        x: flowDecor.cx + modelData * flowDecor.arrowX - width / 2
                        y: flowDecor.orbitCy - height / 2
                        width: flowDecor.arrowH * 2
                        height: flowDecor.arrowH * 2

                        Shape {
                            anchors.centerIn: parent
                            width: flowDecor.arrowH / 2
                            height: flowDecor.arrowH
                            preferredRendererType: Shape.CurveRenderer
                            ShapePath {
                                strokeColor: Theme.text
                                strokeWidth: 1.5
                                fillColor: "transparent"
                                capStyle: ShapePath.FlatCap
                                startX: chevron.modelData < 0 ? flowDecor.arrowH / 2 : 0
                                startY: 0
                                PathLine {
                                    x: chevron.modelData < 0 ? 0 : flowDecor.arrowH / 2
                                    y: flowDecor.arrowH / 2
                                }
                                PathLine {
                                    x: chevron.modelData < 0 ? flowDecor.arrowH / 2 : 0
                                    y: flowDecor.arrowH
                                }
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: carousel.scrollBy(chevron.modelData, false)
                        }
                    }
                }
            }
        }

        // ---- Carousel: an infinite loop of portrait wallcards ----
        // The wallpapers are the content; chrome recedes. Cards are portrait so
        // a handful read at once; the focused card widens to the wallpaper's
        // own aspect (after a short settle delay) so it reads in full. The
        // strip wraps: there is no first/last card, always a next/previous.
        PathView {
            id: carousel
            // Full-bleed band, ~40% tall: the strip spans the whole screen so
            // the outermost cards are cut by the screen edge instead of ending
            // in mid-air — the loop reads as running off both sides rather than
            // as a centered widget. (A library smaller than the slot count
            // still collapses to a compact centered strip; see pathSlots.)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            height: Math.min(Math.round(win.height * 0.40), 480)
            // Flow cards (and the focused card's glow) outgrow the band.
            clip: flowAmount <= 0
            // Cross-fades with the honeycomb grid, which replaces it.
            opacity: 1 - win.hiveAmount
            visible: opacity > 0
            // No drag/flick: movement is keyboard + wheel only, so the wheel
            // can't fight the built-in flick and desync the centered selection.
            interactive: false

            // The strip is an infinite loop: PathView is circular, so
            // increment/decrement wrap and there is no first/last card. The
            // current card is *always* dead-center (StrictlyEnforceRange on a
            // 0.5 highlight), which retires the hand-rolled centering the old
            // ListView needed (edge pinning, contentX re-assertion, settle
            // timer) — a loop has no edges to pin to.
            preferredHighlightBegin: 0.5
            preferredHighlightEnd: 0.5
            highlightRangeMode: PathView.StrictlyEnforceRange
            movementDirection: PathView.Shortest
            // Push mode glides slower so the size-follows-position widen reads;
            // overdraw mode keeps the original snappy step.
            readonly property int navMoveDuration: pushNeighbors || flowMode ? 380 : 220
            // Time budget per extra card in a multi-step glide; the speed cap.
            readonly property int glidePerStep: 100
            // Steps folded into the running glide. A single step expands the
            // focused card while it glides in; a burst (held key, wheel spin,
            // random select) only pops the cards it passes and expands the one
            // it lands on, so the strip doesn't ripple open/shut mid-sweep.
            property int _burst: 0
            readonly property bool sweeping:
                (glide.running && _burst > 1) || bounce.running
            // How far a centred card may widen past the pop: 0 mid-sweep, 1 at
            // rest. Card size itself is driven by distance from the centre, so
            // a single step's widen moves in lockstep with the glide. A random
            // select lands, rests a beat, then widens slowly: after a long
            // jump the eye needs to find the card before it opens.
            property real expandAmount: 1
            property bool _randomLanding: false
            readonly property int randomSettleDelay: 300
            readonly property int randomExpandDuration: 600
            onSweepingChanged: {
                expandOut.stop()
                expandIn.stop()
                expandSettle.stop()
                if (sweeping)
                    expandOut.restart()
                else if (_randomLanding) {
                    _randomLanding = false
                    expandSettle.restart()
                } else
                    expandIn.restart()
            }
            NumberAnimation {
                id: expandOut
                target: carousel
                property: "expandAmount"
                to: 0
                duration: 150
                easing.type: Easing.OutCubic
            }
            NumberAnimation {
                id: expandIn
                target: carousel
                property: "expandAmount"
                to: 1
                duration: carousel.navMoveDuration
                easing.type: Easing.OutCubic
            }
            SequentialAnimation {
                id: expandSettle
                PauseAnimation { duration: carousel.randomSettleDelay }
                NumberAnimation {
                    target: carousel
                    property: "expandAmount"
                    to: 1
                    duration: carousel.randomExpandDuration
                    easing.type: Easing.InOutCubic
                }
            }
            // Snap (not sweep) to the applied card on launch; animate after.
            property bool _primed: false
            highlightMoveDuration: _primed ? navMoveDuration : 0

            // Smooth navigation: stepping currentIndex directly moves the
            // outline to the new card *before* the strip catches up (offset
            // animates behind), so fast scrolling parks the outline off-center.
            // Instead, navigation glides the view `offset`; with
            // StrictlyEnforceRange the view re-derives currentIndex from
            // whatever card is at the 0.5 highlight, so the outline is always
            // the centered card, even mid-glide.
            // Qt normalizes assigned offsets mod count, so gliding across the
            // wrap seam (negative / > count values mid-animation) is safe.
            //
            // `chained` marks continuous input (key auto-repeat, wheel
            // notches): while it keeps the running direction, each step
            // extends the same glide (one accelerated sweep). A *discrete*
            // command — a fresh key press, or any reversal — must obey
            // immediately instead of piling onto a far-away target: it
            // re-targets from the live offset to one card past the nearest
            // one, so the strip decelerates right away and lands where the
            // user asked.
            function scrollBy(steps: int, chained: bool): void {
                if (count <= 0 || steps === 0)
                    return
                _randomLanding = false
                const sameDir = glide.running
                    && Math.sign(glide.to - glide.from) === Math.sign(-steps)
                const extend = chained && sameDir
                const target = extend ? glide.to - steps : Math.round(offset) - steps
                _burst = extend ? _burst + 1 : 1
                glide.stop()
                bounce.stop()
                glide.from = offset
                glide.to = target
                // Duration grows with distance so the sweep speed is capped at
                // ~1 card per glidePerStep ms no matter how fast the burst.
                glide.duration = navMoveDuration
                    + glidePerStep * Math.max(0, Math.abs(target - offset) - 1)
                glide.restart()
            }
            // Direct focus (filter reset): kill any glide first so it can't
            // keep driving offset against the index-driven snap.
            function focusIndex(i: int): void {
                _randomLanding = false
                glide.stop()
                bounce.stop()
                currentIndex = i
            }
            // Back from honeycomb: land on the grid's card without sweeping
            // the strip there (same trick as the launch snap).
            function snapIndex(i: int): void {
                _primed = false
                focusIndex(i)
                Qt.callLater(() => _primed = true)
            }
            // Click: in push mode, glide the shortest way round to the card as
            // one burst; overdraw mode snaps by index like before.
            function glideTo(i: int): void {
                if (!pushNeighbors && !flowMode) {
                    focusIndex(i)
                    return
                }
                if (count <= 0 || i === currentIndex)
                    return
                let steps = ((i - currentIndex) % count + count) % count
                if (steps > count / 2)
                    steps -= count
                _randomLanding = false
                glide.stop()
                bounce.stop()
                _burst = Math.abs(steps)
                glide.from = offset
                glide.to = Math.round(offset) - steps
                glide.duration = navMoveDuration
                    + glidePerStep * Math.max(0, Math.abs(steps) - 1)
                glide.restart()
            }
            NumberAnimation {
                id: glide
                target: carousel
                property: "offset"
                easing.type: Easing.OutCubic
            }

            // Random select: glide to a random other card, overshoot it and
            // settle back. The overshoot is a fixed fraction of a card (not of
            // the distance, as an OutBack easing would be) and stays under half
            // a card, so the centered card never changes during the bounce.
            readonly property real bounceOvershoot: 0.45
            readonly property int bounceSettleDuration: 320
            readonly property int randomMaxDuration: 650
            // A random select always travels at least this far, and preferably
            // to a card outside the visible strip, so it never reads as a nudge.
            readonly property int randomMinSteps: 4
            function selectRandom(): void {
                if (count < 2)
                    return
                glide.stop()
                bounce.stop()
                // Circular strip: the farthest card is half a lap away either way.
                const maxSteps = Math.floor(count / 2)
                const offscreen = Math.ceil(width / (2 * step) + 0.5)
                let minSteps = Math.max(randomMinSteps, offscreen)
                if (minSteps > maxSteps)
                    minSteps = Math.min(randomMinSteps, maxSteps)
                const distance = minSteps
                    + Math.floor(Math.random() * (maxSteps - minSteps + 1))
                const steps = Math.random() < 0.5 ? -distance : distance
                const target = Math.round(offset) - steps
                bounceOut.from = offset
                bounceOut.to = target - Math.sign(steps) * bounceOvershoot
                bounceOut.duration = Math.min(randomMaxDuration,
                    navMoveDuration + glidePerStep * Math.max(0, Math.abs(steps) - 1))
                // Explicit `from`: by the time this step starts, Qt has wrapped the
                // live offset mod count, so an implicit start value would sweep a
                // whole lap back to the same card instead of the short settle.
                bounceBack.from = bounceOut.to
                bounceBack.to = target
                // Set after the stops above: they can flip `sweeping` off and
                // would consume the flag before the bounce even starts.
                _randomLanding = true
                bounce.restart()
            }
            SequentialAnimation {
                id: bounce
                NumberAnimation {
                    id: bounceOut
                    target: carousel
                    property: "offset"
                    easing.type: Easing.OutCubic
                }
                NumberAnimation {
                    id: bounceBack
                    target: carousel
                    property: "offset"
                    duration: carousel.bounceSettleDuration
                    easing.type: Easing.InOutSine
                }
            }

            // Slot geometry. PathView has no `spacing`: the step is baked into
            // the path length — evenly spaced stops of `step` px each, centered
            // on the view. Cards sit nearly glued (6px). `slots` is odd so the
            // center slot is symmetric, +2 beyond the viewport so items
            // enter/leave the path outside the clipped band (no visible pop-in
            // at the seam). pathItemCount also bounds live delegates, replacing
            // the old cacheBuffer.
            //
            // The path length must track the *lesser* of slots and count:
            // PathView spreads all items across the whole path whenever
            // count <= pathItemCount, so a small library on a slots-sized path
            // would fan out with big gaps (and stray partial cards at the band
            // edges). Shrinking the path to count*step keeps the spacing at
            // exactly one step — a compact centered strip.
            //
            // `slots` is sized from the slim (narrowest) step, not the live one,
            // so it stays put while `t` morphs idleW: a pathItemCount change
            // makes PathView rebuild every delegate, and the rebuilt focused
            // card loses its expanded/selected state mid-toggle.
            readonly property real step: idleW + 6
            readonly property int slots: 2 * Math.ceil((width / (slimW + 6) + 2) / 2) + 1
            readonly property int pathSlots: count > 0 ? Math.min(slots, count) : slots
            pathItemCount: pathSlots
            cacheItemCount: 2
            path: Path {
                startX: carousel.width / 2 - carousel.pathSlots * carousel.step / 2
                startY: carousel.height / 2
                PathLine {
                    x: carousel.width / 2 + carousel.pathSlots * carousel.step / 2
                    y: carousel.height / 2
                }
            }

            // Mouse wheel over the strip steps through wallpapers (one per
            // notch), re-centering on the new focus. Hover alone never moves it.
            WheelHandler {
                onWheel: (event) => {
                    // Wheel notches are always "chained": a burst reads as one
                    // sweep; a reverse notch still obeys instantly (direction
                    // check in scrollBy).
                    if (event.angleDelta.y < 0 || event.angleDelta.x < 0)
                        carousel.scrollBy(1, true)
                    else if (event.angleDelta.y > 0 || event.angleDelta.x > 0)
                        carousel.scrollBy(-1, true)
                }
            }

            // Card geometry. Portrait by default. Push mode: the focused card
            // widens to the full landscape `expandedW` while it glides in, and
            // mid-sweep only *pops* (full height + a touch wider). Overdraw
            // mode: it pops instantly, then widens after expandDelay.
            readonly property real cardH: height
            readonly property real portraitW: Math.round(cardH * 0.66)
            // Push mode idles narrower: nothing overlaps there, so slimmer
            // slats fit more of the library on screen. Morphs with pushAmount
            // so `t` reflows the strip instead of jumping.
            readonly property real slimW: Math.round(cardH * 0.42)
            readonly property real idleW: portraitW + (slimW - portraitW) * pushAmount
            // "Pop" size — a touch wider than portrait — a card reaches as it
            // centres mid-sweep; at rest it widens on to the wallpaper's own
            // aspect (per-cell `expandedW` on the delegate).
            readonly property real poppedW: Math.round(cardH * 0.80)
            // Idle cards sit slightly inset so the focused card visibly lifts off
            // them when it pops to full strip height.
            readonly property real idleH: Math.round(cardH * 0.92)
            readonly property int expandDelay: 650   // overdraw mode: ms focused before widening
            // Decode the card thumbnail at 2x the card height (supersampled), so
            // it stays sharp on any display density without leaning on a possibly
            // under-reported devicePixelRatio. Bounded by the cache resolution.
            readonly property int decodeH: Math.round(cardH * 2)

            // A card wider than its slot shoves its neighbours outward instead
            // of drawing over them (`t` toggles back to overdraw). The path
            // can't vary spacing per item, so the slots stay fixed and each
            // card's visual is translated to where a contiguous strip would put
            // it. Only the two cells straddling the centre grow: A at rel
            // (-1, 0], B at (0, 1). Their centres sit step + (gA + gB) / 2
            // apart and the view centre interpolates between them by the
            // offset fraction, so every card outside the pair moves at exactly
            // the glide's speed. (Spreading each card's growth over its slot
            // instead makes outer cards stall, or even back up once the growth
            // exceeds the step, every time a card crosses the centre.)
            // Positions come from `offset` (cell `rel`), never from item x:
            // PathView lays items out one by one, so reading a neighbour's x
            // mid-layout sees last frame's value and jitters.
            readonly property bool pushNeighbors: controller.cardLayout === "push"
            property real pushAmount: pushNeighbors ? 1 : 0
            Behavior on pushAmount { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
            // Push mode widens the focused card without waiting for
            // expandDelay, so switching to overdraw must keep it expanded.
            onPushNeighborsChanged: {
                if (!pushNeighbors && currentItem && !sweeping)
                    currentItem.expanded = true
            }
            readonly property var nextLayout:
                ({ push: "overlay", overlay: "flow", flow: "honeycomb", honeycomb: "push" })

            // Flow: a cover-flow arc. The focused card faces the viewer; the
            // rest turn about their vertical axis toward the centre, shrink,
            // darken and bunch up with distance. Sized from the window, not
            // the band, so it reads as large as a hero shot. Everything is a
            // pure function of a cell's `rel`, like push, and the 3D look is a
            // single per-card projective matrix (cardMatrix), so a glide only
            // updates matrices: no relayout, and the glow's blur stays cached.
            readonly property bool flowMode: controller.cardLayout === "flow"
            property real flowAmount: flowMode ? 1 : 0
            Behavior on flowAmount { NumberAnimation { duration: 360; easing.type: Easing.OutCubic } }
            readonly property real flowH: Math.round(win.height * 0.56)
            readonly property real flowW: Math.round(flowH * 0.585)
            // The focused flow card widens after expandDelay, but only until
            // its edges reach the first neighbours' centres: it overdraws half
            // of each instead of opening the arc (neighbours never move).
            readonly property real flowWideMax: Math.round(2 * flowX(1))
            readonly property int flowWidenInDuration: 300
            readonly property int flowWidenOutDuration: 150
            // Crosshair arm length above/below the focused card.
            readonly property real flowReach: Math.round(win.height * 0.08)
            // How far the bar / color strip move off the band edges in flow.
            readonly property real flowOverhang: (flowH - cardH) / 2 + flowReach
            // Turn about the vertical axis, steepening evenly with distance so
            // the cards bend round the orbit like slats on a drum: 28°, 56°,
            // 70° at one, two, three slots out (capped so the end cards stay
            // readable).
            function flowAngle(a: real): real {
                return Math.min(70, 28 * a) * Math.PI / 180
            }
            // Eye distance for the perspective divide: smaller is more dramatic.
            readonly property real flowDepth: flowW * 1.86
            // Centre offset of a card `a` slots out, in flowW: the first
            // neighbour sits a full card away, then each gap shrinks by 0.42,
            // which puts the third card over the orbit's end (hiding its tip).
            function flowX(r: real): real {
                const a = Math.abs(r)
                const x = a <= 1 ? 0.99 * a : 0.99 + 0.57 * (1 - Math.pow(0.42, a - 1)) / 0.58
                return Math.sign(r) * x * flowW
            }
            // The orbit ring the cards are mounted on, seen from above: it
            // crosses every card at the same relative height (`orbitDrop`
            // below the centre at full scale), so cards rise along the ring's
            // front arc as they move out toward its ends.
            readonly property real orbitA: Math.min(flowW * 1.83, width / 2 - 80)
            readonly property real orbitB: flowH * 0.15
            readonly property real orbitDrop: flowH * 0.25
            function flowRise(r: real): real {
                const u = flowX(r) / orbitA
                return orbitB * (1 - Math.sqrt(Math.max(0, 1 - u * u)))
                    - orbitDrop * (1 - flowScale(Math.abs(r)))
            }
            function flowScale(a: real): real {
                return a <= 1 ? 1 - 0.29 * a
                    : a <= 2 ? 0.71 - 0.15 * (a - 1)
                    : Math.max(0.35, 0.56 - 0.1 * (a - 2))
            }
            // Horizontal-only squeeze: the end cards (three slots out) read as
            // narrow panels closing the ring.
            function flowThin(a: real): real {
                return 1 - 0.2 * Math.max(0, Math.min(1, a - 2))
            }
            // Item-space matrix for a w x h card at `r`: shear (fading out as
            // flow fades in), flow scale and thinning, rotation about the
            // vertical centre line, perspective divide, all about the card
            // centre. Output z is flattened. Row-major, as Qt.matrix4x4 takes it.
            function cardMatrix(r: real, w: real, h: real): matrix4x4 {
                const f = flowAmount
                const k = Theme.cardSlant * (1 - f)
                const a = Math.abs(r)
                const s = 1 - f * (1 - flowScale(a))
                const q = 1 - f * (1 - flowThin(a))
                const th = f * Math.sign(r) * flowAngle(a)
                const c = Math.cos(th)
                const p = Math.sin(th) / flowDepth
                const cx = w / 2
                const cy = h / 2
                const a1 = s * q * (c + cx * p), b1 = s * q * k * (c + cx * p)
                const a2 = s * q * cy * p, b2 = s * (1 + q * cy * p * k)
                const a4 = s * q * p, b4 = s * q * p * k
                return Qt.matrix4x4(
                    a1, b1, 0, cx - a1 * cx - b1 * cy,
                    a2, b2, 0, cy - a2 * cx - b2 * cy,
                    0, 0, 1, 0,
                    a4, b4, 0, 1 - a4 * cx - b4 * cy)
            }

            property var grownCells: []
            function trackGrowth(c: Item, grown: bool): void {
                const i = grownCells.indexOf(c)
                if (grown && i < 0)
                    grownCells = grownCells.concat([c])
                else if (!grown && i >= 0)
                    grownCells = grownCells.filter(o => o !== c)
            }
            readonly property real growthA: {
                let g = 0
                for (const c of grownCells)
                    if (c.rel <= 0)
                        g += c.growth
                return g
            }
            readonly property real growthB: {
                let g = 0
                for (const c of grownCells)
                    if (c.rel > 0)
                        g += c.growth
                return g
            }
            // `r`: a cell's rel. Every rel shares the offset's fractional part,
            // so `u` (how far the focus has left A for B) is the same for all.
            function pushAt(r: real): real {
                if (pushAmount <= 0)
                    return 0
                const u = Math.ceil(r) - r
                const half = (growthA + growthB) / 2
                let push
                if (r <= -1)
                    push = -u * half - growthA / 2
                else if (r <= 0)
                    push = -u * half
                else if (r < 1)
                    push = (1 - u) * half
                else
                    push = (1 - u) * half + growthB / 2
                return push * pushAmount
            }

            model: controller.model

            // Open on the wallpaper that's already applied (appliedRow() returns
            // its row, or 0 when there's no saved state / it left the folder).
            // The model is fully populated before this view is built (the
            // Controller scans in its constructor), so the index is valid here;
            // _primed flips a frame later so the initial positioning snaps
            // instead of sweeping from index 0.
            Component.onCompleted: {
                if (count > 0)
                    currentIndex = controller.appliedRow()
                Qt.callLater(() => _primed = true)
            }

            delegate: Item {
                id: cell
                required property int index
                required property string name
                required property string kind
                required property string thumbnail
                required property string preview
                property bool selected: PathView.isCurrentItem
                // Overdraw mode only: the focused card pops at once, then
                // widens once it has stayed focused for expandDelay.
                property bool expanded: false

                // Cached instances that fell off the path must not paint, and
                // a faded-out seam card (see seamFade) must not take clicks.
                visible: PathView.onPath && opacity > 0

                // The layout slot stays at the idle width, so the loop's slot
                // step never changes with focus. The card *visual* grows beyond this box symmetrically
                // (the focused cell is always screen-centered in a loop, so the
                // growth can never run off-screen); neighbours are pushed aside
                // (`push`), or overdrawn via the z-lift when pushNeighbors is off.
                height: carousel.cardH
                width: carousel.idleW
                // Overdraw mode: a card still shrinking after losing focus
                // stays above the idle cards it overlaps, below the new focus.
                z: carousel.flowMode ? -dist
                    : carousel.pushNeighbors ? near
                    : selected ? 2 : cardVisual.width > carousel.idleW + 0.5 ? 1 : 0

                // Push mode sizing. Signed distance from the view centre in
                // slots, wrapped around the loop. `near` is 1 dead-centre, 0
                // one slot away: the card pops and widens as it glides in, in
                // lockstep with the neighbours it pushes.
                readonly property real rel: {
                    const n = carousel.count
                    if (n <= 0)
                        return 0
                    const r = ((index + carousel.offset) % n + n) % n
                    return r > n / 2 ? r - n : r
                }
                readonly property real near: Math.max(0, 1 - Math.abs(rel))
                readonly property real growth: near * (carousel.poppedW - carousel.idleW
                    + (expandedW - carousel.poppedW) * carousel.expandAmount)
                readonly property real push: carousel.pushAt(rel)
                onGrowthChanged: carousel.trackGrowth(cell, growth > 0)

                // Flow placement: translated from the evenly spaced slot to the
                // arc position; the rest of the look is in `cardMatrix`.
                readonly property real dist: Math.abs(rel)
                readonly property real flowShift:
                    (carousel.flowX(rel) - rel * carousel.step) * carousel.flowAmount
                readonly property matrix4x4 cardMatrix:
                    carousel.cardMatrix(rel, cardVisual.width, cardVisual.height)
                // In a compact strip (count <= slots) the card opposite the
                // focus sits on the path seam, which PathView puts at the left
                // end, so an even count shows one more card left than right.
                // It fades out at the seam instead: symmetric at rest, and a
                // card crossing the seam mid-glide fades rather than jumping
                // ends. Flow fades over a full slot so the arc's two sides
                // never swap in view; far flow cards fade out too.
                readonly property real seamFade: Math.max(0, Math.min(1,
                    (carousel.count / 2 - dist) * (2 - carousel.flowAmount)))
                opacity: seamFade
                    * (1 - carousel.flowAmount * (1 - Math.max(0, Math.min(1, 4 - dist))))

                // Flow glow behind the focused card, shaped and moved by the
                // same matrix. Only the cards straddling the centre load it,
                // and it stays off mid-sweep (expandAmount 0), so at most two
                // live effects, none while scrubbing. The white source sits
                // exactly under the opaque card, so only the halo shows.
                Loader {
                    x: cardVisual.x
                    y: cardVisual.y
                    width: cardVisual.width
                    height: cardVisual.height
                    readonly property real strength:
                        carousel.flowAmount * cell.near * cell.near * carousel.expandAmount
                    active: strength > 0
                    opacity: strength
                    transform: Matrix4x4 { matrix: cell.cardMatrix }
                    sourceComponent: Item {
                        Rectangle {
                            id: glowSource
                            anchors.fill: parent
                            color: Theme.frame
                            visible: false
                        }
                        MultiEffect {
                            anchors.fill: parent
                            source: glowSource
                            // Auto padding cuts the blur tail at ~2% alpha,
                            // a faint hard-edged box on dark wallpapers.
                            autoPaddingEnabled: false
                            paddingRect: Qt.rect(96, 96, 96, 96)
                            blurMax: 48
                            shadowEnabled: true
                            shadowColor: Theme.frame
                            shadowBlur: 1.0
                            shadowOpacity: 0.7
                        }
                    }
                }
                Component.onDestruction: carousel.trackGrowth(cell, false)

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

                // Expanded width follows the wallpaper's real aspect so the
                // whole image is visible, corners included. The thumbnail's
                // implicit size carries the source aspect (decode preserves
                // it); 16:9 until it's ready. Clamped so rare ultrawide art
                // stays on-screen (that case still crops at the sides).
                readonly property real imgAspect:
                    thumb.status === Image.Ready && thumb.implicitHeight > 0
                        ? thumb.implicitWidth / thumb.implicitHeight : 16 / 9
                readonly property real expandedW:
                    Math.min(Math.round(carousel.cardH * imgAspect),
                             Math.round(carousel.width * 0.64))
                // Flow width target: the wallpaper's aspect at flow height,
                // capped by flowWideMax; portrait art never goes below flowW.
                readonly property real flowWideW: Math.max(carousel.flowW,
                    Math.min(Math.round(carousel.flowH * imgAspect), carousel.flowWideMax))
                property real flowWiden: selected && expanded ? carousel.expandAmount : 0
                Behavior on flowWiden {
                    id: flowWidenBehavior
                    enabled: carousel.flowAmount > 0
                    NumberAnimation {
                        duration: flowWidenBehavior.targetValue > 0
                            ? carousel.flowWidenInDuration : carousel.flowWidenOutDuration
                        easing.type: Easing.OutCubic
                    }
                }
                readonly property real flowCardW:
                    carousel.flowW + (flowWideW - carousel.flowW) * flowWiden

                Rectangle {
                    id: cardVisual
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: -carousel.flowRise(cell.rel) * carousel.flowAmount
                    // Centred in the slot so growth overflows symmetrically. No
                    // viewport clamp needed: the focused cell is always
                    // screen-centered (loop) and expandedW is capped to a
                    // fraction of the screen, so the expansion can't run
                    // off-screen.
                    x: (cell.width - width) / 2 + cell.push + cell.flowShift
                    // Push mode binds size to position; overdraw mode leaves
                    // the idle size here and animates via the states below.
                    // Flow cards share one size (depth is in the matrix), bar the
                    // focused card's delayed widen (flowCardW).
                    readonly property real baseW:
                        carousel.pushNeighbors ? carousel.idleW + cell.growth : carousel.idleW
                    readonly property real baseH: carousel.pushNeighbors
                        ? carousel.idleH + (carousel.cardH - carousel.idleH) * cell.near
                        : carousel.idleH
                    width: baseW + (cell.flowCardW - baseW) * carousel.flowAmount
                    height: baseH + (carousel.flowH - baseH) * carousel.flowAmount
                    color: "#161616"
                    border.color: cell.selected ? Theme.frame
                        : Qt.rgba(1, 1, 1, 0.4 * carousel.flowAmount * Math.max(0, 1 - cell.dist / 4))
                    border.width: Theme.frameWidth
                    clip: true
                    antialiasing: true

                    // All cards lean as sheared parallelograms (noctalia-style
                    // slats), the focused one included, so the strip reads as
                    // one continuous set of slats. The shear is uniform, so
                    // edges stay parallel and spacing reads unchanged. Shear
                    // about the vertical centre so the card leans in place
                    // instead of walking. Flow swaps the shear for its 3D turn.
                    transform: Matrix4x4 { matrix: cell.cardMatrix }

                    states: [
                        State {
                            name: "popped"
                            when: !carousel.pushNeighbors && cell.selected && !cell.expanded
                            PropertyChanges {
                                cardVisual.width: carousel.poppedW
                                    + (cell.flowCardW - carousel.poppedW) * carousel.flowAmount
                                cardVisual.height: carousel.cardH
                                    + (carousel.flowH - carousel.cardH) * carousel.flowAmount
                            }
                        },
                        State {
                            name: "expanded"
                            when: !carousel.pushNeighbors && cell.selected && cell.expanded
                            PropertyChanges {
                                cardVisual.width: cell.expandedW
                                    + (cell.flowCardW - cell.expandedW) * carousel.flowAmount
                                cardVisual.height: carousel.cardH
                                    + (carousel.flowH - carousel.cardH) * carousel.flowAmount
                            }
                        }
                    ]
                    // The overlay states stay live in flow (their sizes lerp to
                    // the flow size), so their transitions are off whenever flow
                    // is in play: an animation there would target a size the
                    // flowAmount morph has already moved past, then jump.
                    readonly property bool sizeTransitions: carousel.flowAmount <= 0
                    transitions: [
                        // Fast, smooth pop the moment focus lands.
                        Transition {
                            to: "popped"
                            enabled: cardVisual.sizeTransitions
                            NumberAnimation { properties: "width,height"; duration: 150; easing.type: Easing.OutCubic }
                        },
                        // Slower, deliberate landscape widen.
                        Transition {
                            to: "expanded"
                            enabled: cardVisual.sizeTransitions
                            NumberAnimation { properties: "width,height"; duration: 300; easing.type: Easing.OutCubic }
                        },
                        // An expanded card shrinks back gradually when focus
                        // moves on; a sweep's popped cards settle fast.
                        Transition {
                            from: "expanded"
                            to: ""
                            enabled: cardVisual.sizeTransitions
                            NumberAnimation { properties: "width,height"; duration: 420; easing.type: Easing.InOutQuad }
                        },
                        Transition {
                            to: ""
                            enabled: cardVisual.sizeTransitions
                            NumberAnimation { properties: "width,height"; duration: 180; easing.type: Easing.OutCubic }
                        }
                    ]

                    Image {
                        id: thumb
                        anchors.fill: parent
                        anchors.margins: 2
                        source: cell.thumbnail
                        visible: cell.thumbnail !== "" && !cell.previewing
                        asynchronous: true
                        cache: true
                        // Always Crop: the image stays scaled to the card
                        // height, so the widen *reveals* more of it instead of
                        // re-fitting (a Fit switch mid-expand visibly rescales
                        // the image — looks like a reload). The expanded card
                        // matches the source aspect, so at rest Crop shows the
                        // whole image anyway; only the rare clamped ultrawide
                        // still crops. Decode by card height (the constraining
                        // axis) so it's sharp without over-decoding.
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
                        font.family: Theme.fontFamily
                        font.pixelSize: 24
                    }
                    // Flow depth cue: cards darken as they recede.
                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 2
                        color: "black"
                        opacity: carousel.flowAmount * Math.min(0.85, 0.22 * cell.dist)
                        visible: opacity > 0
                    }

                    // Inside the card so hit-testing follows the shear transform
                    // (an outside sibling would keep the unsheared rectangle).
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        // A single click focuses the card (never hover);
                        // double-click applies and exits.
                        onClicked: {
                            carousel.glideTo(cell.index)
                            if (win.searching)
                                win.exitSearchKeep()
                        }
                        onDoubleClicked: { carousel.focusIndex(cell.index); win.applyAndExit() }
                    }
                }
            }
        }

        // ---- Honeycomb: the full-screen hex grid that replaces the strip in
        // the `honeycomb` layout. Lazy: it exists only while that layout is
        // on or still fading out. Opens on the strip's focused card; the
        // callLater lets the carousel's own launch focus land first.
        Loader {
            id: hiveLoader
            anchors.fill: parent
            // Bar above, color strip below: one reserve on both edges, sized
            // for the taller of the two so the grid stays centred.
            readonly property real edgeReserve:
                win.edgeMargin + Math.max(topBar.height, colorBar.height) + 16
            anchors.topMargin: edgeReserve
            anchors.bottomMargin: edgeReserve
            active: win.honeycomb || win.hiveAmount > 0
            opacity: win.hiveAmount
            sourceComponent: Honeycomb {
                model: controller.ringModel
                onPreviewRequested: (index) => controller.ensurePreview(index)
                onCellClicked: if (win.searching) win.exitSearchKeep()
                onCellActivated: win.applyAndExit()
            }
            onLoaded: Qt.callLater(() => {
                if (win.hive)
                    win.hive.focusIndex(carousel.currentIndex, false)
            })
        }

        // ---- Color filter strip: a framed bar of mini sheared swatch-cards
        // below the carousel, mirroring the floating bar above it. On-demand
        // chrome: hidden until `c` opens color mode, kept while a filter is
        // active (it is the filter's indicator), gone when cleared. The
        // swatches reuse the wallcard language — same shear, same white
        // selection border — because they *are* miniature wallpaper cards:
        // the one deliberate color exception in the greyscale chrome.
        Rectangle {
            id: colorBar
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: carousel.bottom
            readonly property real bandMargin: 24 + (carousel.flowOverhang - 24) * carousel.flowAmount
            anchors.topMargin: bandMargin
                + (parent.height - carousel.y - carousel.height - win.edgeMargin - height
                   - bandMargin) * win.hiveAmount
            width: swatchRow.implicitWidth + 28
            height: swatchRow.implicitHeight + 20
            color: Theme.bg
            border.color: Theme.frame
            border.width: Theme.frameWidth
            visible: win.colorMode || controller.colorFilter !== "all"

            // Swallow clicks so they don't fall through to dismissArea.
            MouseArea { anchors.fill: parent }

            Row {
                id: swatchRow
                anchors.centerIn: parent
                spacing: 8

                Repeater {
                    model: win.colorEntries
                    delegate: Rectangle {
                        id: swatch
                        required property var modelData
                        width: 26
                        height: 40
                        color: modelData.hex
                        border.color: modelData.name === controller.colorFilter
                            ? Theme.frame : "transparent"
                        border.width: Theme.frameWidth
                        antialiasing: true

                        // Same shear as the wallcards (about the vertical
                        // centre, so the swatch leans in place).
                        readonly property real slant: Theme.cardSlant
                        transform: Matrix4x4 {
                            matrix: Qt.matrix4x4(
                                1, swatch.slant, 0, -swatch.slant * swatch.height / 2,
                                0, 1, 0, 0,
                                0, 0, 1, 0,
                                0, 0, 0, 1)
                        }

                        // The "all" swatch clears: typography, not an icon.
                        Text {
                            anchors.centerIn: parent
                            visible: swatch.modelData.name === "all"
                            text: "×"
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: 14
                        }

                        // Inside the swatch so hit-testing follows the shear.
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: controller.setColorFilter(swatch.modelData.name)
                        }
                    }
                }
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
