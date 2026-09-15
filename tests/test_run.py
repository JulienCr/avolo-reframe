"""Tests for the 1080p-scaled ease reference distance in scripts.run."""

import dataclasses

from adapters.control import ControlState
from core.policy import PolicyParams, REFERENCE_SOURCE_H, initial_state
from scripts.layout import CAM_NAME
from scripts.run import (
    REFERENCE_DISTANCE_PX,
    apply_source_resize,
    cam_items_match,
    reapply_state,
    scaled_duration_ms,
)


def test_scaled_duration_unchanged_at_1080p():
    duration = scaled_duration_ms(REFERENCE_DISTANCE_PX, 320.0, 0.0, 10000.0, REFERENCE_SOURCE_H)
    assert abs(duration - 320.0) < 1e-9


def test_scaled_duration_reference_distance_doubles_at_4k():
    # At 4K the reference distance doubles, so the 1080p reference distance
    # now covers half the (scaled) reference, halving the duration.
    duration = scaled_duration_ms(REFERENCE_DISTANCE_PX, 320.0, 0.0, 10000.0, 2 * REFERENCE_SOURCE_H)
    assert abs(duration - 160.0) < 1e-9


class _FakeAnimator:
    def __init__(self):
        self.calls = []

    def jump(self, to, apply_fn):
        self.calls.append((to, apply_fn))


def test_reapply_state_single_mode_jumps_to_current():
    animator = _FakeAnimator()
    state = initial_state(PolicyParams())
    single_fn, split_fn = object(), object()

    reapply_state(animator, state, single_fn, split_fn)

    assert animator.calls == [((state.current,), single_fn)]


def test_reapply_state_split_mode_jumps_to_cells():
    animator = _FakeAnimator()
    base = initial_state(PolicyParams())
    cells = (base.current, base.current)
    state = dataclasses.replace(base, mode="split", cells=cells)
    single_fn, split_fn = object(), object()

    reapply_state(animator, state, single_fn, split_fn)

    assert animator.calls == [(cells, split_fn)]


def test_apply_source_resize_keeps_dock_tuning_and_syncs_control():
    p = PolicyParams(margin=0.3)
    control = ControlState(params=p, detector_name="x", fps=12.0, upper_body=False)
    animator = _FakeAnimator()

    new_p, state, single_fn, split_fn = apply_source_resize(
        p, control, animator, "scene", 3, 4, 5, 3840, 2160
    )

    assert (new_p.source_w, new_p.source_h) == (3840, 2160)
    control_p = control.get_controls()[0]
    assert (control_p.source_w, control_p.source_h) == (3840, 2160)
    assert new_p.margin == 0.3
    assert control_p.margin == 0.3
    assert state == initial_state(new_p)
    assert animator.calls == [((state.current,), single_fn)]


def _scene_items(uuid: str) -> list[dict]:
    return [{"sceneItemId": i, "sourceName": CAM_NAME, "sourceUuid": uuid} for i in (1, 2, 3, 4)]


def test_cam_items_match_same_ids_names_and_uuid():
    items = _scene_items("uuid-old")
    assert cam_items_match(items, (1, 2, 3, 4), "uuid-old") is True


def test_cam_items_match_rejects_rebuilt_source_with_new_uuid():
    # A rebuild reuses the same scene item ids and the same CAM_NAME: only the
    # uuid of the underlying input changes, which is what must be caught here.
    items = _scene_items("uuid-new")
    assert cam_items_match(items, (1, 2, 3, 4), "uuid-old") is False
