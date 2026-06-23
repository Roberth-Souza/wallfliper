"""Wallpaper backend interface.

The UI and the rest of the app talk only to `WallpaperBackend`. Concrete
backends shell out to the actual rendering tools (swww/mpvpaper). The interface
exists so the rendering tool can be swapped or mocked in tests, not so other
operating systems can be bolted on.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImageTransition:
    """How swww animates the switch to a new still image.

    `type` is one of swww's `--transition-type` values. Only images animate;
    video has no equivalent. `type == "none"` is an instant switch (and ignores
    duration, like swww does). Currently fixed to a random transition; a
    per-user picker may come back later.
    """

    type: str = "random"
    duration: float = 1.0
    fps: int = 50


class BackendError(RuntimeError):
    """Raised when a wallpaper operation cannot be completed."""


class MissingDependencyError(BackendError):
    """A required external tool (swww, mpvpaper, ...) is not installed."""


class WallpaperBackend(ABC):
    """Applies wallpapers to the running compositor."""

    @abstractmethod
    def set_image(self, path: Path, transition: ImageTransition | None = None) -> None:
        """Apply a static image as the wallpaper across all outputs.

        `transition` controls the switch animation; None uses the tool default.
        """

    @abstractmethod
    def set_video(self, path: Path) -> None:
        """Apply a looping video as the wallpaper across all outputs."""

    @abstractmethod
    def is_available(self) -> bool:
        """True if this backend can run in the current environment."""

    @staticmethod
    def _require(tool: str) -> str:
        """Return the absolute path to `tool` or raise MissingDependencyError."""
        resolved = shutil.which(tool)
        if resolved is None:
            raise MissingDependencyError(
                f"'{tool}' is not installed or not on PATH."
            )
        return resolved


def get_backend() -> WallpaperBackend:
    """Return the wallpaper backend for the current session.

    MVP targets wlr-layer-shell Wayland compositors (Hyprland, Sway, river,
    Wayfire, niri). There is exactly one backend today; the indirection keeps
    the call sites stable.
    """
    from .wlroots import WlrootsBackend

    return WlrootsBackend()
