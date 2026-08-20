"""Named switch animations composed from swww's transition parameters.

swww exposes more than a transition *type*: the sweep angle, the circle's
center, the easing bezier and the wave geometry are all tunable per invocation.
The presets here compose those into animations that actually look different
from one another, so the random pool has real variety instead of nine raw
types with default parameters.

Invariant every preset must keep: the animation reaches its end state exactly
when the duration elapses. The seamless video lead-in
(`backends/wlroots.py`) holds mpvpaper paused for the full duration and
unpauses on that same beat, so an animation that finishes early would sit on a
frozen frame. That rules out swww's 'fade' (its default curve is done well
before the duration) and any easing whose curve plateaus before t=1. It is also
why `--transition-step` is left alone: a low step feathers the edge over an
unbounded number of frames, which decouples "visually finished" from the
duration.

Presets are resolved once per apply (see `resolve`), never re-rolled, so the
swww flags and the video driver's timing always describe the same animation.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TypeVar

from .base import ImageTransition

# Easing curves, as swww's `--transition-bezier` "x1,y1,x2,y2". All start at
# (0,0) and land on (1,1) at t=1 — see the duration invariant above.
_SMOOTH = "0.42,0,0.58,1"      # symmetric ease-in-out
_SNAP = "0.16,1,0.3,1"         # bursts out, long settle
_RAMP = "0.7,0,0.84,0"         # creeps, then rushes the finish
_LINEAR = "0,0,1,1"

_ORTHOGONAL = (0.0, 90.0, 180.0, 270.0)
_DIAGONAL = (45.0, 135.0, 225.0, 315.0)
_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")

Preset = Callable[[ImageTransition], ImageTransition]
_T = TypeVar("_T")

# swww's own transition types, accepted as-is for anyone who wants the raw
# behaviour or a type no preset covers.
_SWWW_TYPES = frozenset(
    {
        "none", "simple", "fade", "left", "right", "top", "bottom",
        "wipe", "wave", "grow", "center", "any", "outer",
    }
)


def _pick(values: Sequence[_T]) -> _T:
    return random.choice(values)


def _random_point() -> str:
    """A random on-screen point as swww percentage coordinates.

    Kept off the edges: a circle centered on the very border wastes most of its
    radius outside the screen, which reads as a plain wipe rather than a grow.
    """
    return f"{random.uniform(0.15, 0.85):.3f},{random.uniform(0.15, 0.85):.3f}"


PRESETS: dict[str, Preset] = {
    # --- linear sweeps ---
    "sweep": lambda t: replace(t, type="wipe", angle=_pick(_ORTHOGONAL), bezier=_SMOOTH),
    "diagonal": lambda t: replace(t, type="wipe", angle=_pick(_DIAGONAL), bezier=_SNAP),
    "drift": lambda t: replace(t, type="wipe", angle=random.uniform(0, 360), bezier=_LINEAR),
    # --- wave-edged sweeps ---
    "ripple": lambda t: replace(
        t, type="wave", angle=_pick(_ORTHOGONAL), wave="70,45", bezier=_LINEAR
    ),
    "chop": lambda t: replace(
        t, type="wave", angle=_pick(_DIAGONAL), wave="12,12", bezier=_RAMP
    ),
    # --- circular ---
    "iris": lambda t: replace(t, type="grow", pos="center", bezier=_SNAP),
    "collapse": lambda t: replace(t, type="outer", pos="center", bezier=_SMOOTH),
    "spotlight": lambda t: replace(t, type="grow", pos=_random_point(), bezier=_LINEAR),
    "corner": lambda t: replace(t, type="grow", pos=_pick(_CORNERS), bezier=_SNAP),
    "implode": lambda t: replace(t, type="outer", pos=_pick(_CORNERS), bezier=_SMOOTH),
}

# What "random" draws from. Every preset qualifies; the raw swww types stay
# reachable by name (a user pinning "wipe" gets swww's own defaults) but are
# deliberately out of the pool — they are the un-tuned versions of the above.
RANDOM_POOL = tuple(PRESETS)


def resolve(transition: ImageTransition) -> ImageTransition:
    """Expand a preset name (or 'random') into concrete swww parameters.

    A `type` that is not a preset name passes through untouched, so raw swww
    transition types ('wipe', 'grow', 'none', ...) keep working as before.
    """
    name = transition.type
    if name == "random":
        name = random.choice(RANDOM_POOL)
    preset = PRESETS.get(name)
    return preset(transition) if preset else transition


def is_known(name: str) -> bool:
    """True if `name` is something `resolve` understands (preset or swww type)."""
    return name in PRESETS or name in _SWWW_TYPES or name == "random"


# swww's own default easing, used when a transition carries no bezier of its
# own (a raw type name, or None).
SWWW_DEFAULT_BEZIER = ".54,0,.34,.99"

# How complete the animation must look before something may cover it. Circular
# transitions get a stricter bar: their progress drives a *radius*, so 98% of
# the radius still leaves ~4% of the screen — a ring at the edges — showing the
# old image, which pops. Linear sweeps map progress straight to covered area.
_COVER_THRESHOLD = 0.98
_CIRCULAR_THRESHOLD = 0.995
_CIRCULAR_TYPES = frozenset({"grow", "outer", "center", "any"})

_CURVE_SAMPLES = 512


def _bezier_point(x1: float, y1: float, x2: float, y2: float, s: float) -> tuple[float, float]:
    """Point on the cubic bezier (0,0)-(x1,y1)-(x2,y2)-(1,1) at parameter `s`."""
    inv = 1.0 - s
    a, b, c = 3.0 * inv * inv * s, 3.0 * inv * s * s, s * s * s
    return a * x1 + b * x2 + c, a * y1 + b * y2 + c


def _parse_bezier(bezier: str | None) -> tuple[float, float, float, float]:
    try:
        x1, y1, x2, y2 = (float(v) for v in (bezier or SWWW_DEFAULT_BEZIER).split(","))
    except ValueError:
        x1, y1, x2, y2 = (float(v) for v in SWWW_DEFAULT_BEZIER.split(","))
    return x1, y1, x2, y2


def completion_fraction(transition: ImageTransition) -> float:
    """Fraction of the duration after which the animation *looks* finished.

    swww spreads the switch over the full duration, but the easing decides how
    much of the movement has happened at any point: 'chop' does three quarters
    of its travel in the last fifth of the time, while 'iris' is all but done a
    third of the way in. Returns the earliest time (as a fraction of the
    duration) at which the eye can no longer tell the animation from its end
    state.

    This is what the video lead-in schedules against: mpvpaper covers the whole
    screen the moment its surface maps, so launching it before this point chops
    the animation off mid-movement and snaps to the final image. Sampled rather
    than solved — the curve is monotonic and 512 steps resolve far finer than a
    frame at any sane duration.
    """
    x1, y1, x2, y2 = _parse_bezier(transition.bezier)
    target = (
        _CIRCULAR_THRESHOLD
        if transition.type in _CIRCULAR_TYPES
        else _COVER_THRESHOLD
    )
    for step in range(_CURVE_SAMPLES + 1):
        x, y = _bezier_point(x1, y1, x2, y2, step / _CURVE_SAMPLES)
        if y >= target:
            return min(max(x, 0.0), 1.0)
    return 1.0
