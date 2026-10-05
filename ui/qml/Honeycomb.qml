pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Shapes

// Honeycomb layout: the library as a full-screen grid of flat-top hexagons,
// `rows` per column, filled column by column and scrolled sideways. Odd
// columns sit half a row lower so the cells interlock, and every hexagon is
// inset by `gap` so a thin seam of desktop shows between neighbours.
// Selection is the white outline only: no pop, no widen.
//
// Endless like the carousel: `model` is the Controller's RingModel, the
// library repeated end to end, so the grid can always scroll on and the last
// wallpaper runs straight into the first. Grid rows are "slots"; slot s
// shows wallpaper s % n. Before focus reaches either end of the ring it
// jumps by `period` slots to an identical spot (same wallpaper, row and
// column parity) and shifts the view by the same amount, so the jump never
// shows. A library that fits on screen stays a single, centred copy with
// wrapping navigation instead: there is nothing to scroll.
Item {
    id: hive

    required property var model
    readonly property int currentIndex:
        n > 0 && grid.currentIndex >= 0 ? grid.currentIndex % n : -1

    readonly property int rows: 4
    readonly property real gap: 4

    signal previewRequested(int index)
    signal cellClicked()
    signal cellActivated()

    // Lattice: row step `cellH`, column step 1.5·R, where R is the lattice
    // circumradius. Each hexagon is drawn with the radius shrunk by gap/√3,
    // which pulls every edge in by gap/2: a uniform `gap` between neighbours.
    readonly property real cellH: Math.floor(height / (rows + 0.5))
    readonly property real latticeR: cellH / Math.sqrt(3)
    readonly property real hexR: latticeR - gap / Math.sqrt(3)
    readonly property real hexW: 2 * latticeR
    // Supersampled decode, same rule as the carousel cards.
    readonly property int decodeH: Math.round(2 * cellH)

    readonly property int n: model.sourceCount
    // Endless as soon as one copy of the library is wider than the screen:
    // if it scrolls at all, it scrolls forever. A library just past that
    // size may show the same wallpaper in both edge slivers.
    readonly property real copyW: n > 0 ? (Math.ceil(n / rows) - 1) * grid.cellWidth + hexW : 0
    readonly property bool wantEndless: copyW + 2 * gap > width
    readonly property bool endless: model.laps > 1
    // Smallest slot shift that lands on the same wallpaper, row and column
    // parity: a multiple of n and of 2·rows. The ring holds three periods,
    // so focus can always be moved back into the middle one.
    function gcd(a: int, b: int): int {
        return b === 0 ? a : gcd(b, a % b)
    }
    readonly property int period: n > 0 ? n / gcd(n, 2 * rows) * 2 * rows : 0
    Binding {
        target: hive.model
        property: "laps"
        value: hive.wantEndless ? 3 * hive.period / hive.n : 1
    }

    readonly property int columns: Math.ceil(grid.count / rows)
    // A column's hexagons reach a quarter hex past its cell into the next.
    readonly property real stripW: columns > 0 ? (columns - 1) * grid.cellWidth + hexW : 0
    // A single copy narrower than the screen is centred; anything wider is
    // full-bleed, cut by the screen edges like the carousel band.
    readonly property real sideMargin: stripW + 2 * gap < width ? (width - stripW) / 2 : gap

    // Wallpaper focused last, and where its column sat on screen: a ring
    // reset (filter, delete, lap change) rebuilds every slot, and the focus
    // is put back on the same wallpaper at the same screen position.
    property int lastIndex: 0
    property real focusScreenX: (width - hexW) / 2
    property bool placed: false

    function slotLeft(s: int): real {
        return grid.originX + Math.floor(s / rows) * grid.cellWidth
    }

    function focusSlot(s: int, animate: bool): void {
        const total = grid.count
        if (total <= 0 || n <= 0)
            return
        if (endless) {
            const shift = s < period ? period : s >= total - period ? -period : 0
            if (shift !== 0) {
                scroll.stop()
                grid.contentX += shift / rows * grid.cellWidth
                s += shift
            }
        } else
            s = (s % total + total) % total
        grid.currentIndex = s
        lastIndex = s % n
        revealSlot(s, animate)
    }

    // Focus wallpaper `i` (a model row) on the copy nearest the current one.
    function focusIndex(i: int, animate: bool): void {
        lastIndex = Math.max(0, i)
        if (n <= 0)
            return
        if (!placed) {
            Qt.callLater(restoreFocus)
            return
        }
        const cur = grid.currentIndex
        let delta = ((i - cur % n) % n + n) % n
        if (endless && delta > n / 2)
            delta -= n
        focusSlot(endless ? cur + delta : i, animate)
    }

    function moveRow(d: int): void {
        if (placed)
            focusSlot(grid.currentIndex + d, true)
    }

    function moveColumn(d: int): void {
        if (!placed)
            return
        const s = grid.currentIndex
        if (endless) {
            focusSlot(s + d * rows, true)
            return
        }
        // Single copy: past the last column wraps to the first, and back.
        // The last column may be partial: fall back to its last cell.
        const last = Math.floor((n - 1) / rows)
        let c = Math.floor(s / rows) + d
        if (c > last)
            c = 0
        else if (c < 0)
            c = last
        focusSlot(Math.min(c * rows + s % rows, n - 1), true)
    }

    function selectRandom(): void {
        if (n < 2 || !placed)
            return
        const i = Math.floor(Math.random() * (n - 1))
        focusIndex(i >= currentIndex ? i + 1 : i, true)
    }

    // Scroll only as far as needed to keep the focused column, plus one
    // neighbour column, on screen. Jumps longer than a screen (wrap, random)
    // snap: sweeping the whole strip by would just be motion blur.
    function revealSlot(s: int, animate: bool): void {
        const left = slotLeft(s)
        const pad = grid.cellWidth
        const minX = grid.originX - sideMargin
        const maxX = Math.max(minX, grid.originX + stripW + sideMargin - grid.width)
        let x = grid.contentX
        if (left - pad < x)
            x = left - pad
        else if (left + hexW + pad > x + grid.width)
            x = left + hexW + pad - grid.width
        x = Math.max(minX, Math.min(maxX, x))
        focusScreenX = left - x
        scroll.stop()
        if (!animate || Math.abs(x - grid.contentX) > grid.width)
            grid.contentX = x
        else {
            scroll.from = grid.contentX
            scroll.to = x
            scroll.restart()
        }
    }

    function restoreFocus(): void {
        if (n <= 0 || grid.count <= 0)
            return
        const i = Math.max(0, Math.min(lastIndex, n - 1))
        const s = endless ? period + i : i
        scroll.stop()
        grid.currentIndex = s
        lastIndex = i
        grid.contentX = slotLeft(s) - focusScreenX
        placed = true
        revealSlot(s, false)
    }

    Connections {
        target: hive.model
        function onModelAboutToBeReset() {
            hive.placed = false
            scroll.stop()
        }
        function onModelReset() {
            Qt.callLater(hive.restoreFocus)
        }
    }
    onWidthChanged: if (placed) Qt.callLater(() => revealSlot(grid.currentIndex, false))

    // One highlight at a time, owned by the last input used: the white
    // focus outline while the keyboard (or wheel) drives, the grey hover
    // outline once the mouse moves. Main hands it back on any key press.
    // Only real pointer motion counts: keyboard scrolling slides cells under
    // a resting cursor, but the cursor's scene position stays put.
    property bool pointerActive: false
    HoverHandler {
        property point last: Qt.point(NaN, NaN)
        onPointChanged: {
            const p = point.scenePosition
            if (!isNaN(last.x) && (p.x !== last.x || p.y !== last.y))
                hive.pointerActive = true
            last = p
        }
    }

    WheelHandler {
        onWheel: (event) => {
            hive.pointerActive = false
            if (event.angleDelta.y < 0 || event.angleDelta.x < 0)
                hive.moveColumn(1)
            else if (event.angleDelta.y > 0 || event.angleDelta.x > 0)
                hive.moveColumn(-1)
        }
    }

    // One hexagon filled with `texture` (cover-scaled, centred), or a flat
    // placeholder while it is null.
    component Hexagon: Shape {
        id: shape
        property Item texture: null
        property real radius: 0
        property bool outlined: false
        property bool hovered: false

        containsMode: Shape.FillContains
        preferredRendererType: Shape.CurveRenderer

        // fillItem maps the texture at its own pixel size from the shape
        // origin; scale it to cover the hexagon's box, centred.
        readonly property real texW: texture ? texture.implicitWidth : 1
        readonly property real texH: texture ? texture.implicitHeight : 1
        readonly property real cover: Math.max(width / texW, height / texH)
        readonly property real cx: width / 2
        readonly property real cy: height / 2
        readonly property real h: radius * Math.sqrt(3) / 2

        ShapePath {
            fillColor: "#161616"
            fillItem: shape.texture
            fillTransform: Qt.matrix4x4(
                shape.cover, 0, 0, (shape.width - shape.texW * shape.cover) / 2,
                0, shape.cover, 0, (shape.height - shape.texH * shape.cover) / 2,
                0, 0, 1, 0,
                0, 0, 0, 1)
            strokeColor: shape.hovered ? Theme.muted : Theme.frame
            strokeWidth: shape.outlined ? Theme.frameWidth : -1
            joinStyle: ShapePath.MiterJoin

            startX: shape.cx - shape.radius; startY: shape.cy
            PathLine { x: shape.cx - shape.radius / 2; y: shape.cy - shape.h }
            PathLine { x: shape.cx + shape.radius / 2; y: shape.cy - shape.h }
            PathLine { x: shape.cx + shape.radius; y: shape.cy }
            PathLine { x: shape.cx + shape.radius / 2; y: shape.cy + shape.h }
            PathLine { x: shape.cx - shape.radius / 2; y: shape.cy + shape.h }
            PathLine { x: shape.cx - shape.radius; y: shape.cy }
        }
    }

    GridView {
        id: grid
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        // Exactly `rows` cells fit per column; the extra half row holds the
        // lowered odd columns.
        height: hive.cellH * (hive.rows + 0.5)
        flow: GridView.FlowTopToBottom
        cellWidth: 1.5 * hive.latticeR
        cellHeight: hive.cellH
        leftMargin: hive.sideMargin
        rightMargin: hive.sideMargin + hive.hexW - cellWidth
        cacheBuffer: Math.round(2 * cellWidth)
        model: hive.model
        // Keyboard + wheel only, and scrolling is revealSlot's job: a
        // tracking view would fight it on every focus change.
        interactive: false
        highlightFollowsCurrentItem: false

        NumberAnimation {
            id: scroll
            target: grid
            property: "contentX"
            duration: 220
            easing.type: Easing.OutCubic
        }

        delegate: Item {
            id: cell
            required property int index
            required property string kind
            required property string thumbnail
            required property string preview
            readonly property bool selected: GridView.isCurrentItem
            readonly property bool previewing: selected && kind === "video" && preview !== ""

            width: hive.hexW
            height: hive.cellH
            // Passive mouse highlight: a grey outline that never moves focus.
            readonly property bool hovered: hive.pointerActive && pointer.containsMouse
            readonly property bool outlined: hive.pointerActive ? hovered : selected
            // The outline overhangs into the seam; keep it above neighbours.
            z: outlined ? 1 : 0
            transform: Translate {
                y: Math.floor(cell.index / hive.rows) % 2 ? hive.cellH / 2 : 0
            }

            onSelectedChanged: if (selected && kind === "video") hive.previewRequested(index % hive.n)
            Component.onCompleted: if (selected && kind === "video") hive.previewRequested(index % hive.n)

            // Texture source for the hexagon fill, never painted itself.
            Image {
                id: thumb
                opacity: 0
                source: cell.thumbnail
                asynchronous: true
                cache: true
                sourceSize.height: hive.decodeH
            }

            Hexagon {
                id: hex
                anchors.fill: parent
                texture: thumb.status === Image.Ready ? thumb : null
                radius: hive.hexR
                outlined: cell.outlined
                hovered: cell.hovered
            }

            // The clip gets its own hexagon on top instead of swapping the
            // base one's fillItem: a ShapePath that filled with the clip's
            // alpha texture keeps blending the opaque thumbnail as if its
            // alpha were 0 afterwards, so dark wallpapers turned see-through.
            // Loaded only while focused, so idle cells hold no decoder.
            Loader {
                anchors.fill: parent
                active: cell.previewing
                sourceComponent: Item {
                    AnimatedImage {
                        id: clip
                        opacity: 0
                        source: cell.preview
                        playing: true
                        cache: false
                        asynchronous: true
                    }
                    Hexagon {
                        anchors.fill: parent
                        visible: clip.status === Image.Ready
                        texture: clip
                        radius: hive.hexR
                        outlined: cell.outlined
                        hovered: cell.hovered
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: hex.texture === null
                text: cell.kind === "video" ? "▶" : "…"
                color: "#3a3a3a"
                font.family: Theme.fontFamily
                font.pixelSize: 24
            }

            // Hit-testing follows the hexagon, not the overlapping cell boxes.
            MouseArea {
                id: pointer
                anchors.fill: hex
                containmentMask: hex
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    hive.focusSlot(cell.index, true)
                    hive.cellClicked()
                }
                onDoubleClicked: {
                    hive.focusSlot(cell.index, false)
                    hive.cellActivated()
                }
            }
        }
    }
}
