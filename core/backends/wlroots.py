"""Wallpaper backend for wlr-layer-shell compositors.

Images  -> swww (needs swww-daemon; we start it on demand if absent).
Video   -> mpvpaper, launched detached with -p (auto-pause when the wallpaper
           is hidden, i.e. covered by a fullscreen window).

Applying an image stops any running mpvpaper: there is only ever one wallpaper
at a time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import (
    BackendError,
    ImageTransition,
    MissingDependencyError,
    WallpaperBackend,
)


_SWWW_CANDIDATES = ("swww", "awww")

# mpv options passed through to mpvpaper via -o. Tuned for smooth, quiet,
_MPV_OPTIONS = " ".join(
    [
        "loop",
        "--no-audio",
        "--hwdec=auto",
        "--profile=high-quality",
        "--video-sync=display-resample",
        "--interpolation",
        "--tscale=oversample",
    ]
)

_ALL_OUTPUTS = "*"
_DAEMON_TIMEOUT_S = 3.0
# How long the outgoing mpvpaper keeps covering the screen after a new one is
# launched, before we retire it. Must exceed mpvpaper's surface-map time so the
# new video is up before the old goes away — otherwise swww's background would
# flash through the gap. ~0.8s is comfortably past typical mpv startup.
_VIDEO_SWAP_DELAY_S = 0.8


class WlrootsBackend(WallpaperBackend):
    """Drives swww/mpvpaper on a wlr-layer-shell compositor."""

    def is_available(self) -> bool:
        return os.environ.get("WAYLAND_DISPLAY") is not None

    # --- public API -----------------------------------------------------

    def set_image(self, path: Path, transition: ImageTransition | None = None) -> None:
        self._stop_video()
        tool = self._resolve(_SWWW_CANDIDATES)
        self._ensure_daemon(tool)
        self._run([tool, "img", *self._transition_args(transition), str(path)])

    @staticmethod
    def _transition_args(transition: ImageTransition | None) -> list[str]:
        """Translate a transition choice into swww `--transition-*` flags."""
        if transition is None:
            return []
        args = ["--transition-type", transition.type, "--transition-fps", str(transition.fps)]
        # swww ignores duration for the instant 'none'/'simple' switch.
        if transition.type not in ("none", "simple"):
            args += ["--transition-duration", str(transition.duration)]
        return args

    def set_video(self, path: Path) -> None:
        mpvpaper = self._require("mpvpaper")
        # Note the mpvpaper already painting (if any) so we can retire it *after*
        # the new one is up, not before — see the seamless-swap rationale below.
        old_pids = self._mpvpaper_pids()
        # Idempotent restore: if exactly one mpvpaper is already rendering THIS
        # file, there is nothing to do — re-spawning would only stack a second
        # GPU-heavy decoder on the same output (freeze + downscale). This makes a
        # redundant `--restore` (e.g. a stray .desktop autostart firing on top of
        # the compositor's exec line) a harmless no-op. A *broken* state — zero,
        # or two-plus instances — deliberately falls through to the spawn+retire
        # path below, so a manual re-apply still recovers a stuck wallpaper.
        if len(old_pids) == 1 and self._video_path_of(old_pids[0]) == str(path):
            return
        # -p: auto-pause when hidden (the MVP fullscreen auto-pause).
        self._spawn_detached(
            [mpvpaper, "-p", "-o", _MPV_OPTIONS, _ALL_OUTPUTS, str(path)]
        )
        if old_pids:
            # Video -> video: killing the old mpvpaper first would briefly uncover
            # swww's stale background during the new one's startup. Instead we let
            # the old video keep covering the screen and retire it a beat later,
            # once the new surface has mapped — a seamless swap. Detached so it
            # outlives our GUI, which exits immediately after Enter.
            self._retire_pids(old_pids)

    # --- helpers --------------------------------------------------------

    def _ensure_daemon(self, tool: str) -> None:
        """Start the wallpaper daemon if it is not already responding."""
        if self._run([tool, "query"], check=False).returncode == 0:
            return
        daemon = self._require(f"{Path(tool).name}-daemon")
        self._spawn_detached([daemon])
        deadline = time.monotonic() + _DAEMON_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._run([tool, "query"], check=False).returncode == 0:
                return
            time.sleep(0.1)
        raise BackendError(f"{Path(daemon).name} did not become ready in time.")

    @staticmethod
    def _resolve(candidates: tuple[str, ...]) -> str:
        """Return the path to the first available tool, or raise."""
        for name in candidates:
            found = shutil.which(name)
            if found:
                return found
        raise MissingDependencyError(
            "no wallpaper tool found; install one of: " + ", ".join(candidates)
        )

    def _stop_video(self) -> None:
        """Terminate any running mpvpaper instance (best effort)."""
        subprocess.run(
            ["pkill", "-x", "mpvpaper"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    @staticmethod
    def _mpvpaper_pids() -> list[int]:
        """PIDs of currently running mpvpaper processes (empty if none)."""
        result = subprocess.run(
            ["pgrep", "-x", "mpvpaper"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return [int(pid) for pid in result.stdout.split()]

    @staticmethod
    def _video_path_of(pid: int) -> str | None:
        """The media file an mpvpaper PID is playing (its last argv entry), or None.

        Read from /proc/<pid>/cmdline (NUL-separated argv). mpvpaper's media path
        is the final argument, after the options and the `*` output selector — the
        same way we launch it in set_video. Used to detect an already-correct
        wallpaper so a redundant restore can no-op instead of stacking a duplicate.
        """
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = [field for field in fh.read().split(b"\x00") if field]
        except OSError:
            return None
        return argv[-1].decode("utf-8", "replace") if argv else None

    @staticmethod
    def _retire_pids(pids: list[int]) -> None:
        """Kill the given mpvpaper PIDs after the swap delay, detached.

        The delay lets the freshly launched mpvpaper map its surface before we
        remove the old one, so swww's background never shows through the seam.
        Runs in its own session so it survives our GUI exiting right after apply.
        """
        targets = " ".join(str(pid) for pid in pids)
        subprocess.Popen(
            ["sh", "-c", f"sleep {_VIDEO_SWAP_DELAY_S}; kill {targets} 2>/dev/null"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    @staticmethod
    def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise BackendError(
                f"command failed ({result.returncode}): {' '.join(cmd)}\n"
                f"{result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _spawn_detached(cmd: list[str]) -> None:
        """Launch a fully detached process (survives GUI exit)."""
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
