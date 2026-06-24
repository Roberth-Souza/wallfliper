"""First-frame extraction for seamless video-wallpaper transitions.

swww animates image->image switches; mpvpaper (video) has no transition of its
own. To fake one when applying a video wallpaper, the backend swww-animates to a
still of the video's opening frame, then brings mpvpaper up on top of that
identical frame — the cut to live playback is invisible. This module produces
(and caches) that still.

It is deliberately separate from `thumbnails.py` (which writes a small `-ss 1`
JPEG for the grid) and `integrations.py` (which writes a 320px `-ss 1` frame to
theme from): both want a *representative* frame, this wants the *exact first*
frame at full resolution so it lines up with what mpv renders pixel-for-pixel.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .state import cache_dir

_FRAME_DIR_NAME = "firstframes"
_EXTRACT_TIMEOUT_S = 30  # a stuck ffmpeg must not block the apply forever


def first_frame(video: Path, cached_only: bool = False) -> Path | None:
    """Return a PNG of `video`'s first frame, extracting and caching on first use.

    Kept at the video's native resolution (no downscale) and as PNG (no second
    lossy pass) so swww transitions to a still that matches mpv's frame 0 as
    closely as possible. Cached under ~/.cache/wallfliper/firstframes/, keyed by
    path+mtime+size so an edited file re-extracts. Returns None if ffmpeg is
    missing or extraction fails — the caller falls back to a hard cut.

    `cached_only` returns the still only if it is already cached, never
    extracting. The apply path uses it so a not-yet-warmed clip degrades to a
    hard cut instead of running ffmpeg synchronously on the GUI thread; the
    off-thread warmer (selection) does the actual extraction.
    """
    try:
        dest = _cache_path(video)
    except OSError:
        return None  # file vanished between selection and apply → hard-cut fallback
    if dest.exists():
        return dest
    if cached_only:
        return None  # not warmed yet; caller hard-cuts rather than block on ffmpeg
    if not shutil.which("ffmpeg"):
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Unique tmp per call (atomic, and safe when the on-selection warm races
        # the synchronous extraction at apply time — both could target the same
        # dest).
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".ff-", suffix=".png")
    except OSError:
        return None  # unwritable cache dir / full disk → hard-cut fallback
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-frames:v", "1", "-update", "1", str(tmp),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_EXTRACT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None
    try:
        tmp.replace(dest)  # atomic; a concurrent extraction just lands the same bytes
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    return dest


def _cache_path(video: Path) -> Path:
    stat = video.stat()
    key = f"{video.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return cache_dir() / _FRAME_DIR_NAME / f"{digest}.png"
