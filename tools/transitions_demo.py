"""Cycle every switch animation on the real wallpaper, one at a time.

Throwaway dev tool for eyeballing the presets in `core/backends/transitions.py`
without opening the GUI (the overlay grabs the keyboard exclusively, which makes
it a bad harness for watching an animation).

    python tools/transitions_demo.py            # all presets, 1s each
    python tools/transitions_demo.py iris chop  # only these
    python tools/transitions_demo.py --duration 2.5 --hold 4
    python tools/transitions_demo.py --video       # image -> video lead-in

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
from core.firstframe import first_frame
from core.library import scan
from core.state import load_config, load_state
from ui import shaders


def _library(kind: str) -> list[Path]:
    cfg = load_config()
    if cfg.wallpaper_path is None:
        sys.exit("no wallpaper_dir configured")
    return [e.path for e in scan(cfg.wallpaper_path) if e.kind == kind]


def _images(limit: int = 2) -> list[Path]:
    found = _library("image")
    if len(found) < 2:
        sys.exit(f"need at least 2 images in the wallpaper dir, found {len(found)}")
    return found[:limit]


def _video() -> Path:
    found = _library("video")
    if not found:
        sys.exit("no video wallpaper in the wallpaper dir")
    clip = found[0]
    # The backend only fakes a video transition when the first frame is already
    # cached (it never runs ffmpeg on the apply path); warm it here or every
    # preset would silently degrade to a hard cut.
    if first_frame(clip) is None:
        sys.exit(f"could not extract the first frame of {clip.name} (ffmpeg missing?)")
    return clip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("presets", nargs="*", help="preset names (default: all)")
    ap.add_argument("--duration", type=float, default=1.0, help="animation seconds")
    ap.add_argument("--hold", type=float, default=2.0, help="pause between presets")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument(
        "--video",
        action="store_true",
        help="test the image->video lead-in instead of image->image",
    )
    args = ap.parse_args()

    names = args.presets or list(tr.PRESETS)
    # Shader transitions are painted by the GUI's own layer-shell surface, so
    # they cannot be driven from a headless script like this one.
    shader = [n for n in names if shaders.is_shader(n)]
    if shader:
        sys.exit(
            f"{', '.join(shader)}: shader transition, only runs in the GUI "
            '(set it in config.json as "transition")'
        )
    unknown = [n for n in names if not tr.is_known(n)]
    if unknown:
        sys.exit(f"unknown: {', '.join(unknown)}\nknown: {', '.join(tr.PRESETS)}")

    backend = get_backend()
    images = _images()
    clip = _video() if args.video else None
    previous = load_state()

    try:
        for i, name in enumerate(names):
            print(f"→ {name}", flush=True)
            transition = ImageTransition(
                type=name, duration=args.duration, fps=args.fps
            )
            if clip is None:
                backend.set_image(images[i % len(images)], transition)
            else:
                # Reset to a still first (instantly, so it isn't mistaken for
                # the animation under test), then run the lead-in into the clip.
                backend.set_image(images[i % len(images)], ImageTransition(type="none"))
                time.sleep(1.0)
                backend.set_video(clip, transition)
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
