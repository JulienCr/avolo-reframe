"""Tests for scripts.run: the 1080p-scaled ease reference distance, and the
camera-item selection that make_run_lsa's multi-camera topology depends on.
"""

import dataclasses

import pytest

from adapters.control import ControlState
from adapters.obsws import SceneRef
from core.geometry import Rect
from core.policy import PolicyParams, REFERENCE_SOURCE_H, initial_state
from scripts.layout import AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, CAM_NAME
from scripts.run import (
    REFERENCE_DISTANCE_PX,
    SceneNotReady,
    Topology,
    apply_source_resize,
    build_apply_fns,
    cam_items_match,
    reapply_state,
    scaled_duration_ms,
    select_camera_items,
    should_wait_for_first_frame,
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
        p, control, animator, {"sceneName": "scene"}, 3, 4, 5, 3840, 2160
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


def test_should_wait_for_first_frame_avocam_placeholder_while_loop_holds_4k():
    assert should_wait_for_first_frame(AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, (3840, 2160)) is True


def test_should_wait_for_first_frame_avocam_placeholder_while_loop_also_holds_placeholder():
    assert should_wait_for_first_frame(AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, AVOCAM_PLACEHOLDER_SIZE) is False


def test_should_wait_for_first_frame_non_avocam_source_at_placeholder_size():
    assert should_wait_for_first_frame("dshow_input", AVOCAM_PLACEHOLDER_SIZE, (3840, 2160)) is False


def test_should_wait_for_first_frame_avocam_already_at_4k():
    assert should_wait_for_first_frame(AVOCAM_KIND, (3840, 2160), (3840, 2160)) is False


# select_camera_items fixture: 9 items shaped like the LSA Vertical Scene --
# 3 cameras (distinct sourceUuid) x (full item, cell top, cell bottom), no
# control item (control_w=None), mirroring scripts.layout_lsa's geometry.
_CAM_UUIDS = ("uuid-main", "uuid-cour", "uuid-jardin")
_FULL_BOUNDS = (1080.0, 1920.0)
_CELL_BOUNDS = (1080.0, 960.0)


def _lsa_scene() -> tuple[list[dict], dict[int, dict]]:
    specs = []
    next_id = 1
    for uuid in _CAM_UUIDS:
        specs.append((uuid, next_id, _FULL_BOUNDS, 0.0))
        specs.append((uuid, next_id + 1, _CELL_BOUNDS, 0.0))
        specs.append((uuid, next_id + 2, _CELL_BOUNDS, 960.0))
        next_id += 3
    items = [{"sceneItemId": item_id, "sourceUuid": uuid} for uuid, item_id, _, _ in specs]
    transforms = {
        item_id: {"boundsWidth": w, "boundsHeight": h, "positionY": y} for _, item_id, (w, h), y in specs
    }
    return items, transforms


def _lsa_topo(source_uuid: str) -> Topology:
    return Topology(
        name=source_uuid,
        ref={"sceneUuid": "vertical-scene-uuid"},
        image_source_name=f"--- CAM {source_uuid}",
        image_source_uuid=source_uuid,
        source_uuid=source_uuid,
        full_bounds=_FULL_BOUNDS,
        cell_bounds=_CELL_BOUNDS,
        control_w=None,
    )


def test_select_camera_items_filters_by_source_uuid_only():
    # The silent, new-in-this-change failure mode: without uuid filtering,
    # cropping a neighboring camera's tile would raise nothing.
    items, transforms = _lsa_scene()

    control_id, full_id, cell_top_id, cell_bottom_id = select_camera_items(items, transforms, _lsa_topo("uuid-cour"))

    assert (control_id, full_id, cell_top_id, cell_bottom_id) == (None, 4, 5, 6)


def test_select_camera_items_orders_cells_by_position_y():
    items, transforms = _lsa_scene()

    _, _, cell_top_id, cell_bottom_id = select_camera_items(items, transforms, _lsa_topo("uuid-jardin"))

    assert (cell_top_id, cell_bottom_id) == (8, 9)
    assert transforms[cell_top_id]["positionY"] < transforms[cell_bottom_id]["positionY"]


def test_select_camera_items_raises_on_incomplete_camera():
    items, transforms = _lsa_scene()
    items = [i for i in items if not (i["sourceUuid"] == "uuid-main" and i["sceneItemId"] == 3)]

    with pytest.raises(SceneNotReady):
        select_camera_items(items, transforms, _lsa_topo("uuid-main"))


def test_apply_fns_requests_carry_scene_uuid_and_no_scene_name():
    ref: SceneRef = {"sceneUuid": "vertical-scene-uuid"}
    single_fn, split_fn = build_apply_fns(ref, 4, 5, 6, 1080, 1920)
    rect = Rect(0.0, 0.0, 1080.0, 1920.0)

    requests = single_fn((rect,)) + split_fn((rect, rect))

    assert requests
    for _, payload in requests:
        assert payload.get("sceneUuid") == "vertical-scene-uuid"
        assert "sceneName" not in payload
