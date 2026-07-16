"""Dominant-color classification behind the color filter.

Wallpapers are bucketed into a fixed palette of named colors (Wallhaven-style)
rather than per-library extracted swatches: the strip stays predictable and
matching is a set lookup. A wallpaper belongs to its dominant bucket plus any
bucket covering at least _MIN_SHARE of its pixels, so a blue-and-orange shot
matches both filters.

Classification never decodes full-res: it reads the already-cached thumbnail
when present, else a small scaled decode of the source (images in-process via
QImageReader; videos as one tiny ffmpeg frame piped to stdout — silently
skipped without ffmpeg, per the graceful-degradation rule). Work runs on a
bounded QThreadPool, mirroring the thumbnail/preview loaders. Results land in
a JSON index (~/.cache/wallfliper/colors.json) keyed by abspath+mtime, so each
file is classified once; the saved index is pruned to the paths seen this
session, keeping it bounded by the library size.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader

from .library import WallpaperEntry
from .state import cache_dir

# (name, swatch hex) in strip order. The hexes are display colors for the
# swatch cards, not classification targets — matching goes through _bucket_of.
PALETTE: tuple[tuple[str, str], ...] = (
    ("black", "#0a0a0a"),
    ("grey", "#7a7a7a"),
    ("white", "#e8e8e8"),
    ("red", "#b03434"),
    ("orange", "#c26a2e"),
    ("brown", "#7a5230"),
    ("yellow", "#c2a83a"),
    ("green", "#4e8f4e"),
    ("cyan", "#3d9391"),
    ("blue", "#3d6db0"),
    ("purple", "#7a4fa8"),
    ("pink", "#b0518f"),
)

PALETTE_NAMES: frozenset[str] = frozenset(name for name, _ in PALETTE)

# Lookup signature the filter proxy uses: path -> buckets, or None while the
# file hasn't been classified yet.
ColorLookup = Callable[[str], tuple[str, ...] | None]

_INDEX_PATH = cache_dir() / "colors.json"
_SAMPLE_EDGE = 64
_MIN_SHARE = 0.15
_MAX_WORKERS = 2
_SAVE_DEBOUNCE_MS = 2000
_FFMPEG_TIMEOUT_S = 30


def _bucket_of(hue: float, saturation: float, value: float) -> str:
    """Map one HSV pixel (hue in degrees) to a palette bucket.

    Near-black / near-grey / near-white are carved out by value/saturation
    before hue is considered; brown is dark, low-value orange.
    """
    if value < 0.14:
        return "black"
    if saturation < 0.15:
        return "white" if value > 0.72 else "grey"
    if 15.0 <= hue < 50.0 and value < 0.62:
        return "brown"
    if hue < 15.0 or hue >= 345.0:
        return "red"
    if hue < 45.0:
        return "orange"
    if hue < 70.0:
        return "yellow"
    if hue < 160.0:
        return "green"
    if hue < 200.0:
        return "cyan"
    if hue < 255.0:
        return "blue"
    if hue < 290.0:
        return "purple"
    return "pink"


def classify(image: QImage) -> list[str]:
    """Bucket names for `image`: the dominant bucket plus any >= _MIN_SHARE."""
    if image.isNull():
        return []
    small = image.scaled(
        _SAMPLE_EDGE,
        _SAMPLE_EDGE,
        # A histogram doesn't care about aspect; a fixed square caps the work.
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    counts: Counter[str] = Counter()
    for y in range(small.height()):
        for x in range(small.width()):
            hue, sat, val, _ = small.pixelColor(x, y).getHsvF()
            counts[_bucket_of(max(hue, 0.0) * 360.0, sat, val)] += 1
    total = sum(counts.values())
    if total == 0:
        return []
    ranked = counts.most_common()
    top = [name for name, n in ranked if n / total >= _MIN_SHARE]
    return top or [ranked[0][0]]


def _video_frame(src: Path) -> QImage:
    """One tiny frame via ffmpeg piped to stdout; null image without ffmpeg."""
    if not shutil.which("ffmpeg"):
        return QImage()
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", "1", "-i", str(src), "-frames:v", "1",
        "-vf", f"scale={_SAMPLE_EDGE}:-2",
        "-f", "image2pipe", "-c:v", "png", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return QImage()
    if result.returncode != 0:
        return QImage()
    return QImage.fromData(result.stdout)


def _small_image(entry: WallpaperEntry, thumbnail: Path | None) -> QImage:
    """Small decode for classification; never full-res.

    Prefers the cached thumbnail (already scaled, cheap to read); falls back
    to a scaled decode of the source.
    """
    if thumbnail is not None and thumbnail.exists():
        image = QImage(str(thumbnail))
        if not image.isNull():
            return image
    if entry.kind == "video":
        return _video_frame(entry.path)
    reader = QImageReader(str(entry.path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and not size.isEmpty():
        reader.setScaledSize(
            size.scaled(_SAMPLE_EDGE, _SAMPLE_EDGE, Qt.AspectRatioMode.KeepAspectRatio)
        )
    return reader.read()


class _ColorSignals(QObject):
    finished = Signal(str, list)  # source path, bucket names


class _ColorWorker(QRunnable):
    def __init__(
        self,
        entry: WallpaperEntry,
        thumbnail: Path | None,
        signals: _ColorSignals,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._thumbnail = thumbnail
        self._signals = signals

    def run(self) -> None:  # executed on a pool thread
        colors: list[str] = []
        try:
            colors = classify(_small_image(self._entry, self._thumbnail))
        except Exception:  # one bad file must not kill the pool
            colors = []
        self._signals.finished.emit(str(self._entry.path), colors)


class ColorLoader(QObject):
    """Classifies wallpapers asynchronously; emits `ready` on the UI thread.

    `colors_for` is a plain dict lookup so the filter proxy can call it per
    row with no I/O. Failed classifications are kept in memory (so a file is
    tried once per session) but never persisted, so a missing ffmpeg today
    doesn't mark a video colorless forever.
    """

    ready = Signal(str, list)  # source path, bucket names

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(_MAX_WORKERS)
        self._index: dict[str, tuple[str, ...]] = {}
        self._mtimes: dict[str, int] = {}
        self._seen: set[str] = set()
        self._inflight: set[str] = set()
        self._pending_mtime: dict[str, int] = {}
        # Debounced save: one write after a classification burst, not per file.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_index)
        self._load_index()

    def colors_for(self, path: str) -> tuple[str, ...] | None:
        """Buckets for a path, or None while it hasn't been classified yet."""
        return self._index.get(path)

    def request(self, entry: WallpaperEntry, thumbnail: Path | None) -> None:
        """Queue classification (no-op if cached for the current mtime)."""
        key = str(entry.path)
        if key in self._inflight:
            return
        try:
            mtime = entry.path.stat().st_mtime_ns
        except OSError:
            return
        self._seen.add(key)
        if key in self._index and self._mtimes.get(key) == mtime:
            return
        self._inflight.add(key)
        self._pending_mtime[key] = mtime
        signals = _ColorSignals()
        signals.finished.connect(self._on_finished)
        self._pool.start(_ColorWorker(entry, thumbnail, signals))

    def _on_finished(self, path: str, colors: list) -> None:
        self._inflight.discard(path)
        mtime = self._pending_mtime.pop(path, None)
        self._index[path] = tuple(colors)
        if mtime is not None:
            self._mtimes[path] = mtime
        if colors:
            self._save_timer.start()
        self.ready.emit(path, list(colors))

    def _load_index(self) -> None:
        try:
            data = json.loads(_INDEX_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for path, item in data.items():
            try:
                colors = tuple(c for c in item["colors"] if c in PALETTE_NAMES)
                mtime = int(item["mtime"])
            except (KeyError, TypeError, ValueError):
                continue
            if colors:
                self._index[path] = colors
                self._mtimes[path] = mtime

    def _save_index(self) -> None:
        """Persist this session's classifications (atomic, best-effort).

        Pruned to paths seen this session so the index stays bounded by the
        library instead of growing across folder changes forever.
        """
        data = {
            path: {"mtime": self._mtimes[path], "colors": list(colors)}
            for path, colors in self._index.items()
            if colors and path in self._seen and path in self._mtimes
        }
        try:
            _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _INDEX_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(_INDEX_PATH)
        except OSError:
            pass  # cache write is best-effort
