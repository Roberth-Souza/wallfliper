"""Runtime dependency check.

Wallfliper shells out to external tools (swww/awww, mpvpaper, ffmpeg) and needs
the layer-shell-qt QML module to render its overlay. When one is missing the
failure would otherwise be a raw Python/Qt traceback. This module reports what's
present in friendly terms so `--check` and the GUI launch can tell the user
exactly what to install.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass

# Default system QML path (Arch/distro Qt). main.py prepends this for pip-Qt too.
_SYSTEM_QT_QML = "/usr/lib/qt6/qml"
_LAYERSHELL_MARKER = os.path.join("org", "kde", "layershell", "qmldir")
_MIN_PYTHON = (3, 11)


def pyside6_present() -> bool:
    """True if PySide6 is importable, without importing Qt itself."""
    return importlib.util.find_spec("PySide6") is not None


def _qml_import_paths() -> list[str]:
    """Directories where the org.kde.layershell QML module may live.

    Env overrides + the Arch default, plus — when PySide6 is importable — the
    QML imports path Qt itself reports. That last entry is what makes detection
    correct off Arch: distro PySide6 elsewhere (e.g. Debian's
    /usr/lib/x86_64-linux-gnu/qt6/qml) and pip's bundled Qt both differ from
    /usr/lib/qt6/qml, and only Qt knows where it actually looks.
    """
    paths = [
        os.environ.get("QML2_IMPORT_PATH", ""),
        os.environ.get("QML_IMPORT_PATH", ""),
        _SYSTEM_QT_QML,
    ]
    try:
        from PySide6.QtCore import QLibraryInfo

        paths.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
    except Exception:
        pass  # PySide6 missing/unloadable — the static paths still apply
    return paths


@dataclass(frozen=True)
class DepResult:
    """One dependency's status. `required` ones make the app non-functional."""

    name: str
    ok: bool
    required: bool
    hint: str


def _layershell_present() -> bool:
    """True if the org.kde.layershell QML module is on any QML import path."""
    seen: set[str] = set()
    for raw in _qml_import_paths():
        for base in raw.split(os.pathsep):
            if not base or base in seen:
                continue
            seen.add(base)
            if os.path.exists(os.path.join(base, _LAYERSHELL_MARKER)):
                return True
    return False


def check() -> list[DepResult]:
    """Inspect the environment and return the status of every dependency."""
    image_tool = shutil.which("swww") or shutil.which("awww")
    return [
        DepResult(
            "python >= 3.11",
            sys.version_info >= _MIN_PYTHON,
            required=True,
            hint=(
                f"Wallfliper needs Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ "
                f"(running {sys.version_info.major}.{sys.version_info.minor})"
            ),
        ),
        DepResult(
            "PySide6",
            pyside6_present(),
            required=True,
            hint="install your distro's 'pyside6' package (the Qt6/QML runtime) — not pip; see README",
        ),
        DepResult(
            "wayland session",
            os.environ.get("WAYLAND_DISPLAY") is not None,
            required=True,
            hint="run inside a Wayland compositor (Hyprland/Sway/river/niri/Wayfire)",
        ),
        DepResult(
            "layer-shell-qt",
            _layershell_present(),
            required=True,
            hint="install 'layer-shell-qt' (provides the org.kde.layershell QML module)",
        ),
        DepResult(
            "swww (or awww)",
            image_tool is not None,
            required=False,
            hint="install 'swww' (or its fork 'awww') for image wallpapers",
        ),
        DepResult(
            "mpvpaper",
            shutil.which("mpvpaper") is not None,
            required=False,
            hint="install 'mpvpaper' for video wallpapers",
        ),
        DepResult(
            "ffmpeg",
            shutil.which("ffmpeg") is not None,
            required=False,
            hint="install 'ffmpeg' for video thumbnails and previews",
        ),
    ]


def format_report(results: list[DepResult]) -> str:
    """Render results as flat monochrome terminal lines (matches the app style)."""
    lines = []
    for r in results:
        if r.ok:
            lines.append(f"  ✓ {r.name}")
        else:
            tag = "" if r.required else " (optional)"
            lines.append(f"  ✗ {r.name}{tag}   → {r.hint}")
    return "\n".join(lines)


def missing(results: list[DepResult] | None = None) -> list[DepResult]:
    """The subset of dependencies that are not satisfied."""
    return [r for r in (results or check()) if not r.ok]


def all_required_ok(results: list[DepResult] | None = None) -> bool:
    """True when every *required* dependency is present."""
    return all(r.ok for r in (results or check()) if r.required)
