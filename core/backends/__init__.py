"""Wallpaper backends."""

from .base import (
    BackendError,
    MissingDependencyError,
    WallpaperBackend,
    get_backend,
)

__all__ = [
    "BackendError",
    "MissingDependencyError",
    "WallpaperBackend",
    "get_backend",
]
