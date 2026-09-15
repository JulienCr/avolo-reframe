"""Tests for pose_geometry's crown/bust/remap math -- pure joint arithmetic, no detector calls."""

import math

from adapters.pose_geometry import coco17_to_joints, crown_and_bust


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

    crown_close, _, bust_close = crown_and_bust(close, 0.15)
    crown_far, _, bust_far = crown_and_bust(far, 0.15)
    assert bust_close is not None and bust_far is not None

    headroom_close = crown_close - bust_close[1]
    headroom_far = crown_far - bust_far[1]
    assert abs(headroom_close / 0.12 - headroom_far / 0.03) < 1e-9


def _coco_keypoints(width: int, height: int) -> list[tuple[float, float, float]]:
    """A symmetric standing pose in pixel space, index order per COCO-17, with one NaN."""
    points = {
        0: (0.50, 0.20),  # nose/head
        1: (0.46, 0.18), 2: (0.54, 0.18),  # eyes
        3: (0.44, 0.19), 4: (0.56, 0.19),  # ears
        5: (0.35, 0.32), 6: (0.65, 0.32),  # shoulders
        7: (0.30, 0.45), 8: (0.70, 0.45),  # elbows
        9: (0.28, 0.55), 10: (0.72, 0.55),  # wrists
        11: (0.42, 0.60), 12: (0.58, 0.60),  # hips
        13: (0.40, 0.80), 14: (0.60, 0.80),  # knees
        15: (0.38, 0.98), 16: (0.62, 0.98),  # ankles
    }
    keypoints = []
    for index in range(17):
        x, y = points[index]
        conf = 0.9
        if index == 8:
            x, y, conf = math.nan, math.nan, math.nan
        keypoints.append((x * width, y * height, conf))
    return keypoints


def test_coco17_remap_synthesizes_neck_and_handles_nan():
    joints = coco17_to_joints(_coco_keypoints(1000, 1000), width=1000, height=1000)

    assert joints["left_shoulder_1"][0] < joints["right_shoulder_1"][0]
    assert joints["neck_1"] == (
        (joints["left_shoulder_1"][0] + joints["right_shoulder_1"][0]) / 2,
        (joints["left_shoulder_1"][1] + joints["right_shoulder_1"][1]) / 2,
        min(joints["left_shoulder_1"][2], joints["right_shoulder_1"][2]),
    )
    assert joints["right_forearm"] == (0.0, 0.0, 0.0)

    crown, _, _ = crown_and_bust(joints, 0.15)
    assert crown is not None and crown < joints["left_eye"][1]
