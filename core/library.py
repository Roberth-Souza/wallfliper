"""Wallpaper library: scan a directory and classify entries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .state import WallpaperKind


IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"jpg", "jpeg", "png", "webp", "bmp", "gif", "avif", "jxl", "tiff"}
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {"mp4", "mkv", "webm", "mov", "avi", "m4v"}
)


@dataclass(frozen=True)
class WallpaperEntry:
    path: Path
    kind: WallpaperKind

    @property
    def name(self) -> str:
        return self.path.name


def kind_of(path: Path) -> WallpaperKind | None:
    """Classify a file by extension, or None if unsupported."""
    ext = path.suffix.lower().lstrip(".")
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def scan(directory: Path) -> list[WallpaperEntry]:
    """Return supported wallpapers in `directory`, sorted by name.

    Non-recursive and non-hidden by design: a flat, predictable list keeps
    scanning cheap and the grid uncluttered.
    """
    if not directory.is_dir():
        return []

    entries: list[WallpaperEntry] = []
    for child in directory.iterdir():
        if child.name.startswith(".") or not child.is_file():
            continue
        kind = kind_of(child)
        if kind is not None:
            entries.append(WallpaperEntry(path=child, kind=kind))

    entries.sort(key=lambda e: e.name.lower())
    return entries
