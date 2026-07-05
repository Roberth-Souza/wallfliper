"""Persistent config and last-wallpaper state.

Two small JSON files under the XDG config dir:
  - config.json : user settings (the wallpaper directory).
  - state.json  : the last applied wallpaper, used by `--restore` on login.

Kept deliberately tiny; no schema framework, no migrations yet.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

WallpaperKind = Literal["image", "video"]

_APP = "wallfliper"

# Persisted settings keys read back in load_config (kept in one place so the
# loader and the dataclass can't drift). Unknown keys in an existing
# config.json (e.g. the retired background_opacity) are silently ignored.
_CONFIG_KEYS = ("wallpaper_dir", "color_hook")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _APP


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / _APP


def _default_wallpaper_dir() -> Path | None:
    """Best-effort guess: the XDG Pictures dir, or a Wallpapers folder in it."""
    pictures = os.environ.get("XDG_PICTURES_DIR")
    candidates = [Path(pictures)] if pictures else []
    candidates += [Path.home() / "Pictures", Path.home() / "Imagens"]
    for base in candidates:
        wp = base / "Wallpapers"
        if wp.is_dir():
            return wp
        if base.is_dir():
            return base
    return None


@dataclass
class Config:
    wallpaper_dir: str | None = None
    # Optional shell command run after a wallpaper is applied, so external
    # theming tools can recolor from it. `{path}` is replaced with the
    # wallpaper path. Empty = auto: notify noctalia-shell if it's running
    # (it derives its color scheme from the wallpaper but doesn't watch swww).
    color_hook: str = ""

    @property
    def wallpaper_path(self) -> Path | None:
        return Path(self.wallpaper_dir) if self.wallpaper_dir else None


@dataclass
class State:
    path: str | None = None
    kind: WallpaperKind | None = None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic on the same filesystem


def load_config() -> Config:
    data = _read_json(config_dir() / "config.json")
    cfg = Config(**{k: data[k] for k in _CONFIG_KEYS if k in data})
    if cfg.wallpaper_dir is None:
        guess = _default_wallpaper_dir()
        cfg.wallpaper_dir = str(guess) if guess else None
    return cfg


def save_config(cfg: Config) -> None:
    _write_json(config_dir() / "config.json", asdict(cfg))


def load_state() -> State:
    data = _read_json(config_dir() / "state.json")
    return State(**{k: data[k] for k in ("path", "kind") if k in data})


def save_state(path: Path, kind: WallpaperKind) -> None:
    _write_json(config_dir() / "state.json", {"path": str(path), "kind": kind})
