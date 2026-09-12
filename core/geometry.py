"""Pure geometry primitives for the framing core.

Coordinates are source pixels, origin top-left, y down.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    # A point of interest (e.g. eyes) in the same space as x/y, or None
    # when the source has nothing better than the box itself to offer.
    anchor: tuple[float, float] | None = None
    # Estimated skull-top y, same space as x/y; None when unknown.
    crown: float | None = None
    # Safety band above the crown (same units as y), treated as 0 if None.
    crown_margin: float | None = None
    # Head-to-torso sub-rect (x, y, w, h), same space as x/y; used only
    # by callers that choose to fit it instead of the box itself.
    bust: tuple[float, float, float, float] | None = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def union(rects: list[Rect]) -> Rect | None:
    """Smallest rect containing every rect in the list, or None if empty."""
    if not rects:
        return None
    left = min(r.x for r in rects)
    top = min(r.y for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    # A merge of several subjects has no single meaningful anchor or crown.
    anchor = rects[0].anchor if len(rects) == 1 else None
    crown = rects[0].crown if len(rects) == 1 else None
    crown_margin = rects[0].crown_margin if len(rects) == 1 else None
    return Rect(left, top, right - left, bottom - top, anchor=anchor, crown=crown, crown_margin=crown_margin)


def expand(r: Rect, margin: float) -> Rect:
    """Grow r by margin (fraction of w and h), keeping its center."""
    new_w = r.w * (1 + margin)
    new_h = r.h * (1 + margin)
    return Rect(
        r.cx - new_w / 2, r.cy - new_h / 2, new_w, new_h, anchor=r.anchor, crown=r.crown, crown_margin=r.crown_margin
    )


def fit_ratio(r: Rect, ratio: float) -> Rect:
    """Smallest rect of aspect w/h == ratio containing r, same center."""
    h = max(r.h, r.w / ratio)
    w = h * ratio
    return Rect(r.cx - w / 2, r.cy - h / 2, w, h, anchor=r.anchor, crown=r.crown, crown_margin=r.crown_margin)


def clamp_to_source(
    r: Rect, source_w: int, source_h: int, ratio: float, min_h: float, eye_line: float = 0.5
) -> Rect:
    """Clamp r inside the source while preserving its aspect ratio.

    Precedence, highest first: the ratio, then fitting inside the source,
    then the min_h quality floor. A source too narrow for ratio * min_h
    therefore yields a height below min_h rather than overflowing.

    When r carries an anchor, the crop is positioned so the anchor sits at
    eye_line of the crop height from the top; with no anchor, r centers.
    A crown (plus its margin) then wins over both: the top edge is pulled
    up to contain it, and only the source's own edge may still clip it.
    """
    h = max(min(r.h, source_h), min(min_h, source_h))
    w = h * ratio
    if w > source_w:
        w = source_w
        h = w / ratio
    x = max(0.0, min(r.cx - w / 2, source_w - w))
    if r.anchor is not None:
        y = r.anchor[1] - eye_line * h
    else:
        y = r.cy - h / 2
    if r.crown is not None:
        y = min(y, r.crown - (r.crown_margin or 0.0))
    y = max(0.0, min(y, source_h - h))
    return Rect(x, y, w, h, anchor=r.anchor, crown=r.crown, crown_margin=r.crown_margin)


def _fit_pair(a: int, b: int, total: int) -> tuple[int, int]:
    # Shrinks a then b so a + b < total, never going negative. Needed
    # because rounding or an unclamped input rect can push a + b >= total.
    a = max(0, a)
    b = max(0, b)
    excess = a + b - (total - 1)
    if excess > 0:
        take_a = min(a, excess)
        a -= take_a
        b = max(0, b - (excess - take_a))
    return a, b


def to_crop(r: Rect, source_w: int, source_h: int) -> tuple[int, int, int, int]:
    """OBS scene-item crop values: (left, top, right, bottom)."""
    left, right = _fit_pair(round(r.x), round(source_w - r.right), source_w)
    top, bottom = _fit_pair(round(r.y), round(source_h - r.bottom), source_h)
    return left, top, right, bottom


def lerp_rect(a: Rect, b: Rect, t: float) -> Rect:
    """Linear interpolation between a and b, t clamped to [0, 1]."""
    t = max(0.0, min(1.0, t))
    return Rect(
        a.x + (b.x - a.x) * t,
        a.y + (b.y - a.y) * t,
        a.w + (b.w - a.w) * t,
        a.h + (b.h - a.h) * t,
    )


def ease_in_out(t: float) -> float:
    """Cosine ease: ease_in_out(0) == 0, ease_in_out(1) == 1."""
    return (1 - math.cos(math.pi * t)) / 2


def default_rect(source_w: int, source_h: int, ratio: float) -> Rect:
    """Full source height, horizontally centered, at the given ratio."""
    h = float(source_h)
    w = h * ratio
    if w > source_w:
        w = float(source_w)
        h = w / ratio
    return Rect((source_w - w) / 2, (source_h - h) / 2, w, h)
