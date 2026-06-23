"""Controller bridging QML to the Python core.

Exposes a filtered model plus slots QML calls on user actions. All wallpaper
logic stays in `core/`; this is the thin seam between the QML view and Python.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, unquote

from PySide6.QtCore import (
    Property,
    QObject,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication

from core.backends import BackendError, MissingDependencyError, get_backend
from core.backends.base import ImageTransition
from core.integrations import notify_color_tools
from core.library import scan
from core.portal import FolderChooser
from core.previews import PreviewLoader
from core.state import Config, load_config, save_config, save_state
from core.thumbnails import ThumbnailLoader

from .model import WallpaperModel

_THUMB_SIZE = QSize(440, 248)


class Controller(QObject):
    statusChanged = Signal()
    wallpaperDirChanged = Signal()
    cornersChanged = Signal()
    backgroundOpacityChanged = Signal()
    folderPickerClosed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder_chooser = FolderChooser(self)
        self._folder_chooser.picked.connect(self._on_folder_picked)
        self._folder_chooser.cancelled.connect(self.folderPickerClosed)
        self._loader = ThumbnailLoader(_THUMB_SIZE, self)
        self._previews = PreviewLoader(self)
        self._model = WallpaperModel(self._loader, self._previews, self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterRole(self._role("name"))

        self._backend = get_backend()
        self._config: Config = load_config()
        self._status = ""
        self.reload()

    def _role(self, name: str) -> int:
        for role, rname in self._model.roleNames().items():
            if bytes(rname.data()) == name.encode():
                return role
        return Qt.ItemDataRole.DisplayRole

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

    @Property(str, notify=cornersChanged)
    def corners(self) -> str:
        return self._config.corners

    @Property(float, notify=backgroundOpacityChanged)
    def backgroundOpacity(self) -> float:
        return self._config.background_opacity

    # --- slots called from QML ------------------------------------------

    @Slot(str)
    def setFilter(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    @Slot(int)
    def ensurePreview(self, proxy_row: int) -> None:
        """Generate the preview for a cell once it's selected (QML calls this)."""
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        self._model.request_preview(source)

    @Slot(int)
    def apply(self, proxy_row: int) -> None:
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            return
        try:
            if entry.kind == "video":
                self._backend.set_video(entry.path)
            else:
                # Fixed random transition; fps follows the display refresh so
                # the switch animation is as smooth as the monitor can show
                # (a per-user transition picker may return later).
                self._backend.set_image(
                    entry.path, ImageTransition(fps=self._transition_fps())
                )
            save_state(entry.path, entry.kind)
            notify_color_tools(entry.path, entry.kind, self._config.color_hook)
            self._set_status(f"✓ applied {entry.name}")
        except MissingDependencyError as exc:
            self._set_status(f"⚠ {exc}")
        except BackendError as exc:
            self._set_status(f"⚠ failed to apply: {exc}")

    @Slot()
    def pickFolder(self) -> None:
        """Open the user's portal file chooser to pick the wallpaper folder.

        Goes straight to xdg-desktop-portal (see core/portal.py) so every user
        gets their own configured chooser, instead of relying on Qt's
        FolderDialog routing. QML hides the overlay before calling this and
        restores it on `folderPickerClosed`.
        """
        self._folder_chooser.open()

    def _on_folder_picked(self, path: str) -> None:
        self.setFolder(path)
        self.folderPickerClosed.emit()

    @Slot(str)
    def setFolder(self, folder: str) -> None:
        path = _to_local_path(folder)
        if path:
            self._config.wallpaper_dir = path
            save_config(self._config)
            self.wallpaperDirChanged.emit()
            self.reload()

    @Slot(str)
    def setCorners(self, corners: str) -> None:
        if corners not in ("round", "sharp") or corners == self._config.corners:
            return
        self._config.corners = corners  # type: ignore[assignment]
        save_config(self._config)
        self.cornersChanged.emit()

    @Slot(float)
    def setBackgroundOpacity(self, opacity: float) -> None:
        opacity = max(0.0, min(1.0, opacity))
        if opacity == self._config.background_opacity:
            return
        self._config.background_opacity = opacity
        save_config(self._config)
        self.backgroundOpacityChanged.emit()

    # --- internals ------------------------------------------------------

    def reload(self) -> None:
        directory = self._config.wallpaper_path
        entries = scan(directory) if directory else []
        self._model.set_entries(entries)
        where = self._config.wallpaper_dir or "no folder set"
        self._set_status(f"{len(entries)} wallpapers · {where}")

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
