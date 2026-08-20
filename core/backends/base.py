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

    `type` is a preset name from `backends/transitions.py`, one of swww's raw
    `--transition-type` values, or "random". Only images animate; video has no
    equivalent. `type == "none"` is an instant switch (and ignores duration,
    like swww does).

    The remaining fields mirror swww's per-transition parameters and are only
    meaningful for some types (angle: wipe/wave, pos: grow/outer, wave:
    wave). None means "leave swww's default", which is what a raw type gets;
    presets fill them in. Resolve a transition through `transitions.resolve`
    before reading these — a preset name carries its parameters implicitly.
    """

    type: str = "random"
    duration: float = 1.0
    fps: int = 50
    angle: float | None = None
    pos: str | None = None
    bezier: str | None = None
    wave: str | None = None


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
    def set_video(self, path: Path, transition: ImageTransition | None = None) -> None:
        """Apply a looping video as the wallpaper across all outputs.

        `transition` is the swww animation used for the seamless lead-in: the
        switch animates to a still of the video's first frame, then the live
        video takes over on top of it. None applies the video with a hard cut
        (no still, no animation) — used on login restore, where there is no
        prior frame to transition from.
        """

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
