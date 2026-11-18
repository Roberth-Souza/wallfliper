"""Controller bridging QML to the Python core.

Exposes a filtered model plus slots QML calls on user actions. All wallpaper
logic stays in `core/`; this is the thin seam between the QML view and Python.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import (
    Property,
    QCoreApplication,
    QFile,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    QSize,
    QSocketNotifier,
    QSortFilterProxyModel,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication

from core.backends import (
    BackendError,
    MissingDependencyError,
    get_backend,
    transitions,
)
from core.backends.base import ImageTransition, WallpaperBackend
from core.colors import PALETTE, PALETTE_NAMES, ColorLoader, ColorLookup
from core.firstframe import first_frame, prune_first_frames
from core.integrations import notify_color_tools
from core.library import WallpaperEntry, scan
from core.portal import FolderChooser, portal_available
from core.previews import PreviewLoader
from core.silhouette import SilhouetteLoader, cached_field, prune_fields
from core.silhouette import available as silhouette_available
from core.state import (
    Config,
    State,
    WallpaperKind,
    cache_dir,
    load_config,
    load_state,
    save_config,
    save_state,
)
from core.thumbnails import ThumbnailLoader

from . import shaders
from .model import KIND_ROLE, NAME_ROLE, PATH_ROLE, RingModel, WallpaperModel

# Fit-within box for cached card thumbnails. The carousel supersamples each card
# (decodes at ~2x its on-screen height) for crispness, so the cache needs enough
# pixels to feed that: a 1920px box (1920x1080 for 16:9, i.e. native for a 1080p
# source — no upscaling). The on-screen Image still caps its own decode via
# sourceSize, so this sets the disk-cache ceiling, not per-card RAM.
_THUMB_SIZE = QSize(1920, 1920)

_CARD_LAYOUTS = ("push", "overlay", "flow", "honeycomb")

# What `m` flips config.transition between.
_TRANSITION_MODES = ("random", "silhouette")
# The shader `silhouette` degrades to when the new wallpaper has no subject (or
# none has been extracted yet).
_SILHOUETTE_FALLBACK = "colonies"
# Focus has to rest this long before silhouettes are queued around it, so
# scrubbing through cards does not reorder the work on every step.
_SILHOUETTE_SETTLE_MS = 250
# A see-through shader switch (no still of the old wallpaper) applies the new
# one only once the surface is opaque, then keeps covering this long while that
# lands: a swww repaint, or mpvpaper's cold start. Running short is harmless
# for a video — swww is already showing the same first frame underneath.
_LATE_APPLY_IMAGE_MS = 300
_LATE_APPLY_VIDEO_MS = 700
# The frame an outgoing video wallpaper is frozen on, so a shader switch away
# from it has an old picture to draw. No suffix: the backend picks the format.
# Only the switch in flight uses it, and it is deleted when that one lands.
_FROZEN_FRAME = "frozen-frame"

# shaderTransitionRequested's arguments: old URL, new URL, shader URL, field
# URL, duration ms, linger ms.
_ShaderPlan = tuple[str, str, str, str, int, int]

# Qt hands filterAcceptsRow a transient or persistent index; accept the union
# the base declares so type-checkers don't flag a narrowed override.
_Index = QModelIndex | QPersistentModelIndex


class _WallpaperFilterProxy(QSortFilterProxyModel):
    """AND a name substring filter with a kind filter and a color filter.

    The built-in fixed-string filter only matches a single role, so
    filterAcceptsRow applies all three predicates. An empty kind set means
    "all kinds"; an empty color string means "all colors". Color membership
    comes from the injected lookup (core.colors.ColorLoader) — a plain dict
    read, no I/O; a row not classified yet is hidden and streams in when its
    classification lands (the Controller throttles the re-filtering).
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._kinds: frozenset[str] = frozenset()
        self._color = ""
        self._colors_of: ColorLookup = lambda _path: None

    def set_text(self, text: str) -> None:
        text = text.lower()
        if text != self._text:
            self._text = text
            self.invalidateFilter()

    def set_kinds(self, kinds: frozenset[str]) -> None:
        if kinds != self._kinds:
            self._kinds = kinds
            self.invalidateFilter()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.invalidateFilter()

    def set_color_lookup(self, lookup: ColorLookup) -> None:
        self._colors_of = lookup

    def filterAcceptsRow(self, source_row: int, source_parent: _Index) -> bool:
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if self._kinds and model.data(index, KIND_ROLE) not in self._kinds:
            return False
        if self._color:
            colors = self._colors_of(model.data(index, PATH_ROLE))
            if not colors or self._color not in colors:
                return False
        if self._text:
            name = model.data(index, NAME_ROLE) or ""
            if self._text not in name.lower():
                return False
        return True


class _WarmSignals(QObject):
    """Carries a warmer's result back to the Controller's (main) thread.

    QRunnable is not a QObject, so the signal lives here; a queued connection
    hops the result off the pool thread onto the thread that owns the warm sets.
    """

    finished = Signal(str, bool)  # path key, whether the still is now cached


class _FirstFrameWarmer(QRunnable):
    """Pre-extract a video's first frame off the UI thread when it's selected.

    The seamless video transition needs that still before swww can animate it.
    The apply path only takes the seamless route when it is already cached
    (otherwise it hard-cuts rather than block the GUI on ffmpeg), so warming on
    selection is what makes the nice transition show up. `finished` reports
    whether the still is now cached, so a failed warm is retried on re-select
    instead of being marked done forever.
    """

    def __init__(self, path: Path, signals: _WarmSignals) -> None:
        super().__init__()
        self._path = path
        self._signals = signals

    def run(self) -> None:  # executed on a pool thread
        ok = False
        try:
            ok = first_frame(self._path) is not None
        except Exception:  # warming is best-effort; never disturb the app
            ok = False
        self._signals.finished.emit(str(self._path), ok)


class _FreezeSignals(QObject):
    finished = Signal(str)  # the outgoing video's saved frame, "" if there is none


class _VideoFreezer(QRunnable):
    """Freeze the outgoing video wallpaper and save its frame, off the UI thread.

    Getting that frame takes anything from a tenth of a second to most of
    one, depending on the video.
    """

    def __init__(self, backend: WallpaperBackend, dest: Path, signals: _FreezeSignals) -> None:
        super().__init__()
        self._backend = backend
        self._dest = dest
        self._signals = signals

    def run(self) -> None:  # executed on a pool thread
        frame: Path | None = None
        try:
            frame = self._backend.freeze_video(self._dest)
        finally:
            # Always reported: the switch is waiting on this and runs
            # see-through without a frame.
            self._signals.finished.emit(str(frame) if frame is not None else "")


class _CachePruner(QRunnable):
    """Collect orphaned cache files off the UI thread (core/cachegc.py)."""

    def __init__(
        self, thumbs: ThumbnailLoader, previews: PreviewLoader, library: list[WallpaperEntry]
    ) -> None:
        super().__init__()
        self._thumbs = thumbs
        self._previews = previews
        self._library = library

    def run(self) -> None:  # executed on a pool thread
        with suppress(OSError):  # cache cleanup is best-effort
            self._thumbs.prune(self._paths(self._thumbs.supports))
            self._previews.prune(self._paths(self._previews.supports))
            prune_first_frames(self._paths(lambda kind: kind == "video"))
            # A video's silhouette is its first frame's.
            frames = (
                first_frame(video, cached_only=True)
                for video in self._paths(lambda kind: kind == "video")
            )
            prune_fields(
                self._paths(lambda kind: kind == "image")
                + [frame for frame in frames if frame is not None]
            )

    def _paths(self, accepts: Callable[[WallpaperKind], bool]) -> list[Path]:
        return [entry.path for entry in self._library if accepts(entry.kind)]


class Controller(QObject):
    statusChanged = Signal()
    wallpaperDirChanged = Signal()
    folderPickerClosed = Signal()
    folderManualRequested = Signal()
    kindFilterChanged = Signal()
    colorFilterChanged = Signal()
    cardLayoutChanged = Signal()
    transitionModeChanged = Signal()
    # A second launch (the hotkey) asked the running instance to toggle.
    toggleRequested = Signal()
    # Ask Main.qml to paint the switch itself on a layer-shell surface, because
    # the configured animation is a shader swww cannot do (see ui/shaders.py).
    # Args: old wallpaper URL, new wallpaper URL, baked shader URL, field
    # texture URL ("" when the shader takes none), duration ms.
    shaderTransitionRequested = Signal(str, str, str, str, int, int)
    # The video applied under the transition surface is playing: the surface
    # may stop painting its still and let it show through.
    shaderVideoLive = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder_chooser = FolderChooser(self)
        self._folder_chooser.picked.connect(self._on_folder_picked)
        self._folder_chooser.cancelled.connect(self.folderPickerClosed)
        self._folder_chooser.unavailable.connect(self.folderManualRequested)
        self._loader = ThumbnailLoader(_THUMB_SIZE, self)
        self._previews = PreviewLoader(self)
        self._model = WallpaperModel(self._loader, self._previews, self)
        self._proxy = _WallpaperFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._ring = RingModel(self._proxy, self)
        # Exclusive kind filter: "all" | "image" | "video". One mode at a time;
        # each keybind sets its mode idempotently (no per-kind toggling).
        self._kind_filter = "all"
        # Exclusive color filter, same shape as kind: "all" or a palette
        # bucket. Classification is lazy — swept once per reload, and only
        # when the color strip first opens (ensureColorIndex), so launches
        # that never touch it do no color work.
        self._color_filter = "all"
        self._color_swept = False
        self._color_loader = ColorLoader(self)
        self._proxy.set_color_lookup(self._color_loader.colors_for)
        self._color_loader.ready.connect(self._on_colors_ready)
        # While classifications stream in, re-filter at most every interval
        # instead of once per wallpaper.
        self._color_refilter = QTimer(self)
        self._color_refilter.setSingleShot(True)
        self._color_refilter.setInterval(100)
        self._color_refilter.timeout.connect(self._proxy.invalidateFilter)

        self._backend = get_backend()
        self._config: Config = load_config()
        self._status = ""
        # One-at-a-time, low-priority warming of selected videos' first frames
        # so the seamless transition finds them cached at apply time.
        self._warm_pool = QThreadPool(self)
        self._warm_pool.setMaxThreadCount(1)
        self._warmed: set[str] = set()   # first frame confirmed cached
        self._warming: set[str] = set()  # extraction currently in flight
        self._warm_signals = _WarmSignals(self)
        self._warm_signals.finished.connect(self._on_warm_finished)
        self._gc_pool = QThreadPool(self)
        self._gc_pool.setMaxThreadCount(1)
        # Wallpaper waiting on a shader-painted switch: set when the surface is
        # asked for, applied once it covers the screen, cleared when it is done.
        self._pending_shader: WallpaperEntry | None = None
        # Applied while a shader switch was playing; runs when that one lands.
        self._queued_apply: WallpaperEntry | None = None
        self._pending_hold = 0.0
        # A shader switch away from a video waits here while that video's
        # frame is taken; the plan is launched when the freezer reports back.
        self._awaiting_freeze: _ShaderPlan | None = None
        self._frozen_frame: Path | None = None
        self._freeze_pool = QThreadPool(self)
        self._freeze_pool.setMaxThreadCount(1)
        self._freeze_signals = _FreezeSignals(self)
        self._freeze_signals.finished.connect(self._on_freeze_finished)
        # Read end of the pipe the backend reports a live video on, and its
        # notifier; both exist only while a switch to a video is in flight.
        self._live_fd = -1
        self._live_notifier: QSocketNotifier | None = None
        # Silhouette extraction for the `silhouette` transition: only while that
        # mode is on, nearest to the focused card first.
        self._silhouettes = SilhouetteLoader(self)
        self._focus_row = -1
        self._silhouette_settle = QTimer(self)
        self._silhouette_settle.setSingleShot(True)
        self._silhouette_settle.setInterval(_SILHOUETTE_SETTLE_MS)
        self._silhouette_settle.timeout.connect(self._queue_silhouettes)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._silhouettes.stop)
        self.reload()

    # --- properties exposed to QML --------------------------------------

    @Property(QObject, constant=True)
    def model(self) -> QSortFilterProxyModel:
        return self._proxy

    @Property(QObject, constant=True)
    def ringModel(self) -> RingModel:
        """The filtered model repeated end to end, for the endless honeycomb."""
        return self._ring

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=wallpaperDirChanged)
    def wallpaperDir(self) -> str:
        return self._config.wallpaper_dir or ""

    @Property(str, notify=kindFilterChanged)
    def kindFilter(self) -> str:
        """Active kind filter: "all", "image", or "video"."""
        return self._kind_filter

    @Property(str, notify=colorFilterChanged)
    def colorFilter(self) -> str:
        """Active color filter: "all" or a palette bucket name."""
        return self._color_filter

    @Property(str, notify=cardLayoutChanged)
    def cardLayout(self) -> str:
        """Card layout: "push", "overlay", "flow" or "honeycomb" (unknown reads as push)."""
        layout = self._config.card_layout
        return layout if layout in _CARD_LAYOUTS else "push"

    @Property(str, notify=transitionModeChanged)
    def transitionMode(self) -> str:
        """What the configured switch animation will actually do, for the bar.

        Normally config.transition itself; "silhouette" reads as its fallback
        when outlines cannot be extracted on this machine, so the label never
        promises an animation that is not going to play.
        """
        name = self._config.transition
        if not (transitions.is_known(name) or shaders.is_shader(name)):
            return "random"
        if name == "silhouette" and not silhouette_available():
            return _SILHOUETTE_FALLBACK if shaders.is_shader(_SILHOUETTE_FALLBACK) else "random"
        return name

    @Property("QVariantList", constant=True)
    def colorPalette(self) -> list[dict]:
        """Fixed palette for the QML swatch strip: [{name, hex}, …] in order."""
        return [{"name": name, "hex": hex_} for name, hex_ in PALETTE]

    # --- slots called from QML ------------------------------------------

    @Slot(str)
    def setFilter(self, text: str) -> None:
        self._proxy.set_text(text)

    @Slot(str)
    def setKindFilter(self, kind: str) -> None:
        """Show only `kind` ("image"/"video"), or "all". Idempotent: setting the
        mode that's already active is a no-op (re-pressing the key does nothing).
        """
        if kind not in ("all", "image", "video") or kind == self._kind_filter:
            return
        self._kind_filter = kind
        self._proxy.set_kinds(frozenset() if kind == "all" else frozenset({kind}))
        self.kindFilterChanged.emit()

    @Slot(str)
    def setColorFilter(self, color: str) -> None:
        """Show only wallpapers matching `color` (palette bucket), or "all".

        Exclusive and idempotent, mirroring setKindFilter.
        """
        if color == self._color_filter:
            return
        if color != "all" and color not in PALETTE_NAMES:
            return
        self._color_filter = color
        self._proxy.set_color("" if color == "all" else color)
        self.colorFilterChanged.emit()

    @Slot()
    def ensureColorIndex(self) -> None:
        """Kick color classification for the library, once per reload.

        QML calls this when the color strip opens. Cached files (colors.json
        hit) cost a stat each; the rest classify on the bounded pool and the
        grid streams in via the throttled re-filter.
        """
        if self._color_swept:
            return
        self._color_swept = True
        for entry in self._model.entries():
            try:
                thumb = self._loader.cache_path(entry)
            except OSError:
                thumb = None
            self._color_loader.request(entry, thumb)

    def _on_colors_ready(self, _path: str, _colors: list) -> None:
        if self._color_filter != "all" and not self._color_refilter.isActive():
            self._color_refilter.start()

    @Slot(result=int)
    def appliedRow(self) -> int:
        """Proxy row of the currently-applied wallpaper, or 0 if none/missing.

        Lets the carousel open centred on what's already on the desktop instead
        of the first card. Falls back to row 0 when there is no saved state or
        the file is no longer in the library (e.g. the folder changed).
        """
        state = load_state()
        if not state.path:
            return 0
        source_row = self._model.row_for_path(state.path)
        if source_row < 0:
            return 0
        proxy = self._proxy.mapFromSource(self._model.index(source_row))
        return proxy.row() if proxy.isValid() else 0

    @Slot(int)
    def ensurePreview(self, proxy_row: int) -> None:
        """Generate the preview for a cell once it's selected (QML calls this)."""
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        self._model.request_preview(source)
        self._warm_first_frame(source)

    def _warm_first_frame(self, source: QModelIndex) -> None:
        """Extract the selected video's first frame so apply is a cache hit."""
        entry = self._model.entry_at(source)
        if entry is None or entry.kind != "video":
            return
        key = str(entry.path)
        if key in self._warmed or key in self._warming:
            return
        self._warming.add(key)
        self._warm_pool.start(_FirstFrameWarmer(entry.path, self._warm_signals))

    @Slot(str, bool)
    def _on_warm_finished(self, key: str, ok: bool) -> None:
        """Record a warmed first frame; drop a failed one so re-select retries it."""
        self._warming.discard(key)
        if ok:
            self._warmed.add(key)
            self._queue_silhouettes()  # the frame is what gets segmented

    @Slot(int, result=bool)
    def apply(self, proxy_row: int) -> bool:
        """Apply the wallpaper at `proxy_row`.

        Returns True when the switch is being painted by a shader surface and is
        therefore still in flight: the caller must keep the process alive until
        the surface reports back, or the wallpaper is never actually set.
        """
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            return False
        if self._pending_shader is not None:
            # A switch in flight is never cut short: the same wallpaper again
            # is a no-op, another one waits for it (the latest wins).
            same = entry.path == self._pending_shader.path
            self._queued_apply = None if same else entry
            return True
        return self._start_apply(entry)

    @Slot(result=bool)
    def switchPending(self) -> bool:
        """True while a shader switch is in flight, surface or not."""
        return self._pending_shader is not None

    def _start_apply(self, entry: WallpaperEntry) -> bool:
        # One draw per apply: the shader path and the swww fallback must be
        # talking about the same animation.
        name = self._pick_transition(entry)
        previous = load_state()
        plan = self._shader_plan(entry, name, previous)
        if plan is not None:
            # The surface paints the old wallpaper first and calls back (see
            # shaderSurfaceReady); the real switch happens behind it, so the
            # apply itself is deferred, not skipped.
            self._pending_shader = entry
            # A playing video is not a file to draw from, but it can be frozen
            # and its frame saved. Only for another video, which then plays as
            # it is revealed: an image gains nothing from it, and see-through
            # keeps the old video moving under it instead. Not when it is the
            # wallpaper being applied either: that apply is a no-op, which
            # would leave it frozen.
            if (
                plan[0] == ""
                and entry.kind == "video"
                and previous.kind == "video"
                and previous.path != str(entry.path)
            ):
                self._awaiting_freeze = plan
                self._freeze_pool.start(
                    _VideoFreezer(
                        self._backend, cache_dir() / _FROZEN_FRAME, self._freeze_signals
                    )
                )
            else:
                self._launch_shader(plan)
            return True
        self._apply_entry(entry, self._swww_transition(name))
        return False

    def _on_freeze_finished(self, frame: str) -> None:
        plan, self._awaiting_freeze = self._awaiting_freeze, None
        if plan is None:
            return
        if frame:
            self._frozen_frame = Path(frame)
            plan = (QUrl.fromLocalFile(frame).toString(), *plan[1:])
        # Without a frame the old URL stays empty and the surface runs
        # see-through over the still-playing video.
        self._launch_shader(plan)

    def _launch_shader(self, plan: _ShaderPlan) -> None:
        # How long the surface still covers the screen after the apply.
        self._pending_hold = (plan[5] if plan[0] == "" else plan[4]) / 1000
        self.shaderTransitionRequested.emit(*plan)

    def _apply_entry(self, entry: WallpaperEntry, transition: ImageTransition) -> None:
        """Hand a wallpaper to the backend and record it, reporting failures."""
        try:
            if entry.kind == "video":
                self._backend.set_video(entry.path, transition)
            else:
                self._backend.set_image(entry.path, transition)
            save_state(entry.path, entry.kind)
            notify_color_tools(entry.path, entry.kind, self._config.color_hook)
            self._set_status(f"✓ applied {entry.name}")
        except MissingDependencyError as exc:
            self._set_status(f"⚠ {exc}")
        except BackendError as exc:
            self._set_status(f"⚠ failed to apply: {exc}")

    def _shader_plan(
        self, entry: WallpaperEntry, name: str, previous: State
    ) -> _ShaderPlan | None:
        """Arguments for a shader-painted switch, or None to use swww.

        Requires a baked shader, a still of the new wallpaper (a video's is its
        first frame, see `_still_of`) and for `silhouette` that still's field
        texture; anything missing falls back to swww. The old end is optional:
        without a still of it the old URL is empty and the surface runs
        see-through, painting over the live wallpaper and applying the new one
        at the end. A previous *video* gets its still afterwards, by freezing
        it, when the new wallpaper is a video too (see `_start_apply`).
        """
        shader_url = shaders.url_for(name)
        still = self._still_of(entry)
        if shader_url is None or still is None:
            return None
        field_url = ""
        if name == "silhouette":
            field = cached_field(still)
            if field is None:
                return None
            field_url = QUrl.fromLocalFile(str(field)).toString()
        old_url = ""
        if previous.path and previous.kind == "image" and Path(previous.path).is_file():
            old_url = QUrl.fromLocalFile(previous.path).toString()
        return (
            old_url,
            QUrl.fromLocalFile(str(still)).toString(),
            shader_url,
            field_url,
            max(
                1,
                int(self._config.transition_duration * shaders.duration_scale(name) * 1000),
            ),
            _LATE_APPLY_VIDEO_MS if entry.kind == "video" else _LATE_APPLY_IMAGE_MS,
        )

    @Slot()
    def shaderSurfaceReady(self) -> None:
        """The surface hides the wallpaper: swap what's underneath.

        Applying behind the surface rather than after it is what removes the
        seam — by the time the surface is torn down the compositor already holds
        the new wallpaper, so there is no frame where neither is on screen.
        """
        entry = self._pending_shader
        if entry is None:
            return
        if entry.kind != "video":
            self._apply_entry(entry, ImageTransition(type="none"))
            return
        # A video comes up behind the surface on the very frame the shader is
        # drawing and starts playing there at once; the backend reports it on
        # the pipe, and the surface then lets it show through (shaderVideoLive).
        # `hold` only matters to a backend that cannot report: the video then
        # starts as the surface goes away.
        ready_fd = self._watch_live_video()
        try:
            self._apply_entry(
                entry,
                ImageTransition(type="none", hold=self._pending_hold, ready_fd=ready_fd),
            )
        finally:
            # The driver holds its own copy; with this one closed, end-of-file
            # on the read end means nobody is going to report.
            os.close(ready_fd)

    def _watch_live_video(self) -> int:
        """Open the pipe a live video is reported on; returns its write end."""
        self._drop_live_watch()
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self._live_fd = read_fd
        self._live_notifier = QSocketNotifier(read_fd, QSocketNotifier.Type.Read, self)
        self._live_notifier.activated.connect(self._on_live_readable)
        return write_fd

    def _on_live_readable(self) -> None:
        try:
            reported = os.read(self._live_fd, 1)
        except BlockingIOError:
            return  # spurious wake-up
        except OSError:
            reported = b""
        self._drop_live_watch()
        if reported and self._pending_shader is not None:
            self.shaderVideoLive.emit()

    def _drop_frozen_frame(self) -> None:
        """Delete the outgoing video's still: uncompressed, and used once."""
        frame, self._frozen_frame = self._frozen_frame, None
        if frame is not None:
            with suppress(OSError):
                frame.unlink(missing_ok=True)

    def _drop_live_watch(self) -> None:
        if self._live_notifier is not None:
            self._live_notifier.setEnabled(False)
            self._live_notifier.deleteLater()
            self._live_notifier = None
        if self._live_fd >= 0:
            with suppress(OSError):
                os.close(self._live_fd)
            self._live_fd = -1

    @Slot()
    def shaderTransitionDone(self) -> None:
        self._pending_shader = None
        self._drop_live_watch()
        self._drop_frozen_frame()
        self._run_queued_apply()

    def _run_queued_apply(self) -> None:
        entry, self._queued_apply = self._queued_apply, None
        if entry is not None:
            self._start_apply(entry)

    @Slot()
    def shaderTransitionFailed(self) -> None:
        """The surface could not draw (image decode failed): switch plainly."""
        entry = self._pending_shader
        self._pending_shader = None
        self._drop_live_watch()
        self._drop_frozen_frame()
        if entry is not None:
            self._apply_entry(entry, self._swww_transition("random"))
        self._run_queued_apply()

    @Slot(int)
    def deleteWallpaper(self, proxy_row: int) -> None:
        """Move the wallpaper file to the trash and drop its card.

        No confirmation (Shift+D is a deliberate two-hand chord), so the trash
        is the undo. Never falls back to a permanent delete when the file
        can't be trashed. Its cached thumbnail/preview/first frame are left to
        the next reload's cache GC, so restoring the file before then keeps them.
        """
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            return
        if not QFile(str(entry.path)).moveToTrash():
            self._set_status(f"⚠ couldn't move {entry.name} to trash")
            return
        self._model.remove_row(source.row())
        key = str(entry.path)
        self._warmed.discard(key)
        self._warming.discard(key)
        self._set_status(f"✗ trashed {entry.name}")

    @Slot(result=bool)
    def folderPortalAvailable(self) -> bool:
        """Whether a FileChooser portal is reachable (see core/portal.py).

        QML checks this before hiding the overlay: with no portal it goes
        straight to the manual path-entry fallback instead of unmapping the
        window for a chooser that never appears.
        """
        return portal_available()

    @Slot()
    def pickFolder(self) -> None:
        """Open the user's portal file chooser to pick the wallpaper folder.

        Goes straight to xdg-desktop-portal (see core/portal.py) so every user
        gets their own configured chooser, instead of relying on Qt's
        FolderDialog routing. QML hides the overlay before calling this and
        restores it on `folderPickerClosed`. If no portal answers (or the call
        fails), the chooser emits `unavailable`, surfaced here as
        `folderManualRequested` so QML can fall back to manual entry.
        """
        self._folder_chooser.open()

    def _on_folder_picked(self, path: str) -> None:
        self.setFolder(path)
        self.folderPickerClosed.emit()

    @Slot(str, result=str)
    def setFolderFromText(self, text: str) -> str:
        """Set the wallpaper folder from a hand-typed path (portal fallback).

        Expands `~` and env vars, accepts a `file://` URI too, and validates the
        target is a directory. Returns an empty string on success or a short,
        lowercase error for the entry dialog to show inline.
        """
        text = text.strip()
        if not text:
            return "enter a path"
        if text.startswith("file://"):
            local = _to_local_path(text)
        else:
            local = os.path.expanduser(os.path.expandvars(text))
        if not local:
            return "invalid path"
        path = Path(local)
        if not path.is_dir():
            return "not a folder"
        self.setFolder(str(path))
        return ""

    @Slot(str)
    def setCardLayout(self, layout: str) -> None:
        if layout not in _CARD_LAYOUTS or layout == self.cardLayout:
            return
        self._config.card_layout = layout
        save_config(self._config)
        self.cardLayoutChanged.emit()

    @Slot()
    def cycleTransitionMode(self) -> None:
        """`m`: flip the switch animation between the two modes.

        A hand-configured animation that is neither of them moves to the first
        mode rather than being cycled back to — pressing `m` picks a mode.
        """
        current = self._config.transition
        index = _TRANSITION_MODES.index(current) if current in _TRANSITION_MODES else -1
        self._config.transition = _TRANSITION_MODES[(index + 1) % len(_TRANSITION_MODES)]
        save_config(self._config)
        self.transitionModeChanged.emit()
        self._queue_silhouettes()

    @Slot(int)
    def noteFocus(self, proxy_row: int) -> None:
        """QML reports the focused card, so silhouettes are extracted near it."""
        self._focus_row = proxy_row
        if self._config.transition == "silhouette":
            self._silhouette_settle.start()

    def _queue_silhouettes(self) -> None:
        """(Re)order silhouette extraction around the focused card.

        The whole filtered library is queued, nearest card first: a wallpaper
        is usually applied within a second or two of being focused, sooner than
        its own silhouette could be ready, so the ones around the focus are
        extracted ahead of the user reaching them. Leaving the mode empties the
        queue, which lets the worker exit.
        """
        if self._config.transition != "silhouette" or not shaders.is_shader("silhouette"):
            self._silhouettes.want([])
            return
        count = self._proxy.rowCount()
        if count == 0:
            self._silhouettes.want([])
            return
        focus = min(max(self._focus_row, 0), count - 1)
        wanted: list[Path] = []
        # The strip is a loop, so distance wraps: focus, +1, -1, +2, -2, …
        for step in range(count):
            offset = (step + 1) // 2 * (1 if step % 2 else -1)
            row = (focus + offset) % count
            entry = self._model.entry_at(self._proxy.mapToSource(self._proxy.index(row, 0)))
            still = self._still_of(entry) if entry is not None else None
            if still is not None:
                wanted.append(still)
        self._silhouettes.want(wanted)

    @staticmethod
    def _still_of(entry: WallpaperEntry) -> Path | None:
        """The picture a shader switch *to* `entry` draws: the image itself, or
        a video's first frame once it is cached (warmed on selection)."""
        if entry.kind == "image":
            return entry.path
        return first_frame(entry.path, cached_only=True)

    @Slot(str)
    def setFolder(self, folder: str) -> None:
        path = _to_local_path(folder)
        if path:
            self._config.wallpaper_dir = path
            save_config(self._config)
            self.wallpaperDirChanged.emit()
            self.reload()

    # --- internals ------------------------------------------------------

    def reload(self) -> None:
        directory = self._config.wallpaper_path
        entries = scan(directory) if directory else []
        self._model.set_entries(entries)
        self._gc_pool.start(
            _CachePruner(self._loader, self._previews, entries)
        )
        self._warmed.clear()
        self._warming.clear()
        self._queue_silhouettes()
        # New library, new sweep — but only re-kick it right away if a color
        # filter is live (otherwise unclassified entries would stay hidden).
        self._color_swept = False
        if self._color_filter != "all":
            self.ensureColorIndex()
        # No count/folder chrome in the front; clear any stale apply message.
        self._set_status("")

    def _set_status(self, text: str) -> None:
        self._status = text
        self.statusChanged.emit()

    def _pick_transition(self, entry: WallpaperEntry) -> str:
        """Resolve the configured animation to one concrete name, per apply.

        "random" draws from the swww presets *and* the shader transitions, so
        both kinds share a single pool. A typo in config.json degrades to that
        pool instead of handing swww a flag value it rejects, which would fail
        the whole apply.

        `silhouette` needs the new wallpaper's subject: without a cached field
        (no subject, not extracted yet, or no onnxruntime) it is left out of the
        random pool, and when configured it becomes its fallback shader.
        """
        name = self._config.transition
        if not (transitions.is_known(name) or shaders.is_shader(name)):
            name = "random"
        still = self._still_of(entry)
        has_field = still is not None and cached_field(still) is not None
        if name == "random":
            pool = transitions.RANDOM_POOL + tuple(
                shader for shader in shaders.names() if shader != "silhouette" or has_field
            )
            return random.choice(pool) if pool else "random"
        if name == "silhouette" and not has_field:
            return _SILHOUETTE_FALLBACK if shaders.is_shader(_SILHOUETTE_FALLBACK) else "random"
        return name

    def _swww_transition(self, name: str) -> ImageTransition:
        """The transition to hand swww, with fps matched to the display.

        A shader name means nothing to swww, so a wallpaper the shader path
        can't take (a video, or no previous still to blend from) falls back to
        the random preset pool rather than to no animation at all.
        """
        return ImageTransition(
            type="random" if shaders.is_shader(name) else name,
            duration=self._config.transition_duration,
            fps=self._transition_fps(),
        )

    @staticmethod
    def _transition_fps() -> int:
        """Frame rate for the swww switch animation, matched to the displays.

        swww renders the transition's frames in software for its duration
        (~1s) only — there is no steady-state cost. It animates every output
        at one rate, so the fastest refresh wins: a lower rate visibly stutters
        on the faster output, while frames a slower output can't show only cost
        CPU for that second. Floored at 60 so an odd/missing value can't make
        the animation choppy.
        """
        return max([60, *(round(s.refreshRate()) for s in QGuiApplication.screens())])


def _to_local_path(folder: str) -> str | None:
    """Accept a plain path or a file:// URL (QML FolderDialog gives a URL)."""
    if folder.startswith("file://"):
        return unquote(urlparse(folder).path)
    return folder or None
