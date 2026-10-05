"""Asynchronous, disk-cached animated previews.

Hovering/selecting a wallpaper plays a short looping clip on its thumbnail.
Mirrors `thumbnails.py`: a bounded QThreadPool keeps work off the UI thread, a
per-kind strategy registry keeps the source pluggable, and results are cached
to disk keyed by abspath+mtime so repeat hovers are instant.

Local video previews are generated with ffmpeg as a small looping **animated
WebP** (the only truecolor animated format QMovie/QML `AnimatedImage` plays).
Generation is gated on selection by the caller, so we only ever encode clips
the user actually hovers.

The strategy registry is the seam for future sources: a Lively wallpaper, for
example, would register a strategy that simply copies/downloads the preview
asset it already ships instead of re-encoding anything.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import cachegc
from .library import WallpaperEntry
from .state import WallpaperKind, cache_dir

PreviewStrategy = Callable[[Path, Path], bool]

_PREVIEW_DIR = cache_dir() / "previews"
_MAX_WORKERS = 2  # encoding is heavier than thumbs; previews fire one at a time
# Bounds what other wallpaper folders keep; the current library is never evicted.
_CAP_BYTES = 100 * 1024 * 1024

# Preview clip shape: short, modest framerate — enough to convey motion while
# staying light to encode/decode (it's animated, so a big bump costs CPU per
# frame, not just disk). Sized by height: cards are portrait and crop-fill, so
# the card height is the axis that scales the clip (the flow card is ~0.56 of
# the screen height, ~605px at 1080p). Width-keyed 720px clips were only 405px
# tall and got upscaled ~1.5x there.
_START_SECONDS = "1"   # skip a possible black/fade-in opening frame
_DURATION_SECONDS = "3"
_PREVIEW_HEIGHT = 720
_PREVIEW_FILTER = f"fps=15,scale=-2:{_PREVIEW_HEIGHT}"


def _video_preview_strategy(src: Path, dest: Path) -> bool:
    """Encode a short looping animated WebP from a video via ffmpeg."""
    if not shutil.which("ffmpeg"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.webp")  # write atomically: never read a partial
    cmd = [
        "ffmpeg", "-y",
        "-ss", _START_SECONDS, "-t", _DURATION_SECONDS, "-i", str(src),
        "-an", "-vf", _PREVIEW_FILTER,
        "-loop", "0", "-c:v", "libwebp", "-q:v", "50",
        str(tmp),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=60,  # a stuck ffmpeg must not hold a worker forever
        )
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)
        return False
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def _cache_path(src: Path) -> Path:
    stat = src.stat()
    # Height is in the key so a resolution change re-encodes instead of serving a
    # stale clip (mirrors the thumbnail cache keying on its target size).
    key = f"{src.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|h{_PREVIEW_HEIGHT}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return _PREVIEW_DIR / f"{digest}.webp"


class _WorkerSignals(QObject):
    ready = Signal(str, str)  # source path, preview file path
    failed = Signal(str)  # source path


class _PreviewWorker(QRunnable):
    def __init__(
        self, entry: WallpaperEntry, strategy: PreviewStrategy, signals: _WorkerSignals
    ) -> None:
        super().__init__()
        self._entry = entry
        self._strategy = strategy
        self._signals = signals

    def run(self) -> None:  # executed on a pool thread
        src = self._entry.path
        try:
            dest = _cache_path(src)
            if not dest.exists() and not self._strategy(src, dest):
                self._signals.failed.emit(str(src))
                return
            self._signals.ready.emit(str(src), str(dest))
        except Exception:  # noqa: BLE001 — a single bad file must not kill the pool
            self._signals.failed.emit(str(src))


class PreviewLoader(QObject):
    """Requests animated previews asynchronously; emits `ready` on the UI thread."""

    ready = Signal(str, str)  # source path, preview file path
    failed = Signal(str)  # source path

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(_MAX_WORKERS)
        self._strategies: dict[WallpaperKind, PreviewStrategy] = {}
        self._inflight: set[str] = set()
        self.register_strategy("video", _video_preview_strategy)

    def register_strategy(self, kind: WallpaperKind, strategy: PreviewStrategy) -> None:
        """Register the preview generator for a wallpaper kind/source."""
        self._strategies[kind] = strategy

    def supports(self, kind: WallpaperKind) -> bool:
        return kind in self._strategies

    def cache_path(self, entry: WallpaperEntry) -> Path:
        """Disk path where this entry's preview is (or will be) cached."""
        return _cache_path(entry.path)

    def prune(self, library: list[Path]) -> None:
        """Drop orphaned previews (blocking — call from a pool thread)."""
        cachegc.prune(_PREVIEW_DIR, library, _cache_path, _CAP_BYTES)

    def request(self, entry: WallpaperEntry) -> None:
        """Queue a preview for `entry` (no-op if unsupported or in-flight)."""
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
        self._pool.start(_PreviewWorker(entry, strategy, signals))

    def _on_ready(self, path: str, preview: str) -> None:
        self._inflight.discard(path)
        self.ready.emit(path, preview)

    def _on_failed(self, path: str) -> None:
        self._inflight.discard(path)
        self.failed.emit(path)
