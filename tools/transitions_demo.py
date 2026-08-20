"""Cycle every switch animation on the real wallpaper, one at a time.

Throwaway dev tool for eyeballing the presets in `core/backends/transitions.py`
without opening the GUI (the overlay grabs the keyboard exclusively, which makes
it a bad harness for watching an animation).

    python tools/transitions_demo.py            # all presets, 1s each
    python tools/transitions_demo.py iris chop  # only these
    python tools/transitions_demo.py --duration 2.5 --hold 4

It applies actual wallpapers, alternating between two images from the configured
wallpaper directory, and restores whatever was set before on exit (including
Ctrl-C).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backends import get_backend
from core.backends import transitions as tr
from core.backends.base import ImageTransition
from core.library import scan
from core.state import load_config, load_state


def _images(limit: int = 2) -> list[Path]:
    cfg = load_config()
    if cfg.wallpaper_path is None:
        sys.exit("no wallpaper_dir configured")
    found = [e.path for e in scan(cfg.wallpaper_path) if e.kind == "image"]
    if len(found) < 2:
        sys.exit(f"need at least 2 images in {cfg.wallpaper_path}, found {len(found)}")
    return found[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("presets", nargs="*", help="preset names (default: all)")
    ap.add_argument("--duration", type=float, default=1.0, help="animation seconds")
    ap.add_argument("--hold", type=float, default=2.0, help="pause between presets")
    ap.add_argument("--fps", type=int, default=60)
    args = ap.parse_args()

    names = args.presets or list(tr.PRESETS)
    unknown = [n for n in names if not tr.is_known(n)]
    if unknown:
        sys.exit(f"unknown: {', '.join(unknown)}\nknown: {', '.join(tr.PRESETS)}")

    backend = get_backend()
    images = _images()
    previous = load_state()

    try:
        for i, name in enumerate(names):
            print(f"→ {name}", flush=True)
            backend.set_image(
                images[i % len(images)],
                ImageTransition(type=name, duration=args.duration, fps=args.fps),
            )
            time.sleep(args.duration + args.hold)
    except KeyboardInterrupt:
        print()
    finally:
        if previous.path and previous.kind == "image":
            print(f"restoring {previous.path}")
            backend.set_image(Path(previous.path), ImageTransition(type="none"))
        elif previous.path:
            print("previous wallpaper was a video; restore it with: wallfliper --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
