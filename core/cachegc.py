"""Garbage collection for the content-addressed thumbnail/preview/first-frame caches.

Cache files are named sha1(abspath|mtime|size|dims), which keeps them correct
(an edited source or a new target size simply misses) but makes every
superseded file an orphan nothing will ever read again. The hash can't be
reversed, so each cache dir keeps a manifest (`index.json`) mapping digest →
source path; with it, a file can be checked against its source:

  - in the current library          → keep (never evicted)
  - no manifest record              → delete (pre-manifest leftover)
  - source deleted or changed       → delete
  - source in another intact folder → keep, so switching wallpaper folders
                                      back and forth doesn't regenerate all
  - source folder unreachable       → keep (likely an unmounted drive)

Entries outside the current library are then evicted least-recently-seen first
while the dir exceeds its byte cap. Runs once per library reload on a pool
thread; best-effort throughout — a failure leaves the cache as it was.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

CachePath = Callable[[Path], Path]

_MANIFEST = "index.json"
_STALE_TMP_S = 3600  # an atomic-write tmp this old is abandoned
# Only names the cache writers produce are ever deleted, so a misconfigured
# cache dir that overlaps user files can't cost a wallpaper.
_OWNED = re.compile(r"[0-9a-f]{40}\.(?:jpg|webp|png)")
_OWNED_TMP = re.compile(r"[0-9a-f]{40}\.tmp\.(?:jpg|webp|png)|\.ff-[a-z0-9_]+\.png")


def prune(
    directory: Path,
    library: Iterable[Path],
    cache_path: CachePath,
    cap_bytes: int,
) -> None:
    """Delete orphaned files in `directory` and cap what other folders keep.

    `library` is the current folder's sources; `cache_path` maps a source to
    its cache file (and raises OSError when the source is gone).
    """
    if not directory.is_dir():
        return
    now = time.time()
    manifest = _load(directory / _MANIFEST)

    live: set[str] = set()
    for src in library:
        try:
            digest = cache_path(src).name.split(".", 1)[0]
        except OSError:
            continue
        manifest[digest] = {"src": str(src), "seen": now}
        live.add(digest)

    live_bytes = 0
    others: list[tuple[float, int, Path, str]] = []  # (seen, size, file, digest)
    for file in directory.iterdir():
        is_tmp = _OWNED_TMP.fullmatch(file.name) is not None
        if not (is_tmp or _OWNED.fullmatch(file.name)) or not file.is_file():
            continue
        try:
            stat = file.stat()
        except OSError:
            continue
        if is_tmp:
            if now - stat.st_mtime > _STALE_TMP_S:
                _unlink(file)
            continue
        digest = file.name.split(".", 1)[0]
        if digest in live:
            live_bytes += stat.st_size
            continue
        record = manifest.get(digest)
        if record is None or not _still_valid(record, digest, cache_path):
            _unlink(file)
            continue
        others.append((record["seen"], stat.st_size, file, digest))

    total = live_bytes + sum(size for _, size, _, _ in others)
    others.sort(key=lambda item: item[0])
    kept: set[str] = set(live)
    for _, size, file, digest in others:
        if total > cap_bytes:
            _unlink(file)
            total -= size
        else:
            kept.add(digest)

    _save(directory / _MANIFEST, {d: manifest[d] for d in kept})


def _still_valid(record: dict, digest: str, cache_path: CachePath) -> bool:
    src = Path(record["src"])
    try:
        return cache_path(src).name.split(".", 1)[0] == digest
    except OSError:
        # Gone from a folder that is still there → deleted; a missing folder
        # is more likely an unmounted drive, left to the cap to bound.
        return not src.parent.is_dir()


def _load(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        digest: {"src": record["src"], "seen": float(record["seen"])}
        for digest, record in data.items()
        if isinstance(record, dict)
        and isinstance(record.get("src"), str)
        and isinstance(record.get("seen"), (int, float))
    }


def _save(path: Path, manifest: dict[str, dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest))
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _unlink(file: Path) -> None:
    try:
        file.unlink(missing_ok=True)
    except OSError:
        pass
