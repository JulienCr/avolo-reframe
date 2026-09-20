"""Tests for scripts.run: the 1080p-scaled ease reference distance, and the
camera-item selection that make_run_lsa's multi-camera topology depends on.
"""

import dataclasses

import pytest

from adapters.control import ControlState
from adapters.obsws import SceneRef
from core.geometry import Rect
from core.policy import Command, PolicyParams, REFERENCE_SOURCE_H, initial_state
from scripts.layout import AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, CAM_NAME
from scripts.run import (
    REFERENCE_DISTANCE_PX,
    SceneNotReady,
    Topology,
    apply_source_resize,
    build_apply_fns,
    cam_items_match,
    emit_command,
    make_disable_apply_fn,
    make_single_apply_fn,
    make_split_apply_fn,
    make_visibility_fn,
    reapply_state,
    scaled_duration_ms,
    select_camera_items,
    should_wait_for_first_frame,
    visibility_prelude,
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

    def jump(self, to, apply_fn, prelude=None):
        self.calls.append(("jump", to, apply_fn, prelude))

    def play(self, frm, to, duration_ms, apply_fn, prelude=None):
        self.calls.append(("play", to, apply_fn, prelude))


def test_reapply_state_single_mode_jumps_to_current():
    animator = _FakeAnimator()
    state = initial_state(PolicyParams())
    single_fn, split_fn = object(), object()
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 10, 11, 12)

    reapply_state(animator, state, single_fn, split_fn, visibility_fn)

    assert animator.calls == [("jump", (state.current,), single_fn, visibility_fn("single"))]


def test_reapply_state_split_mode_jumps_to_cells():
    animator = _FakeAnimator()
    base = initial_state(PolicyParams())
    cells = (base.current, base.current)
    state = dataclasses.replace(base, mode="split", cells=cells)
    single_fn, split_fn = object(), object()
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 10, 11, 12)

    reapply_state(animator, state, single_fn, split_fn, visibility_fn)

    assert animator.calls == [("jump", cells, split_fn, visibility_fn("split"))]


def test_reapply_state_always_resends_regardless_of_last_applied_mode():
    # Unlike emit_command, a rebuild leaves OBS's actual item visibility
    # unknown, so reapply_state must not skip the write even when its
    # tracked mode already matches -- there is nothing to trust it against.
    animator = _FakeAnimator()
    state = initial_state(PolicyParams())
    single_fn, split_fn = object(), object()
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 10, 11, 12)

    reapply_state(animator, state, single_fn, split_fn, visibility_fn)

    assert animator.calls[0][3] is not None


def test_apply_source_resize_keeps_dock_tuning_and_syncs_control():
    p = PolicyParams(margin=0.3)
    control = ControlState(params=p, detector_name="x", fps=12.0, upper_body=False)
    animator = _FakeAnimator()

    new_p, state, single_fn, split_fn, disable_fn, visibility_fn, mode = apply_source_resize(
        p, control, animator, True, {"sceneName": "scene"}, 3, 4, 5, 3840, 2160, None
    )

    assert (new_p.source_w, new_p.source_h) == (3840, 2160)
    control_p = control.get_controls()[0]
    assert (control_p.source_w, control_p.source_h) == (3840, 2160)
    assert new_p.margin == 0.3
    assert control_p.margin == 0.3
    assert state == initial_state(new_p)
    assert animator.calls == [("jump", (state.current,), single_fn, visibility_fn("single"))]
    assert mode == "single"


def test_apply_source_resize_skips_visibility_when_already_single():
    p = PolicyParams(margin=0.3)
    animator = _FakeAnimator()

    *_rest, mode = apply_source_resize(
        p, None, animator, True, {"sceneName": "scene"}, 3, 4, 5, 3840, 2160, "single"
    )

    assert animator.calls[0][3] is None
    assert mode == "single"


def test_apply_source_resize_off_air_rebuilds_without_applying():
    # Regression for the P1 where a resize while merely observing could take
    # the canvas: off air, the callbacks are rebuilt but never handed to the
    # animator, so nothing reaches OBS or re-enables the output item.
    p = PolicyParams(margin=0.3)
    animator = _FakeAnimator()

    new_p, state, single_fn, split_fn, disable_fn, visibility_fn, mode = apply_source_resize(
        p, None, animator, False, {"sceneName": "scene"}, 3, 4, 5, 3840, 2160, None
    )

    assert (new_p.source_w, new_p.source_h) == (3840, 2160)
    assert animator.calls == []
    assert mode is None


def test_make_disable_apply_fn_disables_owned_items_only():
    # Regression for the P1 race with the animator's 60Hz thread: the disable
    # must name exactly output/cell_top/cell_bottom (never a control item),
    # so routing it through animator.jump() can't re-enable the wrong thing.
    apply_fn = make_disable_apply_fn({"sceneName": "scene"}, 10, 11, 12)

    requests = apply_fn((Rect(0.0, 0.0, 1.0, 1.0),))

    assert requests == [
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 10, "sceneItemEnabled": False}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 11, "sceneItemEnabled": False}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 12, "sceneItemEnabled": False}),
    ]


def _scene_items(uuid: str) -> list[dict]:
    return [{"sceneItemId": i, "sourceName": CAM_NAME, "sourceUuid": uuid} for i in (1, 2, 3, 4)]


def test_cam_items_match_same_ids_names_and_uuid():
    items = _scene_items("uuid-old")
    expected = {i: "uuid-old" for i in (1, 2, 3, 4)}
    assert cam_items_match(items, expected) is True


def test_cam_items_match_rejects_rebuilt_source_with_new_uuid():
    # A rebuild reuses the same scene item ids and the same CAM_NAME: only the
    # uuid of the underlying input changes, which is what must be caught here.
    items = _scene_items("uuid-new")
    expected = {i: "uuid-old" for i in (1, 2, 3, 4)}
    assert cam_items_match(items, expected) is False


def test_should_wait_for_first_frame_avocam_placeholder_while_loop_holds_4k():
    assert should_wait_for_first_frame(AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, (3840, 2160)) is True


def test_should_wait_for_first_frame_avocam_placeholder_while_loop_also_holds_placeholder():
    assert should_wait_for_first_frame(AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, AVOCAM_PLACEHOLDER_SIZE) is False


def test_should_wait_for_first_frame_non_avocam_source_at_placeholder_size():
    assert should_wait_for_first_frame("dshow_input", AVOCAM_PLACEHOLDER_SIZE, (3840, 2160)) is False


def test_should_wait_for_first_frame_avocam_already_at_4k():
    assert should_wait_for_first_frame(AVOCAM_KIND, (3840, 2160), (3840, 2160)) is False


# select_camera_items fixture: 9 items shaped like the LSA Vertical Scene --
# 3 cameras x (plain clone, split-top clone, split-bottom clone). No shared
# uuid and no bounds signature: each item's sourceName is its own clone name.
_CAM_LABELS = ("Main", "Cour", "Jardin")
_FULL_BOUNDS = (1080.0, 1920.0)
_CELL_BOUNDS = (1080.0, 960.0)


def _clone_names(label: str) -> tuple[str, str, str]:
    return f"Cam {label} - plain", f"Cam {label} - Split ↑", f"Cam {label} - Split ↓"


def _lsa_scene() -> list[dict]:
    items = []
    next_id = 1
    for label in _CAM_LABELS:
        for name in _clone_names(label):
            items.append({"sceneItemId": next_id, "sourceName": name})
            next_id += 1
    return items


def _lsa_topo(label: str) -> Topology:
    full_name, top_name, bottom_name = _clone_names(label)
    return Topology(
        name=label,
        ref={"sceneUuid": "vertical-scene-uuid"},
        image_source_name=f"--- CAM {label}",
        image_source_uuid=f"uuid-{label}",
        source_uuid=f"uuid-{label}",
        full_bounds=_FULL_BOUNDS,
        cell_bounds=_CELL_BOUNDS,
        control_w=None,
        full_name=full_name,
        cell_top_name=top_name,
        cell_bottom_name=bottom_name,
    )


def test_select_camera_items_filters_by_clone_name_only():
    # The silent, new-in-this-change failure mode: without exact-name
    # filtering, cropping a neighboring camera's tile would raise nothing.
    items = _lsa_scene()

    control_id, full_id, cell_top_id, cell_bottom_id = select_camera_items(items, {}, _lsa_topo("Cour"))

    assert (control_id, full_id, cell_top_id, cell_bottom_id) == (None, 4, 5, 6)


def test_select_camera_items_maps_split_roles_by_name_not_position():
    # Roles come from the clone name alone now, not from a positionY sort:
    # transforms is empty here and the mapping still lands on the right ids.
    items = _lsa_scene()

    _, _, cell_top_id, cell_bottom_id = select_camera_items(items, {}, _lsa_topo("Jardin"))

    assert (cell_top_id, cell_bottom_id) == (8, 9)


def test_select_camera_items_raises_on_incomplete_camera():
    items = _lsa_scene()
    items = [i for i in items if i["sourceName"] != "Cam Main - Split ↓"]

    with pytest.raises(SceneNotReady):
        select_camera_items(items, {}, _lsa_topo("Main"))


def test_apply_fns_requests_carry_scene_uuid_and_no_scene_name():
    ref: SceneRef = {"sceneUuid": "vertical-scene-uuid"}
    single_fn, split_fn, disable_fn, visibility_fn = build_apply_fns(ref, 4, 5, 6, 1080, 1920)
    rect = Rect(0.0, 0.0, 1080.0, 1920.0)

    requests = (
        single_fn((rect,)) + split_fn((rect, rect)) + disable_fn((rect,))
        + visibility_fn("single") + visibility_fn("split")
    )

    assert requests
    for _, payload in requests:
        assert payload.get("sceneUuid") == "vertical-scene-uuid"
        assert "sceneName" not in payload


def test_single_and_split_apply_fns_carry_crop_only_never_visibility():
    # The regression this change fixes: apply_fn is ticked at 60Hz for the
    # whole ease_ms of a transition, so visibility must never ride in it --
    # only VisibilityFn's one-shot prelude may emit SetSceneItemEnabled.
    ref: SceneRef = {"sceneName": "scene"}
    single_fn = make_single_apply_fn(ref, 4, 1080, 1920)
    split_fn = make_split_apply_fn(ref, 5, 6, 1080, 1920)
    rect = Rect(0.0, 0.0, 1080.0, 1920.0)

    single_requests = single_fn((rect,))
    split_requests = split_fn((rect, rect))

    assert [name for name, _ in single_requests] == ["SetSceneItemTransform"]
    assert [name for name, _ in split_requests] == ["SetSceneItemTransform", "SetSceneItemTransform"]


def test_visibility_fn_single_enables_output_and_disables_cells():
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)

    assert visibility_fn("single") == [
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 4, "sceneItemEnabled": True}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 5, "sceneItemEnabled": False}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 6, "sceneItemEnabled": False}),
    ]


def test_visibility_fn_split_disables_output_and_enables_cells():
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)

    assert visibility_fn("split") == [
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 4, "sceneItemEnabled": False}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 5, "sceneItemEnabled": True}),
        ("SetSceneItemEnabled", {"sceneName": "scene", "sceneItemId": 6, "sceneItemEnabled": True}),
    ]


def test_visibility_prelude_none_when_mode_already_applied():
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)

    assert visibility_prelude(visibility_fn, "single", "single") is None


def test_visibility_prelude_patches_on_mode_change():
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)

    assert visibility_prelude(visibility_fn, "split", "single") == visibility_fn("split")


def test_visibility_prelude_patches_when_no_mode_applied_yet():
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)

    assert visibility_prelude(visibility_fn, "single", None) == visibility_fn("single")


def test_emit_command_skips_visibility_prelude_within_same_mode():
    # A tracking move inside one mode must not resend the visibility
    # triplet: it was already applied before this command.
    animator = _FakeAnimator()
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)
    target = Rect(0.0, 0.0, 100.0, 100.0)
    frm = Rect(10.0, 10.0, 100.0, 100.0)
    cmd = Command(target=target, frm=frm, duration_ms=300.0, reason="track")
    single_fn, split_fn = object(), object()

    new_mode = emit_command(animator, cmd, single_fn, split_fn, visibility_fn, "single", 180.0, 900.0, 1920)

    assert new_mode == "single"
    assert animator.calls == [("play", (target,), single_fn, None)]


def test_emit_command_sends_visibility_prelude_on_mode_change():
    animator = _FakeAnimator()
    visibility_fn = make_visibility_fn({"sceneName": "scene"}, 4, 5, 6)
    target = Rect(0.0, 0.0, 100.0, 100.0)
    cmd = Command(target=target, frm=None, duration_ms=0.0, reason="split")
    single_fn, split_fn = object(), object()

    new_mode = emit_command(animator, cmd, single_fn, split_fn, visibility_fn, "split", 180.0, 900.0, 1920)

    assert new_mode == "single"
    assert animator.calls == [("jump", (target,), single_fn, visibility_fn("single"))]
