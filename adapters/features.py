"""Face geometry, landmarks and body pose via Apple's Vision framework.

Every coordinate returned is normalized to the full image, origin
TOP-LEFT, y down -- Vision itself is bottom-left, y up, and face
landmarks are further normalized to the face box, not the image.
"""

import io
import math
import time
from dataclasses import dataclass

import Foundation
import Quartz
import Vision
from PIL import Image

_FACE_LANDMARK_REGIONS = (
    "leftEye",
    "rightEye",
    "leftPupil",
    "rightPupil",
    "outerLips",
    "innerLips",
    "nose",
    "medianLine",
    "faceContour",
    "leftEyebrow",
    "rightEyebrow",
)

_JOINT_SUFFIX = "_joint"
_LEFT_SHOULDER, _RIGHT_SHOULDER = "left_shoulder_1", "right_shoulder_1"


@dataclass(frozen=True)
class Face:
    box: tuple[float, float, float, float]
    yaw_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None
    regions: dict[str, list[tuple[float, float]]]


@dataclass(frozen=True)
class Body:
    joints: dict[str, tuple[float, float, float]]
    shoulder_angle_deg: float | None
    box: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class Features:
    faces: list[Face]
    bodies: list[Body]
    timings_ms: dict[str, float]


def _radians_to_degrees(value: object) -> float | None:
    return math.degrees(float(value)) if value is not None else None


def _box_xyxy(bounding_box) -> tuple[float, float, float, float]:
    (x0, y0), (w, h) = bounding_box
    return (x0, y0, x0 + w, y0 + h)


def _to_top_left_box(bounding_box) -> tuple[float, float, float, float]:
    (x0, y0), (w, h) = bounding_box
    return (x0, 1.0 - (y0 + h), w, h)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / (area_a + area_b - inter)


def _best_match(target_box, candidates: list) -> object | None:
    target = _box_xyxy(target_box)
    best, best_score = None, 0.0
    for candidate in candidates:
        score = _iou(target, _box_xyxy(candidate.boundingBox()))
        if score > best_score:
            best, best_score = candidate, score
    return best


def _joints_box(points: list[tuple[float, float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    return (x0, y0, max(xs) - x0, max(ys) - y0)


def _shoulder_angle(
    left: tuple[float, float, float] | None, right: tuple[float, float, float] | None
) -> float | None:
    if left is None or right is None:
        return None
    dx, dy = right[0] - left[0], right[1] - left[1]
    # Image y grows downward: positive means the right shoulder sits lower than the left.
    return math.degrees(math.atan2(dy, dx))


def _jpeg_size(jpeg: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(jpeg)).size


def _synthetic_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(128, 128, 128)).save(buffer, format="JPEG")
    return buffer.getvalue()


class FeatureExtractor:
    """Runs face rectangles, face landmarks and body pose Vision requests on a JPEG."""

    def __init__(
        self, min_confidence: float = 0.15, want_landmarks: bool = True, want_pose: bool = True
    ) -> None:
        self._min_confidence = min_confidence
        self._want_landmarks = want_landmarks
        self._want_pose = want_pose
        self.extract(_synthetic_jpeg())

    def extract(self, jpeg: bytes) -> Features:
        handler = self._make_handler(jpeg)
        image_w, image_h = _jpeg_size(jpeg)
        timings: dict[str, float] = {}

        rect_request = Vision.VNDetectFaceRectanglesRequest.alloc().init()
        rect_request.setRevision_(3)
        self._run(handler, rect_request, timings, "face_rectangles")
        rect_observations = list(rect_request.results() or [])

        landmark_observations: list = []
        if self._want_landmarks:
            landmarks_request = Vision.VNDetectFaceLandmarksRequest.alloc().init()
            self._run(handler, landmarks_request, timings, "face_landmarks")
            landmark_observations = list(landmarks_request.results() or [])

        bodies: list[Body] = []
        if self._want_pose:
            pose_request = Vision.VNDetectHumanBodyPoseRequest.alloc().init()
            self._run(handler, pose_request, timings, "body_pose")
            for observation in pose_request.results() or []:
                if float(observation.confidence()) < self._min_confidence:
                    continue
                bodies.append(self._build_body(observation, self._min_confidence))

        faces = self._build_faces(rect_observations, landmark_observations, image_w, image_h)
        return Features(faces=faces, bodies=bodies, timings_ms=timings)

    def _build_faces(
        self, rect_observations: list, landmark_observations: list, image_w: int, image_h: int
    ) -> list[Face]:
        faces = []
        for observation in rect_observations:
            score = float(observation.confidence())
            if score < self._min_confidence:
                continue
            regions: dict[str, list[tuple[float, float]]] = {}
            if landmark_observations:
                match = _best_match(observation.boundingBox(), landmark_observations)
                if match is not None:
                    regions = self._extract_regions(match, image_w, image_h)
            faces.append(
                Face(
                    box=_to_top_left_box(observation.boundingBox()),
                    yaw_deg=_radians_to_degrees(observation.yaw()),
                    pitch_deg=_radians_to_degrees(observation.pitch()),
                    roll_deg=_radians_to_degrees(observation.roll()),
                    regions=regions,
                )
            )
        return faces

    @staticmethod
    def _extract_regions(
        observation, image_w: int, image_h: int
    ) -> dict[str, list[tuple[float, float]]]:
        landmarks = observation.landmarks()
        if landmarks is None:
            return {}
        size = Quartz.CGSizeMake(image_w, image_h)
        regions = {}
        for name in _FACE_LANDMARK_REGIONS:
            region = getattr(landmarks, name)()
            if region is None:
                continue
            # pointsInImageOfSize_ returns an objc.varlist: a bare pointer to a
            # C array with no length of its own. Iterating it directly walks
            # off the end and segfaults; as_tuple(pointCount()) is the bound.
            points = region.pointsInImageOfSize_(size).as_tuple(region.pointCount())
            regions[name] = [(p.x / image_w, 1.0 - (p.y / image_h)) for p in points]
        return regions

    @staticmethod
    def _build_body(observation, min_confidence: float) -> Body:
        raw, error = observation.recognizedPointsForJointsGroupName_error_(
            Vision.VNHumanBodyPoseObservationJointsGroupNameAll, None
        )
        if error or not raw:
            return Body(joints={}, shoulder_angle_deg=None, box=None)

        joints: dict[str, tuple[float, float, float]] = {}
        for key, point in raw.items():
            name = str(key)
            if name.endswith(_JOINT_SUFFIX):
                name = name[: -len(_JOINT_SUFFIX)]
            x, y = point.location()
            joints[name] = (x, 1.0 - y, float(point.confidence()))

        confident = {n: p for n, p in joints.items() if p[2] >= min_confidence}
        box = _joints_box(list(confident.values())) if confident else None
        shoulder_angle = _shoulder_angle(confident.get(_LEFT_SHOULDER), confident.get(_RIGHT_SHOULDER))
        return Body(joints=joints, shoulder_angle_deg=shoulder_angle, box=box)

    @staticmethod
    def _make_handler(jpeg: bytes):
        nsdata = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
        return Vision.VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)

    @staticmethod
    def _run(handler, request, timings: dict[str, float], key: str) -> None:
        start = time.perf_counter()
        ok, error = handler.performRequests_error_([request], None)
        timings[key] = (time.perf_counter() - start) * 1000.0
        if not ok:
            raise RuntimeError(f"Vision request {key} failed: {error}")
