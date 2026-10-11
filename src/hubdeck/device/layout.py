"""Physical layout of each module's controls, and how mounting it rotated
changes what "control 1" and "up" mean.

Config numbers always follow reading order (left to right, then top to
bottom) in the module's actual orientation. Sliding/rolling controls
increase towards up, or right when the module is mounted sideways.

Positions are (x, y) in screen coordinates (y grows downwards) of each
control, by the module's own 0-based index, in its default orientation
(rotate: 0). See docs/protocol/ for how each was established.
"""

from __future__ import annotations

from functools import lru_cache

NATIVE: dict[str, list[tuple[float, float]]] = {
    "keys": [(c, r) for r in range(3) for c in range(5)],   # 5 x 3, index = row * 5 + column
    "dial": [(0, 0)],
    "knobs": [(0, 1), (2, 1), (1, 0)],      # index 0 bottom-left, 1 bottom-right, 2 top
    "rollers": [(1, 0), (0, 0)],            # index 0 right, 1 left
    "faders": [(i, 0) for i in range(5)],   # left to right, value grows upwards
    "base": [(0, 0)],
}


def _rotate(point: tuple[float, float], degrees: int) -> tuple[float, float]:
    x, y = point
    for _ in range((degrees // 90) % 4):   # 90 degrees clockwise on screen: left edge -> top edge
        x, y = -y, x
    return x, y


@lru_cache(maxsize=None)
def numbering(kind: str, rotate: int = 0) -> tuple[int, ...]:
    """Module index for each config number: numbering(kind, r)[number - 1] -> index."""
    pts = NATIVE[kind]
    rotated = [_rotate(p, rotate) for p in pts]
    return tuple(sorted(range(len(pts)), key=lambda i: (round(rotated[i][1], 3), round(rotated[i][0], 3))))


def index_of(kind: str, number: int, rotate: int = 0) -> int:
    """0-based module index of config number `number` (1-based)."""
    return numbering(kind, rotate)[number - 1]


def number_of(kind: str, index: int, rotate: int = 0) -> int:
    """1-based config number of the control at module index `index`."""
    return numbering(kind, rotate).index(index) + 1


def axis_sign(rotate: int) -> int:
    """+1 if the native 'up' still points up or right after rotating, else -1 (down or left)."""
    return 1 if rotate % 360 in (0, 90) else -1


def image_rotation(rotate: int) -> int:
    """Clockwise rotation to apply to pictures so they look upright on a module rotated by `rotate`."""
    return (360 - rotate) % 360
