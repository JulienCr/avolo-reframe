"""Tests for PoseDetector's crown/bust math -- pure joint arithmetic, no Vision calls."""

from adapters.detect_pose import PoseDetector


def _pose_joints(eye_y: float, face_scale: float) -> dict:
    """A confident, symmetric pose at the given eye height and face scale."""
    neck_y = eye_y + face_scale
    shoulder_y = neck_y + 0.03
    hip_y = shoulder_y + 0.30
    return {
        "left_eye": (0.45, eye_y, 1.0),
        "right_eye": (0.55, eye_y, 1.0),
        "neck_1": (0.5, neck_y, 1.0),
        "left_shoulder_1": (0.35, shoulder_y, 1.0),
        "right_shoulder_1": (0.65, shoulder_y, 1.0),
        "left_upLeg": (0.42, hip_y, 1.0),
        "right_upLeg": (0.58, hip_y, 1.0),
    }


def test_headroom_scales_with_the_subject():
    close = _pose_joints(eye_y=0.20, face_scale=0.12)
    far = _pose_joints(eye_y=0.45, face_scale=0.03)  # same subject, farther away

    crown_close, _, bust_close = PoseDetector._crown_and_bust(close, 0.15)
    crown_far, _, bust_far = PoseDetector._crown_and_bust(far, 0.15)
    assert bust_close is not None and bust_far is not None

    headroom_close = crown_close - bust_close[1]
    headroom_far = crown_far - bust_far[1]
    assert abs(headroom_close / 0.12 - headroom_far / 0.03) < 1e-9
