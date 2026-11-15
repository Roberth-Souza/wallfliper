"""Subject silhouettes for the `silhouette` switch animation.

The animation draws the outline of the new wallpaper's subject before growing
the picture out of it, which needs a segmentation model. That part is optional
and heavy (onnxruntime, two ~170 MB models, over 1 GB of RAM while it runs), so
it never enters the selector's process: `SilhouetteLoader` runs
`silhouette_worker.py` as a child, feeds it one image at a time and lets it exit
as soon as there is nothing left to do.

Results are field textures cached under ~/.cache/wallfliper/silhouettes/, keyed
like every other cache (abspath+mtime+size), so each wallpaper is segmented
once. An image with no subject is cached too, as a 1x1 marker, so it is not
tried again; `cached_field` reports those as missing and the caller falls back
to another animation.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess

from . import cachegc
from .state import cache_dir, data_dir

_FIELD_DIR_NAME = "silhouettes"
# Bump when the worker's field layout changes, so stale textures simply miss.
_FIELD_VERSION = "1"
# A real field is tens of kilobytes; the "no subject" marker is a 1x1 PNG.
_MARKER_MAX_BYTES = 512
_CAP_BYTES = 100 * 1024 * 1024
_WORKER = Path(__file__).with_name("silhouette_worker.py")
# A worker that keeps dying (broken onnxruntime, no memory) is not restarted
# forever.
_MAX_CRASHES = 2


def available() -> bool:
    """True if the interpreter running the app can run the segmentation worker."""
    return all(
        importlib.util.find_spec(name) is not None for name in ("onnxruntime", "numpy")
    )


def models_dir() -> Path:
    return data_dir() / "models"


def cache_path(source: Path) -> Path:
    stat = source.stat()
    key = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{_FIELD_VERSION}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return cache_dir() / _FIELD_DIR_NAME / f"{digest}.png"


def cached_field(source: Path) -> Path | None:
    """The field texture for `source` if one is cached and it has a subject."""
    try:
        field = cache_path(source)
        return field if field.stat().st_size > _MARKER_MAX_BYTES else None
    except OSError:
        return None


def prune_fields(images: list[Path]) -> None:
    """Drop orphaned fields (blocking — call from a pool thread)."""
    cachegc.prune(cache_dir() / _FIELD_DIR_NAME, images, cache_path, _CAP_BYTES)


class SilhouetteLoader(QObject):
    """Segments wallpapers in the background, in the order the caller wants them."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: list[Path] = []
        self._settled: set[str] = set()  # cached or failed: nothing left to do
        self._busy: str | None = None
        self._process: QProcess | None = None
        # Input was closed and the worker is on its way out; new work waits for
        # it to be gone and then starts a fresh one.
        self._closing = False
        self._crashes = 0
        self._disabled = not available()

    def want(self, sources: list[Path]) -> None:
        """Replace the work list; `sources` is in priority order.

        The image being segmented right now is finished either way. An empty
        list lets the worker exit, releasing the model's memory.
        """
        if self._disabled:
            return
        self._queue = list(sources)
        self._pump()

    def _pump(self) -> None:
        if self._busy is not None or self._closing:
            return
        while self._queue:
            source = self._queue.pop(0)
            key = str(source)
            if key in self._settled:
                continue
            try:
                dest = cache_path(source)
            except OSError:
                self._settled.add(key)
                continue
            if dest.exists():
                self._settled.add(key)
                continue
            self._send(source, dest)
            return
        if self._process is not None:
            self._closing = True
            self._process.closeWriteChannel()  # end of input: the worker exits

    def _send(self, source: Path, dest: Path) -> None:
        process = self._process
        if process is None:
            process = QProcess(self)
            process.setProgram(sys.executable)
            process.setArguments([str(_WORKER), "--models", str(models_dir())])
            process.setProcessChannelMode(
                QProcess.ProcessChannelMode.ForwardedErrorChannel
            )
            process.readyReadStandardOutput.connect(self._on_output)
            process.finished.connect(self._on_finished)
            process.errorOccurred.connect(self._on_error)
            self._process = process
            process.start()
        self._busy = str(source)
        process.write(f"{source}\t{dest}\n".encode())

    def _on_output(self) -> None:
        process = self._process
        if process is None:
            return
        while process.canReadLine():
            line = (
                bytes(process.readLine().data()).decode(errors="replace").rstrip("\n")
            )
            status, _, subject = line.partition("\t")
            if status == "model":
                if subject == "failed":
                    self._disabled = True
                continue
            if subject == self._busy:
                self._settled.add(subject)  # a failure is not retried this session
                self._busy = None
        self._pump()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._disabled = True
            self._drop_process()

    def _on_finished(self) -> None:
        interrupted = self._busy is not None
        self._drop_process()
        if interrupted:
            self._crashes += 1
            if self._crashes >= _MAX_CRASHES:
                self._disabled = True
        if not self._disabled:
            self._pump()

    def _drop_process(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
            self._process = None
        self._busy = None
        self._closing = False
        if self._disabled:
            self._queue.clear()

    def stop(self) -> None:
        """Kill the worker now (application exit)."""
        self._queue.clear()
        process, self._process = self._process, None
        self._busy = None
        self._closing = False
        if process is not None:
            process.finished.disconnect(self._on_finished)
            process.kill()
            process.waitForFinished(1000)
