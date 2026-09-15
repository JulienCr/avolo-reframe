"""Shared types for body detectors."""

import sys
from dataclasses import dataclass
from pathlib import Path
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


def build_detector(name: str, upper_body: bool, yolo_model: str = "models/yolo11m-pose.pt") -> "Detector":
    """One factory for scripts.run and scripts.corpus.

    Imports are local to each branch so importing this module never pulls
    in pyobjc (macOS-only) or torch (Windows/Linux-only) on the wrong OS.
    """
    if name in ("pose", "vision"):
        if sys.platform != "darwin":
            print(
                f"Apple Vision n'existe que sur macOS : « --detector {name} » est indisponible sur cette machine. "
                "Utilisez --detector yolo."
            )
            sys.exit(1)
        if name == "pose":
            from adapters.detect_pose import PoseDetector

            return PoseDetector(bust=upper_body)
        from adapters.detect_vision import VisionDetector

        return VisionDetector(upper_body=upper_body)

    model_path = Path(yolo_model)
    if not model_path.is_file():
        print(f"Modèle YOLO introuvable : {model_path}. Téléchargez-le avec « make model MODEL={model_path.as_posix()} ».")
        sys.exit(1)

    from adapters.detect_yolo import YoloDetector

    return YoloDetector(str(model_path), bust=upper_body)
