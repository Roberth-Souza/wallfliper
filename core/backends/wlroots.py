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
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..firstframe import first_frame
from ..state import cache_dir
from . import transitions as tr
from .base import (
    BackendError,
    ImageTransition,
    MissingDependencyError,
    WallpaperBackend,
)


_SWWW_CANDIDATES = ("swww", "awww")


# mpv options passed through to mpvpaper via -o. Tuned for robust, quiet, looping
# playback. no-config isolates from the user's ~/.config/mpv: a custom mpv.conf
# (broken hwdec/vo, scripts) is a common cause of a wallpaper that never plays.
# osd-level=0 hides mpv's corner messages over the wallpaper (`no-osd` is not
# an mpv option — it is rejected at startup). Hardware decode +
# high-quality scaling do the rest. The initial pause state is appended per-launch
# (see _mpvpaper_cmd): a hard cut starts playing at once, the seamless lead-in
# starts paused on frame 0 and is unpaused over IPC.
#
# video-sync stays on mpv's default decoupled (audio/system) clock rather than
# display-resample: a wallpaper outlives DPMS sleep and suspend, and slaving
# playback to the display's vsync clock makes mpv freeze on a stale, wrongly
# scaled buffer when the output is torn down and recreated on wake. For the same
# reason interpolation/tscale are omitted — they need continuous presentation
# feedback, are pure GPU cost on a looping background, and stall across reconfig.
_MPV_OPTIONS = " ".join(
    [
        "loop",
        "--no-audio",
        "no-config",
        "osd-level=0",
        "--hwdec=auto",
        "--profile=high-quality",
        "--video-sync=audio",
    ]
)

_ALL_OUTPUTS = "*"
# Where a still-pending seamless driver announces itself. The GUI exits right
# after apply, so the *next* launch is a different process with no memory of the
# driver the last one left running — without this file nothing would cancel it,
# and its video would map on top of whatever was applied in the meantime.
_PENDING_FILE = "pending-transition.json"
_DAEMON_TIMEOUT_S = 3.0
# How long the outgoing mpvpaper keeps covering the screen after a new one is
# launched, before we retire it. Must exceed mpvpaper's surface-map time so the
# new video is up before the old goes away — otherwise swww's background would
# flash through the gap. ~0.8s is comfortably past typical mpv startup.
_VIDEO_SWAP_DELAY_S = 0.8
# Seamless lead-in: the most we will launch mpvpaper *before* the transition
# ends, so its cold-start overlaps the animation instead of stacking after it.
# Roughly mpv's startup cost; a larger value buys nothing once mpv is up before
# the animation finishes.
#
# This is only a ceiling — the actual lead-in is clamped by the easing (see
# _prewarm_for). mpvpaper covers the whole screen the instant its surface maps,
# with the transition's endpoint frame, so launching it while the animation is
# still visibly moving truncates it: the switch runs smoothly and then snaps to
# the final image. How early that is safe depends entirely on the curve, which
# is why the cap alone is not enough.
_MPV_PREWARM_MAX_S = 0.6
_SEAMLESS_DRIVER = Path(__file__).resolve().parent.parent / "seamless.py"


class WlrootsBackend(WallpaperBackend):
    """Drives swww/mpvpaper on a wlr-layer-shell compositor."""

    def __init__(self) -> None:
        # The most recent seamless driver (core/seamless.py) that may still be
        # pending, and the IPC socket it was handed. It brings its video up ~1s in
        # the future, so a superseding apply has to cancel it (see
        # _cancel_pending_transition) or the stale video maps on top of the newer
        # wallpaper; the socket is unlinked there since a killed driver can't.
        self._pending_driver: subprocess.Popen | None = None
        self._pending_sock: str | None = None

    def is_available(self) -> bool:
        return os.environ.get("WAYLAND_DISPLAY") is not None

    # --- public API -----------------------------------------------------

    def set_image(self, path: Path, transition: ImageTransition | None = None) -> None:
        self._cancel_pending_transition()  # a pending video must not map over the image
        self._stop_video()
        tool = self._resolve(_SWWW_CANDIDATES)
        self._ensure_daemon(tool)
        if transition is not None:
            transition = tr.resolve(transition)
        self._run([tool, "img", *self._transition_args(transition), str(path)])

    @staticmethod
    def _transition_args(transition: ImageTransition | None) -> list[str]:
        """Translate a resolved transition into swww `--transition-*` flags.

        Expects a transition already run through `tr.resolve` (the callers do
        it, so the flags and the video driver's timing describe the same
        animation); resolving again here would re-roll a random preset.
        """
        if transition is None:
            return []
        args = [
            "--transition-type", transition.type,
            "--transition-fps", str(transition.fps),
        ]
        # swww ignores duration for the instant 'none'/'simple' switch.
        if transition.type not in ("none", "simple"):
            args += ["--transition-duration", str(transition.duration)]
        # Preset parameters; unset ones keep swww's defaults.
        if transition.angle is not None:
            args += ["--transition-angle", f"{transition.angle:g}"]
        if transition.pos is not None:
            args += ["--transition-pos", transition.pos]
        if transition.bezier is not None:
            args += ["--transition-bezier", transition.bezier]
        if transition.wave is not None:
            args += ["--transition-wave", transition.wave]
        return args

    def set_video(self, path: Path, transition: ImageTransition | None = None) -> None:
        mpvpaper = self._require("mpvpaper")
        # Supersede any in-flight seamless lead-in *before* sampling the running
        # state, so old_pids includes a (paused) video the cancelled driver had
        # already mapped — it gets retired below instead of lingering frozen.
        had_pending = self._pending_driver is not None
        self._cancel_pending_transition()
        old_pids = self._mpvpaper_pids()
        # Already rendering this exact file → no-op, avoid stacking a 2nd GPU
        # decoder. Skipped when a driver was just cancelled: that mpvpaper is
        # paused on frame 0 and its un-pauser is now dead, so it must be redone.
        # Counted in process *groups*: one mpvpaper instance can be two
        # processes (see _groups_of), so a PID count would never match.
        if (
            not had_pending
            and len(self._groups_of(old_pids)) == 1
            and any(self._video_path_of(pid) == str(path) for pid in old_pids)
        ):
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
        swww, or the first-frame still isn't cached yet (extraction is warmed
        off-thread on selection; the apply path never blocks the GUI on ffmpeg).

        Any covering mpvpaper is dropped *now* so the swww animation is visible
        underneath it; a detached driver (core/seamless.py) then brings the video
        up and unpauses it in sync with the animation, so this works even though
        the GUI exits right after apply.
        """
        swww = self._resolve_optional(_SWWW_CANDIDATES)
        if swww is None:
            return False
        # cached_only: don't run ffmpeg on the GUI thread at apply. A not-yet-warmed
        # clip degrades to a hard cut instead of freezing the overlay.
        frame = first_frame(path, cached_only=True)
        if frame is None:
            return False
        try:
            self._ensure_daemon(swww)
        except BackendError:
            return False  # daemon won't start → fall back to a plain hard cut
        # Resolve the preset once so the swww flags below and the driver's
        # unpause timing agree on the same concrete animation (see tr.resolve).
        transition = tr.resolve(transition)
        # Dispatch the transition *before* retiring the old video. swww img returns
        # as soon as the daemon accepts the frame (it animates asynchronously), so
        # killing mpvpaper right after reveals a wipe that is already painting — no
        # blank-background flash on a video->video switch (swww's surface is stale
        # there). If swww itself fails (daemon/compositor hiccup), bail so the
        # caller falls back to a hard cut instead of dropping the current wallpaper
        # onto a frame that never rendered.
        if self._run(
            [swww, "img", *self._transition_args(transition), str(frame)],
            check=False,
        ).returncode != 0:
            return False
        if old_pids:
            self._kill_pids(old_pids)  # reveal swww so its transition shows
        instant = transition.type in ("none", "simple")
        duration = 0.0 if instant else transition.duration
        sock = self._ipc_socket_path()
        cfg = json.dumps(
            {
                "cmd": self._mpvpaper_cmd(mpvpaper, path, ipc_socket=sock),
                # Hard-cut command the driver falls back to if the IPC unpause
                # never lands, so a failed handoff plays instead of freezing.
                "fallback": self._mpvpaper_cmd(mpvpaper, path),
                "sock": sock,
                "duration": duration,
                "prewarm": self._prewarm_for(transition, duration),
            }
        )
        # The driver runs in its own process: it launches mpvpaper paused on the
        # first frame partway through the transition (overlapping cold-start) and
        # unpauses it over IPC the instant the duration elapses — so motion begins
        # exactly when the animation ends, not a cold-start later. Tracked so a
        # quick follow-up apply can cancel it before it maps a now-stale video.
        self._pending_driver = self._spawn_detached(
            [sys.executable, str(_SEAMLESS_DRIVER), cfg]
        )
        self._pending_sock = sock
        self._record_pending(self._pending_driver.pid, sock)
        return True

    @staticmethod
    def _prewarm_for(transition: ImageTransition, duration: float) -> float:
        """How early mpvpaper may be launched without cutting the animation short.

        Never earlier than the point where the easing has visually finished
        (`tr.completion_fraction`), and never more than the cap — an eased-out
        transition like 'iris' is done a third of the way in and can hide most
        of mpv's cold start, while a back-loaded one like 'chop' moves until the
        last frame and must not be covered at all.
        """
        return min(_MPV_PREWARM_MAX_S, duration * (1.0 - tr.completion_fraction(transition)))

    def _cancel_pending_transition(self) -> None:
        """Kill a still-pending seamless driver so a newer apply wins.

        The driver brings its video up ~1s after apply (it overlaps mpv's
        cold-start with the swww animation, then unpauses over IPC). Without
        this, a second apply races that timer and the stale video maps on top of
        the newer wallpaper — switching video->image fast would leave the video
        showing, and fast video->video would land on the wrong clip.

        The driver to cancel is usually *not* one of ours: applying closes the
        GUI, so the next launch is a fresh process that only knows about the
        driver through the file the spawning process left behind. Hence the
        on-disk record, checked before the in-memory handle.

        Killing the driver's process group stops it before it launches or
        unpauses mpv. Any mpvpaper it already spawned escaped into its own
        session (start_new_session), so killpg here doesn't touch it; the caller
        reaps it separately via old_pids / _stop_video.
        """
        driver = self._pending_driver
        self._pending_driver = None
        self._pending_sock = None
        record = self._take_pending_record()
        if record is not None:
            pid, sock = record
            # A recycled PID could be anything by now; only kill something that
            # is still one of our drivers.
            if self._is_seamless_driver(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass  # already exited
            if sock:
                # The driver was killed before its own socket cleanup, so do it
                # here — otherwise every superseded transition leaks an IPC
                # socket. Safe even if mpv came up: it keeps its listening fd,
                # only the pathname goes.
                Path(sock).unlink(missing_ok=True)
        if driver is not None:
            try:
                driver.wait(timeout=1.0)  # reap ours so they don't pile up as zombies
            except subprocess.TimeoutExpired:
                pass  # SIGKILL is near-instant; never block apply on a stuck reap

    @staticmethod
    def _pending_path() -> Path:
        return cache_dir() / _PENDING_FILE

    @classmethod
    def _record_pending(cls, pid: int, sock: str) -> None:
        """Announce a just-spawned driver so any later apply can cancel it."""
        path = cls._pending_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"pid": pid, "sock": sock}))
        except OSError:
            pass  # best effort: a lost record only costs the cross-process cancel

    @classmethod
    def _take_pending_record(cls) -> tuple[int, str] | None:
        """Read and clear the pending-driver record, if there is one."""
        path = cls._pending_path()
        try:
            data = json.loads(path.read_text())
            pid = int(data["pid"])
            sock = str(data.get("sock", ""))
        except (OSError, ValueError, KeyError, TypeError):
            pid, sock = 0, ""
        path.unlink(missing_ok=True)
        return (pid, sock) if pid > 0 else None

    @staticmethod
    def _is_seamless_driver(pid: int) -> bool:
        """True if `pid` is still running our seamless driver (not a recycled PID)."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return _SEAMLESS_DRIVER.name.encode() in fh.read()
        except OSError:
            return False

    @staticmethod
    def _ipc_socket_path() -> str:
        """A fresh mpv IPC socket path (unique per launch, never stale)."""
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(runtime) if runtime else cache_dir()
        if runtime is None:
            # XDG_RUNTIME_DIR is normally always set on a live Wayland session;
            # the cache-dir fallback may not exist yet, and mpv won't create the
            # socket's parent — without it the IPC handoff fails and the seamless
            # transition silently degrades to a hard cut. Best-effort create.
            try:
                base.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        return str(base / f"wallfliper-mpv-{time.monotonic_ns()}.sock")

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
    def _groups_of(pids: list[int]) -> list[int]:
        """Process-group ids for `pids`, deduplicated.

        mpvpaper is not always a single process — it forks a holder alongside
        the process that owns libmpv, and the fork lands a moment after launch.
        Killing the PIDs a `pgrep` happened to see therefore leaves the sibling
        running whenever the snapshot caught it mid-fork, and an orphaned
        mpvpaper keeps covering the screen with a frozen frame: the wallpaper
        looks like a video that never plays. Each instance is launched with
        start_new_session, so its whole family shares one group — signal that
        instead and nothing is missed.
        """
        groups: list[int] = []
        for pid in pids:
            try:
                gid = os.getpgid(pid)
            except OSError:
                continue  # already gone
            if gid not in groups:
                groups.append(gid)
        return groups

    @classmethod
    def _kill_pids(cls, pids: list[int]) -> None:
        """Terminate the given mpvpaper instances immediately (best effort)."""
        for gid in cls._groups_of(pids):
            try:
                os.killpg(gid, signal.SIGTERM)
            except OSError:
                pass

    @classmethod
    def _retire_pids(cls, pids: list[int]) -> None:
        """Kill the given mpvpaper instances after the swap delay, detached.

        The delay lets the freshly launched mpvpaper map its surface before we
        remove the old one, so swww's background never shows through the seam.
        Runs in its own session so it survives our GUI exiting right after apply.
        """
        groups = cls._groups_of(pids)
        if not groups:
            return
        targets = " ".join(f"-{gid}" for gid in groups)  # negative = process group
        subprocess.Popen(
            ["sh", "-c", f"sleep {_VIDEO_SWAP_DELAY_S}; kill -- {targets} 2>/dev/null"],
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
    def _spawn_detached(cmd: list[str]) -> subprocess.Popen:
        """Launch a fully detached process (survives GUI exit); return its handle."""
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
