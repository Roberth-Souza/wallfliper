"""Registry of the GLSL transitions the app paints itself.

Some switch animations cannot be expressed as swww flags at all — swww's
transition types are compiled into its daemon. Those are drawn by wallfliper on
its own layer-shell surface (`ui/qml/TransitionSurface.qml`) with a fragment
shader from `ui/qml/shaders/`.

Qt 6 cannot compile GLSL at runtime, so what ships is the baked `.qsb` next to
each `.frag` (see `tools/build_shaders.sh`). A name only counts as available
when its baked file is actually present: a missing or unbaked shader degrades to
the swww transitions instead of failing the apply, like every other optional
piece in the app.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl

SHADER_DIR = Path(__file__).resolve().parent / "qml" / "shaders"
_SUFFIX = ".frag.qsb"


def path_for(name: str) -> Path | None:
    """The baked shader for `name`, or None if there isn't one on disk."""
    if not name or "/" in name or name.startswith("."):
        return None  # never let a config value walk out of the shader dir
    baked = SHADER_DIR / f"{name}{_SUFFIX}"
    return baked if baked.is_file() else None


def url_for(name: str) -> str | None:
    """The baked shader as a URL for QML's `fragmentShader`, or None."""
    baked = path_for(name)
    return QUrl.fromLocalFile(str(baked)).toString() if baked else None


def names() -> tuple[str, ...]:
    """Every shader transition available in this install, sorted."""
    if not SHADER_DIR.is_dir():
        return ()
    return tuple(
        sorted(f.name[: -len(_SUFFIX)] for f in SHADER_DIR.glob(f"*{_SUFFIX}"))
    )


def is_shader(name: str) -> bool:
    """True if `name` is a shader transition that can actually be drawn."""
    return path_for(name) is not None
