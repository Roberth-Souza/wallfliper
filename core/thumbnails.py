"""Asynchronous, disk-cached thumbnail loader.

Design goals (see CLAUDE.md performance rules):
  - Never decode on the UI thread: workers run on a bounded QThreadPool.
  - Cache to disk keyed by abspath+mtime+size+target, so repeat launches are
    instant and folders are scanned-and-scaled at most once.
  - Pluggable per-kind strategy registry: images decode in-process via QImage
    (no external dependency). Registering video (ffmpeg) later is one call to
    `register_strategy` — no change to the loader, model, or UI.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

from .library import WallpaperEntry
from .state import WallpaperKind, cache_dir

# A strategy turns a source file into a thumbnail file on disk at `dest`,
# scaled to fit within `size`. Returns True on success.
ThumbStrategy = Callable[[Path, Path, QSize], bool]

_THUMB_DIR = cache_dir() / "thumbnails"
_DEFAULT_SIZE = QSize(360, 360)
_MAX_WORKERS = 4


def _image_strategy(src: Path, dest: Path, size: QSize) -> bool:
    """Decode an image and write a scaled JPEG thumbnail.

    Uses QImageReader so large sources are downscaled at decode time rather
    than loading full-resolution bitmaps into memory.
    """
    reader = QImageReader(str(src))
    reader.setAutoTransform(True)  # honour EXIF orientation
    original = reader.size()
    if original.isValid() and not original.isEmpty():
        scaled = original.scaled(size, _aspect_keep())
        reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    return image.save(str(dest), "JPEG", quality=85)


def _video_thumb_strategy(src: Path, dest: Path, size: QSize) -> bool:
    """Extract a single representative frame from a video as a JPEG thumbnail.

    Uses ffmpeg (`-ss 1` skips a possible black/fade-in opening frame). The
    frame is scaled at decode time to the thumbnail width so we never write a
    full-resolution still. Silently no-ops if ffmpeg is missing — the card
    falls back to the placeholder, exactly as before.
    """
    if not shutil.which("ffmpeg"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")  # write atomically: never read a partial
    cmd = [
        "ffmpeg", "-y",
        "-ss", "1", "-i", str(src), "-frames:v", "1",
        "-vf", f"scale={size.width()}:-2", "-update", "1",
        str(tmp),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=30,  # a stuck ffmpeg must not hold a worker forever
        )
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)
        return False
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def _aspect_keep():
    # Imported lazily to keep the module import light and avoid a top-level
    # Qt namespace dependency in signatures.
    from PySide6.QtCore import Qt

    return Qt.AspectRatioMode.KeepAspectRatio


class _WorkerSignals(QObject):
    ready = Signal(str, QImage)  # source path, decoded thumbnail image
    failed = Signal(str)  # source path


class _ThumbWorker(QRunnable):
    def __init__(
        self,
        entry: WallpaperEntry,
        strategy: ThumbStrategy,
        size: QSize,
        signals: _WorkerSignals,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._strategy = strategy
        self._size = size
        self._signals = signals

    def run(self) -> None:  # executed on a pool thread
        src = self._entry.path
        try:
            dest = _cache_path(src, self._size)
            if not dest.exists():
                if not self._strategy(src, dest, self._size):
                    self._signals.failed.emit(str(src))
                    return
            image = QImage(str(dest))
            if image.isNull():
                self._signals.failed.emit(str(src))
                return
            self._signals.ready.emit(str(src), image)
        except Exception:  # a single bad file must not kill the pool
            self._signals.failed.emit(str(src))


def _cache_path(src: Path, size: QSize) -> Path:
    stat = src.stat()
    key = f"{src.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{size.width()}x{size.height()}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return _THUMB_DIR / f"{digest}.jpg"


class ThumbnailLoader(QObject):
    """Requests thumbnails asynchronously; emits `ready` on the UI thread.

    The QImage delivered by `ready` should be converted to a QPixmap by the
    receiver (QPixmap is main-thread only) and cached in QPixmapCache.
    """

    ready = Signal(str, QImage)  # source path, thumbnail image
    failed = Signal(str)  # source path

    def __init__(self, size: QSize = _DEFAULT_SIZE, parent: QObject | None = None):
        super().__init__(parent)
        self._size = size
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(_MAX_WORKERS)
        self._strategies: dict[WallpaperKind, ThumbStrategy] = {}
        self._inflight: set[str] = set()
        self.register_strategy("image", _image_strategy)
        self.register_strategy("video", _video_thumb_strategy)

    def register_strategy(self, kind: WallpaperKind, strategy: ThumbStrategy) -> None:
        """Register the thumbnail generator for a wallpaper kind.

        Adding video support later is just:
            loader.register_strategy("video", ffmpeg_frame_strategy)
        """
        self._strategies[kind] = strategy

    def supports(self, kind: WallpaperKind) -> bool:
        return kind in self._strategies

    def cache_path(self, entry: WallpaperEntry) -> Path:
        """Disk path where this entry's thumbnail is (or will be) cached."""
        return _cache_path(entry.path, self._size)

    def request(self, entry: WallpaperEntry) -> None:
        """Queue a thumbnail for `entry` (no-op if unsupported or in-flight)."""
        strategy = self._strategies.get(entry.kind)
        if strategy is None:
            return
        key = str(entry.path)
        if key in self._inflight:
            return
        self._inflight.add(key)

        signals = _WorkerSignals()
        signals.ready.connect(self._on_ready)
        signals.failed.connect(self._on_failed)
        worker = _ThumbWorker(entry, strategy, self._size, signals)
        self._pool.start(worker)

    def _on_ready(self, path: str, image: QImage) -> None:
        self._inflight.discard(path)
        self.ready.emit(path, image)

    def _on_failed(self, path: str) -> None:
        self._inflight.discard(path)
        self.failed.emit(path)
