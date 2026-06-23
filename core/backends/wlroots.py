"""Wallpaper backend for wlr-layer-shell compositors.

Images  -> swww (needs swww-daemon; we start it on demand if absent).
Video   -> mpvpaper, launched detached with -p (auto-pause when the wallpaper
           is hidden, i.e. covered by a fullscreen window).

Applying an image stops any running mpvpaper: there is only ever one wallpaper
at a time.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..firstframe import first_frame
from ..state import cache_dir
from .base import (
    BackendError,
    ImageTransition,
    MissingDependencyError,
    WallpaperBackend,
)


_SWWW_CANDIDATES = ("swww", "awww")

# swww's animated transitions minus 'fade'. We resolve 'random' from this pool
# ourselves rather than passing swww's own 'random', which can land on fade.
# Fade finishes visually well before the transition duration, so the seamless
# video lead-in (which keeps mpvpaper paused for the full duration) would sit on
# a frozen frame after the animation is already done. The instant 'none'/'simple'
# switches are excluded too — these are the actual animations.
_RANDOM_TRANSITIONS = (
    "wipe",
    "wave",
    "grow",
    "center",
    "outer",
    "left",
    "right",
    "top",
    "bottom",
)

# mpv options passed through to mpvpaper via -o. Tuned for robust, quiet, looping
# playback. no-config isolates from the user's ~/.config/mpv: a custom mpv.conf
# (broken hwdec/vo, scripts) is a common cause of a wallpaper that never plays.
# no-osd hides mpv's corner messages over the wallpaper. Hardware decode +
# high-quality scaling/interpolation do the rest. The initial pause state is
# appended per-launch (see _mpvpaper_cmd): a hard cut starts playing at once,
# the seamless lead-in starts paused on frame 0 and is unpaused over IPC.
_MPV_OPTIONS = " ".join(
    [
        "loop",
        "--no-audio",
        "no-config",
        "no-osd",
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
# Seamless lead-in: how far before the transition ends mpvpaper is launched, so
# its cold-start overlaps the animation instead of stacking after it. Roughly
# mpv's startup cost — too small leaves a residual delay before motion, too
# large maps mpv (frozen on frame 0) over the transition's last frames. The
# detached driver gates the unpause on the full duration regardless, so motion
# never begins before the animation visually ends.
_MPV_PREWARM_S = 0.4
_SEAMLESS_DRIVER = Path(__file__).resolve().parent.parent / "seamless.py"


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
        ttype = transition.type
        # Resolve 'random' here (excluding fade) instead of letting swww pick.
        if ttype == "random":
            ttype = random.choice(_RANDOM_TRANSITIONS)
        args = ["--transition-type", ttype, "--transition-fps", str(transition.fps)]
        # swww ignores duration for the instant 'none'/'simple' switch.
        if ttype not in ("none", "simple"):
            args += ["--transition-duration", str(transition.duration)]
        return args

    def set_video(self, path: Path, transition: ImageTransition | None = None) -> None:
        mpvpaper = self._require("mpvpaper")
        old_pids = self._mpvpaper_pids()
        # Already rendering this exact file → no-op, avoid stacking a 2nd GPU decoder.
        if len(old_pids) == 1 and self._video_path_of(old_pids[0]) == str(path):
            return
        if transition is not None and self._transition_into_video(
            path, transition, old_pids, mpvpaper
        ):
            return
        # Hard cut (restore on login, or no ffmpeg/swww to fake a transition).
        # -p: auto-pause when hidden (the MVP fullscreen auto-pause).
        self._spawn_detached(self._mpvpaper_cmd(mpvpaper, path))
        if old_pids:
            # Video -> video: killing the old mpvpaper first would briefly uncover
            # swww's stale background during the new one's startup. Instead we let
            # the old video keep covering the screen and retire it a beat later,
            # once the new surface has mapped — a seamless swap. Detached so it
            # outlives our GUI, which exits immediately after Enter.
            self._retire_pids(old_pids)

    def _transition_into_video(
        self,
        path: Path,
        transition: ImageTransition,
        old_pids: list[int],
        mpvpaper: str,
    ) -> bool:
        """Fake a video transition by animating to its first frame via swww.

        swww has no concept of video; mpvpaper has no transitions. So animate the
        switch on a still of the video's opening frame, then bring the live video
        up on top of that identical frame — the cut is invisible. Returns False
        (caller falls back to a hard cut) when the pieces aren't available: no
        swww, or no ffmpeg to extract the still.

        Any covering mpvpaper is dropped *now* so the swww animation is visible
        underneath it; a detached driver (core/seamless.py) then brings the video
        up and unpauses it in sync with the animation, so this works even though
        the GUI exits right after apply.
        """
        swww = self._resolve_optional(_SWWW_CANDIDATES)
        if swww is None:
            return False
        frame = first_frame(path)
        if frame is None:
            return False
        self._ensure_daemon(swww)
        if old_pids:
            self._kill_pids(old_pids)  # reveal swww so its transition shows
        self._run(
            [swww, "img", *self._transition_args(transition), str(frame)],
            check=False,
        )
        instant = transition.type in ("none", "simple")
        duration = 0.0 if instant else transition.duration
        sock = self._ipc_socket_path()
        cfg = json.dumps(
            {
                "cmd": self._mpvpaper_cmd(mpvpaper, path, ipc_socket=sock),
                "sock": sock,
                "duration": duration,
                "prewarm": _MPV_PREWARM_S,
            }
        )
        # The driver runs in its own process: it launches mpvpaper paused on the
        # first frame partway through the transition (overlapping cold-start) and
        # unpauses it over IPC the instant the duration elapses — so motion begins
        # exactly when the animation ends, not a cold-start later.
        self._spawn_detached([sys.executable, str(_SEAMLESS_DRIVER), cfg])
        return True

    @staticmethod
    def _ipc_socket_path() -> str:
        """A fresh mpv IPC socket path (unique per launch, never stale)."""
        base = os.environ.get("XDG_RUNTIME_DIR") or str(cache_dir())
        return str(Path(base) / f"wallfliper-mpv-{time.monotonic_ns()}.sock")

    @staticmethod
    def _mpvpaper_cmd(mpvpaper: str, path: Path, ipc_socket: str | None = None) -> list[str]:
        """argv for a detached, auto-pausing mpvpaper covering every output.

        Without `ipc_socket` it is a hard cut: start playing immediately. With
        one it starts paused on frame 0 with an IPC server, for the seamless
        driver to unpause once the transition has finished.
        """
        opts = _MPV_OPTIONS
        if ipc_socket is None:
            opts += " pause=no"
        else:
            opts += f" pause=yes --input-ipc-server={ipc_socket}"
        return [mpvpaper, "-p", "-o", opts, _ALL_OUTPUTS, str(path)]

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
    def _resolve_optional(candidates: tuple[str, ...]) -> str | None:
        """Path to the first available tool, or None if none are installed."""
        for name in candidates:
            found = shutil.which(name)
            if found:
                return found
        return None

    @classmethod
    def _resolve(cls, candidates: tuple[str, ...]) -> str:
        """Return the path to the first available tool, or raise."""
        found = cls._resolve_optional(candidates)
        if found is None:
            raise MissingDependencyError(
                "no wallpaper tool found; install one of: " + ", ".join(candidates)
            )
        return found

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
    def _kill_pids(pids: list[int]) -> None:
        """Terminate the given PIDs immediately (best effort)."""
        subprocess.run(
            ["kill", *[str(pid) for pid in pids]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

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
