"""Body-pose detection via Apple's Vision framework (macOS only)."""

import io
import math
from dataclasses import dataclass

import Foundation
import Vision
from PIL import Image

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


@dataclass(frozen=True)
class Pose:
    """One detected body: bounding box, all recognized joints, and shoulder tilt."""

    box: Box
    joints: dict[JointName, Joint]
    shoulder_angle_deg: float | None


def _warmup_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, format="JPEG")
    return buf.getvalue()


class PoseDetector:
    """Detects body poses using VNDetectHumanBodyPoseRequest (19 joints per body)."""

    def __init__(self, min_confidence: float = 0.15, bust: bool = False) -> None:
        self._min_confidence = min_confidence
        self._bust = bust
        self.name = "pose-bust" if bust else "pose"
        # First call in the process loads the model (~7s); pay it here, not on frame one.
        self._run(_warmup_jpeg())

    def detect(self, jpeg: bytes) -> list[Box]:
        return [pose.box for pose in self.detect_poses(jpeg)]

    def detect_poses(self, jpeg: bytes) -> list[Pose]:
        poses = []
        for observation in self._run(jpeg):
            joints = self._joints(observation)
            if not joints:
                continue
            candidates = joints if not self._bust else {n: v for n, v in joints.items() if n in BUST_JOINTS}
            confident = {n: v for n, v in candidates.items() if v[2] >= self._min_confidence}
            if not confident:
                continue
            crown, crown_margin, bust_rect = self._crown_and_bust(joints, self._min_confidence)
            poses.append(
                Pose(
                    box=self._box(confident, self._anchor(joints), crown, crown_margin, bust_rect),
                    joints=joints,
                    shoulder_angle_deg=self._shoulder_angle(joints),
                )
            )
        return poses

    def _anchor(self, joints: dict[JointName, Joint]) -> tuple[float, float] | None:
        """Eye midpoint when both eyes are confident, else head, else neck_1."""
        left_eye, right_eye = joints.get("left_eye"), joints.get("right_eye")
        if left_eye and right_eye and left_eye[2] >= self._min_confidence and right_eye[2] >= self._min_confidence:
            return (left_eye[0] + right_eye[0]) / 2, (left_eye[1] + right_eye[1]) / 2
        for name in ("head", "neck_1"):
            joint = joints.get(name)
            if joint and joint[2] >= self._min_confidence:
                return joint[0], joint[1]
        return None

    @staticmethod
    def _crown_and_bust(
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

    def _run(self, jpeg: bytes) -> list:
        request = Vision.VNDetectHumanBodyPoseRequest.alloc().init()
        nsdata = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision request failed: {error}")
        return request.results() or []

    def _joints(self, observation) -> dict[JointName, Joint]:
        # The group name must be the named constant: the literal string 'all' returns
        # None with no error, a silent failure.
        points, _ = observation.recognizedPointsForJointsGroupName_error_(
            Vision.VNHumanBodyPoseObservationJointsGroupNameAll, None
        )
        joints = {}
        for key, point in (points or {}).items():
            name = str(key).removesuffix("_joint")
            x, y = point.location()
            # Vision is bottom-left origin, y up; ours is top-left, y down.
            joints[name] = (min(1.0, max(0.0, x)), min(1.0, max(0.0, 1.0 - y)), float(point.confidence()))
        return joints

    @staticmethod
    def _box(
        joints: dict[JointName, Joint],
        anchor: tuple[float, float] | None,
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
            anchor=anchor,
            crown=crown,
            crown_margin=crown_margin,
            bust=bust_rect,
        )

    def _shoulder_angle(self, joints: dict[JointName, Joint]) -> float | None:
        left = joints.get("left_shoulder_1")
        right = joints.get("right_shoulder_1")
        if not left or not right:
            return None
        if left[2] < self._min_confidence or right[2] < self._min_confidence:
            return None
        angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))
        # A line has no direction: facing the camera, right.x < left.x wraps the raw
        # angle near +-180 degrees at rest, so fold it into (-90, 90] to read near 0.
        if angle > 90:
            angle -= 180
        elif angle <= -90:
            angle += 180
        return angle
