"""Pose box/crown/bust geometry, shared by every detector (Vision, YOLO).

Standard library only: this is core-adjacent logic, kept portable so a
non-Apple detector produces the same Box shape by the same rules.
"""

import math
from dataclasses import dataclass

from adapters.detector import Box

JointName = str
Joint = tuple[float, float, float]  # x, y (top-left origin), confidence

BUST_JOINTS = frozenset(
    {
        "head",
        "neck_1",
        "left_shoulder_1",
        "right_shoulder_1",
        "left_forearm",
        "right_forearm",
        "left_upLeg",
        "right_upLeg",
    }
)

# The head joint sits below the eyes (measured on real poses at roughly
# eye_y + 0.11 normalized); the crown is placed one eye-to-neck distance up.
_CROWN_FACTOR = 1.0
# Extra room above the crown, as a fraction of the eye-to-neck face scale.
_BUST_HEADROOM = 0.5
# Safety band so a small drift right at the crown does not re-trigger a
# forced re-commit every other frame; a fraction of the face scale.
_CROWN_MARGIN_FACTOR = 0.3
# Side padding on the bust, as a fraction of the shoulder-to-shoulder span.
_BUST_SIDE_MARGIN = 0.15

# COCO-17 index -> Vision joint name. Wrists/elbows/ankles/knees keep
# Vision's forearm/hand/leg/foot naming so downstream code (overlay, bust
# rules) never has to know which detector produced a Pose.
_COCO_TO_VISION = {
    0: "head",
    1: "left_eye",
    2: "right_eye",
    3: "left_ear",
    4: "right_ear",
    5: "left_shoulder_1",
    6: "right_shoulder_1",
    7: "left_forearm",
    8: "right_forearm",
    9: "left_hand",
    10: "right_hand",
    11: "left_upLeg",
    12: "right_upLeg",
    13: "left_leg",
    14: "right_leg",
    15: "left_foot",
    16: "right_foot",
}


@dataclass(frozen=True)
class Pose:
    """One detected body: bounding box, all recognized joints, and shoulder tilt."""

    box: Box
    joints: dict[JointName, Joint]
    shoulder_angle_deg: float | None


def coco17_to_joints(keypoints: list[tuple[float, float, float]], width: int, height: int) -> dict[JointName, Joint]:
    """Map 17 COCO keypoints (pixels, top-left origin) to Vision joint names.

    A non-finite x, y or confidence yields (0, 0, 0.0) for that point, so a
    detector's NaN never propagates into the box/crown math below.
    neck_1 and root are synthesized as shoulder/hip midpoints.
    """
    joints: dict[JointName, Joint] = {}
    for index, name in _COCO_TO_VISION.items():
        x, y, conf = keypoints[index]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(conf)):
            joints[name] = (0.0, 0.0, 0.0)
            continue
        joints[name] = (min(1.0, max(0.0, x / width)), min(1.0, max(0.0, y / height)), float(conf))

    joints["neck_1"] = _midpoint(joints["left_shoulder_1"], joints["right_shoulder_1"])
    joints["root"] = _midpoint(joints["left_upLeg"], joints["right_upLeg"])
    return joints


def _midpoint(a: Joint, b: Joint) -> Joint:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, min(a[2], b[2]))


def detect_poses(all_joints: list[dict[JointName, Joint]], min_confidence: float, bust: bool) -> list[Pose]:
    """Turn per-body joint dicts into Poses: same box/crown/bust rules for every detector."""
    poses = []
    for joints in all_joints:
        if not joints:
            continue
        candidates = joints if not bust else {n: v for n, v in joints.items() if n in BUST_JOINTS}
        confident = {n: v for n, v in candidates.items() if v[2] >= min_confidence}
        if not confident:
            continue
        crown, crown_margin, bust_rect = crown_and_bust(joints, min_confidence)
        poses.append(
            Pose(
                box=box(confident, anchor(joints, min_confidence), crown, crown_margin, bust_rect),
                joints=joints,
                shoulder_angle_deg=shoulder_angle(joints, min_confidence),
            )
        )
    return poses


def anchor(joints: dict[JointName, Joint], min_confidence: float) -> tuple[float, float] | None:
    """Eye midpoint when both eyes are confident, else head, else neck_1."""
    left_eye, right_eye = joints.get("left_eye"), joints.get("right_eye")
    if left_eye and right_eye and left_eye[2] >= min_confidence and right_eye[2] >= min_confidence:
        return (left_eye[0] + right_eye[0]) / 2, (left_eye[1] + right_eye[1]) / 2
    for name in ("head", "neck_1"):
        joint = joints.get(name)
        if joint and joint[2] >= min_confidence:
            return joint[0], joint[1]
    return None


def crown_and_bust(
    joints: dict[JointName, Joint], min_confidence: float
) -> tuple[float | None, float | None, tuple[float, float, float, float] | None]:
    """Skull-top estimate (plus its safety margin) and head-to-torso
    sub-rect, both keyed off the face scale since the head joint is
    not the top of the skull.
    """
    left_eye, right_eye, neck = joints.get("left_eye"), joints.get("right_eye"), joints.get("neck_1")
    eyes_ok = bool(left_eye and right_eye and neck) and min(left_eye[2], right_eye[2], neck[2]) >= min_confidence
    crown = crown_margin = face_scale = None
    if eyes_ok:
        eye_y = (left_eye[1] + right_eye[1]) / 2
        face_scale = neck[1] - eye_y
        crown = eye_y - _CROWN_FACTOR * face_scale
        crown_margin = _CROWN_MARGIN_FACTOR * face_scale

    left_sh, right_sh = joints.get("left_shoulder_1"), joints.get("right_shoulder_1")
    left_hip, right_hip = joints.get("left_upLeg"), joints.get("right_upLeg")
    shoulders_ok = bool(left_sh and right_sh) and min(left_sh[2], right_sh[2]) >= min_confidence
    bust_rect = None
    if crown is not None and shoulders_ok and left_hip and right_hip:
        shoulder_y = (left_sh[1] + right_sh[1]) / 2
        hip_y = (left_hip[1] + right_hip[1]) / 2
        top = crown - _BUST_HEADROOM * face_scale
        bottom = (shoulder_y + hip_y) / 2
        span = abs(right_sh[0] - left_sh[0])
        left = min(left_sh[0], right_sh[0]) - _BUST_SIDE_MARGIN * span
        right = max(left_sh[0], right_sh[0]) + _BUST_SIDE_MARGIN * span
        bust_rect = (left, top, right - left, bottom - top)

    return crown, crown_margin, bust_rect


def box(
    joints: dict[JointName, Joint],
    anchor_point: tuple[float, float] | None,
    crown: float | None,
    crown_margin: float | None,
    bust_rect: tuple[float, float, float, float] | None,
) -> Box:
    xs = [v[0] for v in joints.values()]
    ys = [v[1] for v in joints.values()]
    scores = [v[2] for v in joints.values()]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return Box(
        x=x0,
        y=y0,
        w=x1 - x0,
        h=y1 - y0,
        score=sum(scores) / len(scores),
        anchor=anchor_point,
        crown=crown,
        crown_margin=crown_margin,
        bust=bust_rect,
    )


def shoulder_angle(joints: dict[JointName, Joint], min_confidence: float) -> float | None:
    left = joints.get("left_shoulder_1")
    right = joints.get("right_shoulder_1")
    if not left or not right:
        return None
    if left[2] < min_confidence or right[2] < min_confidence:
        return None
    angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))
    # A line has no direction: facing the camera, right.x < left.x wraps the raw
    # angle near +-180 degrees at rest, so fold it into (-90, 90] to read near 0.
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180
    return angle
