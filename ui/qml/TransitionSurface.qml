import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// Full-screen wlr-layer-shell surface that paints the wallpaper switch itself,
// with a GLSL shader, for effects swww has no concept of (see shaders/).
//
// It lives on the *bottom* layer: strictly above swww-daemon and mpvpaper (both
// on background — verified with `hyprctl layers`), strictly below every normal
// window and the selector overlay, so the animation plays behind whatever the
// user has open, exactly where a wallpaper belongs.
//
// Lifetime is a single switch: Main.qml creates it, it animates once, and it is
// destroyed. Nothing of it is held while idle — the two full-screen textures are
// the most expensive thing in the app and must not outlive the animation.
//
// The sequencing that makes it seamless: the surface first paints a copy of the
// *old* wallpaper (an invisible change, it's what is already on screen), then
// signals `covered`, which is when the real wallpaper is applied underneath it.
// So when the surface is destroyed at the end, the compositor is already showing
// the new wallpaper — nothing flashes in the gap.
//
// With no `oldSource` (the old wallpaper is a playing video, or its file is
// gone) the surface is *see-through* instead: the old texture is empty, so the
// same shaders leave whatever is on screen visible and paint only the new
// picture over it. Nothing may change underneath while that shows through, so
// `covered` then fires at the END of the animation, once the surface is opaque,
// and the surface lingers for `lingerMs` while the real switch happens behind it.
//
// A video does not have to wait for any of this to end. Once the new wallpaper
// is applied underneath and its video is actually playing there, `goLive()`
// stops painting the new picture from its still and leaves those pixels to the
// video, so what the shader reveals is already moving.
Window {
    id: surface

    property url oldSource
    property url newSource
    property url shaderUrl
    // Field texture of the new wallpaper's subject (core/silhouette.py), for
    // the shaders that draw from it. Empty for the ones that don't.
    property url maskSource
    property int durationMs: 1000
    property int lingerMs: 300
    // Hex cells across the screen's height. More cells = finer honeycomb.
    property real cells: 9

    // The surface hides the wallpaper: it is safe to swap what's underneath.
    signal covered()
    signal finished()
    // Either image failed to decode; the caller falls back to a plain swww switch.
    signal failed()

    visible: true
    // Sized by the compositor (all four edges anchored), like the overlay.
    // Decode size is fixed up front: tracking the surface size would re-decode
    // both images when the first configure arrives. Largest output, so no
    // output upscales.
    readonly property size decodeSize: {
        let w = 0, h = 0
        for (const s of Qt.application.screens) {
            w = Math.max(w, s.width)
            h = Math.max(h, s.height)
        }
        return Qt.size(Math.round(w * 1.5), Math.round(h * 1.5))
    }
    // Transparent, not black: the surface maps as soon as it is created, but the
    // wallpapers behind the shader decode asynchronously, so for that whole
    // decode there is nothing to draw. An opaque clear colour would be painted
    // over swww in the meantime — a black blink before the animation. Clearing
    // to nothing lets swww's current wallpaper, which is exactly the frame the
    // animation starts from, show through until the first shaded frame lands.
    color: "transparent"

    LayerShell.Window.scope: "wallfliper-transition"
    // Same output as the overlay, where the user is looking.
    LayerShell.Window.wantsToBeOnActiveScreen: true
    LayerShell.Window.layer: LayerShell.Window.LayerBottom
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1

    readonly property bool hasMask: maskSource.toString() !== ""
    readonly property bool seeThrough: oldSource.toString() === ""
    readonly property bool ready: (seeThrough || oldImage.status === Image.Ready)
        && newImage.status === Image.Ready
        && (!hasMask || maskImage.status === Image.Ready)
    property bool started: false
    property bool live: false

    onReadyChanged: if (ready && !started) settle.start()

    // The still and the video's opening frame are the same picture, scaled by
    // two different renderers; the short fade keeps that from reading as a pop.
    function goLive() {
        if (surface.live)
            return
        surface.live = true
        liveFade.start()
    }

    NumberAnimation {
        id: liveFade
        target: effect
        property: "liveNew"
        from: 0.0
        to: 1.0
        duration: 120
        // Past the reveal the surface only lingers to hide the switch behind
        // it; with the video showing through there is nothing left to hide.
        onFinished: {
            if (linger.running) {
                linger.stop()
                surface.finished()
            }
        }
    }

    // Give the compositor a couple of frames to actually show our copy of the
    // old wallpaper before anything changes underneath it. Qt's frame-swap
    // signals fire on the render thread, so a short timer is the safe way to
    // wait for "we are on screen" from QML.
    Timer {
        id: settle
        interval: 60
        onTriggered: {
            surface.started = true
            if (!surface.seeThrough)
                surface.covered()
            reveal.start()
        }
    }

    Timer {
        id: linger
        interval: surface.lingerMs
        onTriggered: surface.finished()
    }

    // Nothing here is allowed to hang: the caller may have hidden its window and
    // be waiting on us to quit. If the images have not decoded in time, give up
    // and let the caller fall back to a plain swww switch.
    Timer {
        id: watchdog
        interval: 3000
        running: true
        onTriggered: if (!surface.started) surface.failed()
    }

    // sourceSize caps the decode: a 6000px wallpaper would otherwise sit in RAM
    // at full resolution for the whole animation. The 1.5x headroom over the
    // screen leaves room for PreserveAspectCrop to crop without upscaling.
    Image {
        id: oldImage
        anchors.fill: parent
        source: surface.oldSource
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        sourceSize: surface.decodeSize
        onStatusChanged: {
            if (status === Image.Ready) oldTexture.scheduleUpdate()
            else if (status === Image.Error) surface.failed()
        }
    }

    Image {
        id: newImage
        anchors.fill: parent
        source: surface.newSource
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        sourceSize: surface.decodeSize
        onStatusChanged: {
            if (status === Image.Ready) newTexture.scheduleUpdate()
            else if (status === Image.Error) surface.failed()
        }
    }

    // Cropped exactly like the wallpaper it was extracted from, so the field
    // lines up with the picture on any screen shape. smooth, because the
    // shader reconstructs a one-pixel line from this much smaller texture.
    Image {
        id: maskImage
        anchors.fill: parent
        source: surface.maskSource
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        smooth: true
        onStatusChanged: {
            if (status === Image.Ready) maskTexture.scheduleUpdate()
            else if (status === Image.Error) surface.failed()
        }
    }

    // live: false renders each texture once instead of every frame — the
    // wallpapers are still images, re-rendering them 60 times a second would be
    // two extra full-screen passes per frame for nothing.
    ShaderEffectSource {
        id: oldTexture
        sourceItem: oldImage
        hideSource: true
        live: false
    }

    ShaderEffectSource {
        id: newTexture
        sourceItem: newImage
        hideSource: true
        live: false
    }

    ShaderEffectSource {
        id: maskTexture
        sourceItem: maskImage
        hideSource: true
        live: false
    }

    ShaderEffect {
        id: effect
        anchors.fill: parent
        fragmentShader: surface.shaderUrl
        // Names must match the uniforms in the .frag; Qt binds them by name.
        property variant srcOld: oldTexture
        property variant srcNew: newTexture
        property real progress: 0.0
        property real aspect: width / Math.max(height, 1)
        property real cells: surface.cells
        property variant srcMask: maskTexture
        // Screen pixels per field texel, under the same crop as maskImage.
        property real maskScale: maskImage.implicitWidth > 0
            ? Math.max(width / maskImage.implicitWidth, height / maskImage.implicitHeight) : 1
        property real pixelHeight: height
        property real seed: Math.random()
        property real liveNew: 0.0
    }

    NumberAnimation {
        id: reveal
        target: effect
        property: "progress"
        from: 0.0
        to: 1.0
        duration: surface.durationMs
        // Linear: the shader already eases each cell individually, and the
        // caller times the video hand-off against this exact curve.
        easing.type: Easing.Linear
        onFinished: {
            if (surface.seeThrough) {
                surface.covered()
                linger.start()
            } else {
                surface.finished()
            }
        }
    }
}
