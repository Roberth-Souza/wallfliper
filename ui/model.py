"""Qt model exposing the wallpaper library to the QML GridView.

Roles exposed to QML: name, path, kind, thumbnail, preview. Thumbnails load
lazily: when QML asks for the `thumbnail` role, the model returns the cached
JPEG path if ready, otherwise queues generation and returns "" until the
worker finishes, then emits dataChanged so the delegate's Image updates.

Previews (short looping clips) are loaded the same way but are *not* auto-
requested on data() — that would encode every visible video. Generation is
triggered explicitly via `request_preview` when a cell becomes selected.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from core.library import WallpaperEntry
from core.previews import PreviewLoader
from core.thumbnails import ThumbnailLoader

# Qt's model API hands index/parent as either a transient QModelIndex or a
# QPersistentModelIndex; our overrides must accept the same union the base
# declares, or type-checkers flag an incompatible override (LSP: a subclass
# may not narrow what a method accepts). Both types expose .isValid()/.row().
_Index = QModelIndex | QPersistentModelIndex

NAME_ROLE = Qt.ItemDataRole.UserRole + 1
PATH_ROLE = Qt.ItemDataRole.UserRole + 2
KIND_ROLE = Qt.ItemDataRole.UserRole + 3
THUMBNAIL_ROLE = Qt.ItemDataRole.UserRole + 4
PREVIEW_ROLE = Qt.ItemDataRole.UserRole + 5


class WallpaperModel(QAbstractListModel):
    def __init__(
        self,
        loader: ThumbnailLoader,
        preview_loader: PreviewLoader,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[WallpaperEntry] = []
        self._loader = loader
        self._previews = preview_loader
        self._requested: set[str] = set()
        self._preview_requested: set[str] = set()
        # path -> source row, so a worker's ready signal resolves its cell in O(1)
        # instead of scanning every entry (was O(n) per thumbnail, O(n²) total).
        self._row_by_path: dict[str, int] = {}
        # Resolved disk-cache URIs, so data() never re-stat()s a known thumbnail/
        # preview on the UI thread (GridView queries data() on every repaint).
        self._thumb_uri: dict[str, str] = {}
        self._preview_uri: dict[str, str] = {}
        self._preview_checked: set[str] = set()
        self._loader.ready.connect(self._on_thumbnail_ready)
        self._previews.ready.connect(self._on_preview_ready)

    def set_entries(self, entries: list[WallpaperEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self._row_by_path = {str(e.path): row for row, e in enumerate(entries)}
        self._requested.clear()
        self._preview_requested.clear()
        self._thumb_uri.clear()
        self._preview_uri.clear()
        self._preview_checked.clear()
        self.endResetModel()

    def entry_at(self, index: _Index) -> WallpaperEntry | None:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        return self._entries[index.row()]

    # --- QAbstractListModel ---------------------------------------------

    def roleNames(self) -> dict:
        return {
            NAME_ROLE: QByteArray(b"name"),
            PATH_ROLE: QByteArray(b"path"),
            KIND_ROLE: QByteArray(b"kind"),
            THUMBNAIL_ROLE: QByteArray(b"thumbnail"),
            PREVIEW_ROLE: QByteArray(b"preview"),
        }

    def rowCount(self, parent: _Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole):
        entry = self.entry_at(index)
        if entry is None:
            return None
        if role == NAME_ROLE:
            return entry.name
        if role == PATH_ROLE:
            return str(entry.path)
        if role == KIND_ROLE:
            return entry.kind
        if role == THUMBNAIL_ROLE:
            return self._thumbnail(entry)
        if role == PREVIEW_ROLE:
            return self._preview(entry)
        return None

    # --- thumbnails -----------------------------------------------------

    def _thumbnail(self, entry: WallpaperEntry) -> str:
        if not self._loader.supports(entry.kind):
            return ""
        key = str(entry.path)
        uri = self._thumb_uri.get(key)
        if uri is not None:
            return uri
        if key in self._requested:
            return ""  # generation in flight; wait for dataChanged, no stat
        # First sighting of this entry: one disk check, then memoize the result.
        cached = self._loader.cache_path(entry)
        if cached.exists():
            self._thumb_uri[key] = cached.as_uri()
            return self._thumb_uri[key]
        self._requested.add(key)
        self._loader.request(entry)
        return ""

    def _on_thumbnail_ready(self, path: str, _image) -> None:
        row = self._row_by_path.get(path)
        if row is None:
            return
        self._thumb_uri[path] = self._loader.cache_path(self._entries[row]).as_uri()
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [THUMBNAIL_ROLE])

    # --- previews -------------------------------------------------------

    def _preview(self, entry: WallpaperEntry) -> str:
        """Cached preview URI if ready, else "" (generation is not auto-fired)."""
        if not self._previews.supports(entry.kind):
            return ""
        key = str(entry.path)
        uri = self._preview_uri.get(key)
        if uri is not None:
            return uri
        if key in self._preview_checked:
            return ""  # disk already checked; wait for generation + dataChanged
        # One disk check per entry; later generation arrives via _on_preview_ready.
        self._preview_checked.add(key)
        cached = self._previews.cache_path(entry)
        if cached.exists():
            self._preview_uri[key] = cached.as_uri()
            return self._preview_uri[key]
        return ""

    def request_preview(self, index: QModelIndex) -> None:
        """Trigger preview generation for a cell (call when it becomes selected)."""
        entry = self.entry_at(index)
        if entry is None or not self._previews.supports(entry.kind):
            return
        key = str(entry.path)
        if key in self._preview_requested:
            return
        self._preview_requested.add(key)
        self._previews.request(entry)

    def _on_preview_ready(self, path: str, preview: str) -> None:
        row = self._row_by_path.get(path)
        if row is None:
            return
        self._preview_uri[path] = Path(preview).as_uri()
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [PREVIEW_ROLE])
