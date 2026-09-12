"""Shared types for body detectors."""

from dataclasses import dataclass
from typing import Protocol

from core.geometry import Rect


@dataclass(frozen=True)
class Box:
    """Normalized body box, origin TOP-LEFT, y down, all values in [0, 1]."""

    x: float
    y: float
    w: float
    h: float
    score: float
    # A point of interest (e.g. eyes), same normalized space as x/y, or
    # None when the detector has nothing better to offer than the box.
    anchor: tuple[float, float] | None = None
    # Estimated skull-top y, same normalized space; None when unknown.
    crown: float | None = None
    # Safety band above the crown (same normalized units), 0 if None.
    crown_margin: float | None = None
    # Head-to-torso sub-rect (x, y, w, h), same normalized space as the
    # box, for a caller that wants a bust instead of the full body.
    bust: tuple[float, float, float, float] | None = None


def to_source_rect(box: Box, source_w: int, source_h: int) -> Rect:
    """Scale a normalized Box into source pixels, optional fields included.

    Written once because it was written twice: two call sites drifted and
    silently dropped anchor/crown/bust, making a whole framing fix inert.
    """
    return Rect(
        box.x * source_w,
        box.y * source_h,
        box.w * source_w,
        box.h * source_h,
        anchor=None if box.anchor is None else (box.anchor[0] * source_w, box.anchor[1] * source_h),
        crown=None if box.crown is None else box.crown * source_h,
        crown_margin=None if box.crown_margin is None else box.crown_margin * source_h,
        bust=None if box.bust is None else (
            box.bust[0] * source_w, box.bust[1] * source_h,
            box.bust[2] * source_w, box.bust[3] * source_h,
        ),
    )


class Detector(Protocol):
    name: str

    def detect(self, jpeg: bytes) -> list[Box]: ...
