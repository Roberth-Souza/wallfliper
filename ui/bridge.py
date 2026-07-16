"""Controller bridging QML to the Python core.

Exposes a filtered model plus slots QML calls on user actions. All wallpaper
logic stays in `core/`; this is the thin seam between the QML view and Python.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from PySide6.QtCore import (
    Property,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication

from core.backends import BackendError, MissingDependencyError, get_backend
from core.backends.base import ImageTransition
from core.colors import PALETTE, PALETTE_NAMES, ColorLoader, ColorLookup
from core.firstframe import first_frame
from core.integrations import notify_color_tools
from core.library import scan
from core.portal import FolderChooser, portal_available
from core.previews import PreviewLoader
from core.state import Config, load_config, load_state, save_config, save_state
from core.thumbnails import ThumbnailLoader

from .model import KIND_ROLE, NAME_ROLE, PATH_ROLE, WallpaperModel

# Fit-within box for cached card thumbnails. The carousel supersamples each card
# (decodes at ~2x its on-screen height) for crispness, so the cache needs enough
# pixels to feed that: a 1920px box (1920x1080 for 16:9, i.e. native for a 1080p
# source — no upscaling). The on-screen Image still caps its own decode via
# sourceSize, so this sets the disk-cache ceiling, not per-card RAM.
_THUMB_SIZE = QSize(1920, 1920)

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


class Controller(QObject):
    statusChanged = Signal()
    wallpaperDirChanged = Signal()
    folderPickerClosed = Signal()
    folderManualRequested = Signal()
    kindFilterChanged = Signal()
    colorFilterChanged = Signal()

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
        self.reload()

    # --- properties exposed to QML --------------------------------------

    @Property(QObject, constant=True)
    def model(self) -> QSortFilterProxyModel:
        return self._proxy

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

    @Slot(int)
    def apply(self, proxy_row: int) -> None:
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            return
        try:
            # Fixed random transition; fps follows the display refresh so the
            # switch animation is as smooth as the monitor can show (a per-user
            # transition picker may return later). Video reuses it for the
            # seamless lead-in: swww animates to the clip's first frame, then
            # mpvpaper takes over.
            transition = ImageTransition(fps=self._transition_fps())
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

    @Slot(int)
    def deleteWallpaper(self, proxy_row: int) -> None:
        """Permanently delete the wallpaper file and drop its card. No undo:
        Shift+D is a deliberate two-hand chord, so no confirmation dialog.
        Cached thumbnail/preview are removed too so they don't linger orphaned.
        """
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            return
        # Cache paths key on the source file's stat (mtime+size), so they must
        # be resolved while the file still exists — after unlink they raise.
        try:
            stale = (self._loader.cache_path(entry), self._previews.cache_path(entry))
        except OSError:
            stale = ()  # source already gone; nothing to key the caches with
        try:
            entry.path.unlink(missing_ok=True)
        except OSError as exc:
            self._set_status(f"⚠ delete failed: {exc}")
            return
        self._model.remove_row(source.row())
        key = str(entry.path)
        self._warmed.discard(key)
        self._warming.discard(key)
        for cached in stale:
            try:
                cached.unlink(missing_ok=True)
            except OSError:
                pass  # cache cleanup is best-effort
        self._set_status(f"✗ deleted {entry.name}")

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
        self._warmed.clear()
        self._warming.clear()
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

    @staticmethod
    def _transition_fps() -> int:
        """Frame rate for the swww switch animation, matched to the display.

        swww renders the transition's frames in software for its duration
        (~1s) only — there is no steady-state cost — so we pace it to the
        monitor's refresh rate: smoother frames the display can't show are
        wasted. Qt knows the rate without any compositor-specific call; fall
        back to 60 if no screen is reported. Floored so an odd/low value can't
        make the animation choppy.
        """
        screen = QGuiApplication.primaryScreen()
        rate = round(screen.refreshRate()) if screen else 0
        return max(rate, 60)


def _to_local_path(folder: str) -> str | None:
    """Accept a plain path or a file:// URL (QML FolderDialog gives a URL)."""
    if folder.startswith("file://"):
        return unquote(urlparse(folder).path)
    return folder or None
