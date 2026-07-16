"""Post-apply theming integration.

Wallpaper *painting* (swww/mpvpaper) is decoupled from *theming*: tools like
noctalia-shell, matugen, wallust or pywal derive a color scheme from the
wallpaper image. Since they don't watch swww, wallfliper notifies them after
applying. Best-effort and non-blocking: a failure here never affects the
wallpaper that was just set, and never blocks the UI.

Color tools only understand still images. For video wallpapers we first extract
a representative frame with ffmpeg and theme from that, so the scheme still
adapts to a video. The extraction is chained into the same detached shell
command (`ffmpeg ... && <notify>`), so it stays off the UI thread and silently
does nothing if ffmpeg is missing.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .state import WallpaperKind, cache_dir

# noctalia v5+ ships a native binary with its own IPC CLI; v4 and earlier is a
# quickshell config reached through `qs`. Detected by binary, newest first.
_NOCTALIA_V5 = "noctalia msg wallpaper-set {path}"
_NOCTALIA_V4 = 'qs -c noctalia-shell ipc call wallpaper set {path} ""'


def notify_color_tools(path: Path, kind: WallpaperKind, hook: str = "") -> None:
    """Tell external theming tools the wallpaper changed.

    If `hook` is set, run it as a shell command with `{path}` substituted (lets
    users wire up matugen/wallust/pywal/etc). Otherwise auto-detect
    noctalia-shell and have it regenerate its scheme. For video, `{path}` /
    the noctalia path is a still frame extracted from the clip.
    """
    color_source, prefix = _color_source(path, kind)
    if color_source is None:
        return  # video but no ffmpeg -> nothing we can theme from

    quoted = shlex.quote(str(color_source))
    if hook:
        _spawn(prefix + hook.replace("{path}", quoted))
        return
    # noctalia regenerates colors even when its own wallpaper rendering is
    # disabled; if it isn't running the call fails fast and is swallowed.
    if shutil.which("noctalia"):
        _spawn(prefix + _NOCTALIA_V5.format(path=quoted))
    elif shutil.which("qs"):
        _spawn(prefix + _NOCTALIA_V4.format(path=quoted))


def _color_source(path: Path, kind: WallpaperKind) -> tuple[Path | None, str]:
    """Return (image to theme from, shell prefix that produces it).

    Images theme directly (no prefix). Video themes from a still frame: the
    prefix is an `ffmpeg ... &&` that writes the frame just before the notify
    command runs. Returns (None, "") when video can't be themed (no ffmpeg).
    """
    if kind != "video":
        return path, ""
    if not shutil.which("ffmpeg"):
        return None, ""
    frame = _next_frame_path()
    frame.parent.mkdir(parents=True, exist_ok=True)
    # -ss 1: skip a possible black/fade-in opening frame. scale: color tools
    # only need the dominant/secondary colors, so 320px wide is ample and keeps
    # the extraction near-instant and the file tiny. -update 1: write a single
    # still without image-sequence warnings.
    prefix = (
        f"ffmpeg -y -ss 1 -i {shlex.quote(str(path))} -frames:v 1 "
        f"-vf scale=320:-2 -update 1 {shlex.quote(str(frame))} >/dev/null 2>&1 && "
    )
    return frame, prefix


def _next_frame_path() -> Path:
    """Pick a frame file whose path differs from the previously used one.

    noctalia (and similar tools) skip regenerating when handed the same
    wallpaper path twice — which broke video->video switches when every frame
    reused one filename. Alternating between two files guarantees the path
    changes each time, while staying strictly bounded to 2 cached frames.
    """
    cache = cache_dir()
    a, b = cache / "colorframe-a.png", cache / "colorframe-b.png"
    if not a.exists():
        return a
    if not b.exists():
        return b
    # Both exist: the most-recently-written one is what the tool currently has,
    # so write to (and return) the other.
    return b if a.stat().st_mtime >= b.stat().st_mtime else a


def _spawn(command: str) -> None:
    """Fire-and-forget a shell command: detached, output and errors discarded."""
    try:
        subprocess.Popen(
            ["sh", "-c", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass
