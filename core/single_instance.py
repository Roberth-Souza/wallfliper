"""Single-instance guard with toggle-to-close.

Wallfliper is launcher-shaped: bound to a hotkey, the first press should open it
and a second press (while it's still up) should close it — never stack two
overlays fighting for the keyboard (the same focus problem a second layer-shell
window would cause). We enforce one live GUI by holding an exclusive advisory
lock (`flock`) on a lock file for the whole process lifetime. A second launch
can't take the lock, reads the running PID from the file, sends it SIGTERM to
toggle it closed, and exits. The kernel drops a `flock` when its owner dies, so
a crash never leaves a stale lock behind. Linux-only (`fcntl`), matching scope.
"""

from __future__ import annotations

import fcntl
import os
import signal
from pathlib import Path


def _lock_path() -> Path:
    """Per-user runtime lock path (tmpfs when XDG_RUNTIME_DIR is set)."""
    base = (
        os.environ.get("XDG_RUNTIME_DIR")
        or os.environ.get("XDG_CACHE_HOME")
        or str(Path.home() / ".cache")
    )
    return Path(base) / "wallfliper.lock"


class SingleInstance:
    """Owns the run lock for this process; toggles an existing instance closed."""

    def __init__(self) -> None:
        self._path = _lock_path()
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Become the sole instance.

        Returns True if we took the lock (the caller should launch the GUI).
        Returns False if another instance already holds it — in which case we've
        signalled that instance to quit (the toggle) and the caller should exit.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._toggle_running(fd)
            os.close(fd)
            return False
        # We own the lock: record our PID so the next launch knows whom to signal.
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd  # held open for life — the lock releases when we exit
        return True

    @staticmethod
    def _toggle_running(fd: int) -> None:
        """Tell the instance that currently holds the lock to quit (SIGTERM)."""
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            pid = int(os.read(fd, 32).decode().strip() or 0)
        except (ValueError, OSError):
            return
        if pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # already gone; this launch will simply take the lock

    def release(self) -> None:
        """Release the lock (also happens automatically on process exit)."""
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        finally:
            self._fd = None
