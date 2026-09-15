"""Body-pose detection via Apple's Vision framework (macOS only)."""

import io

import Foundation
import Vision
from PIL import Image

from adapters.detector import Box
from adapters.pose_geometry import Joint, JointName, Pose, detect_poses


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
        all_joints = [self._joints(observation) for observation in self._run(jpeg)]
        return detect_poses(all_joints, self._min_confidence, self._bust)

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
